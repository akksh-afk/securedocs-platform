"""System benchmark: produces outputs/benchmark_report.json and outputs/BENCHMARK.md.

    python -m evaluation.benchmark --docs 150 --tamper-test 600

All data is synthetic. The legacy set (samples/generated) comes from the original project's
generator and was never used for training; the MRZ font it uses is held out from training."""
from __future__ import annotations

import argparse
import collections
import json
import random
import tempfile
import time
from datetime import date
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 16)


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def auc(scores, labels):
    s, l = np.asarray(scores, float), np.asarray(labels, bool)
    if l.all() or (~l).all():
        return None
    order = np.argsort(np.concatenate([s[l], s[~l]]), kind='mergesort')
    ranks = np.empty(len(order)); ranks[order] = np.arange(1, len(order) + 1)
    n1, n0 = l.sum(), (~l).sum()
    return round(float((ranks[:n1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), 4)


# ---------------------------------------------------------------------------

def bench_legacy(report):
    """Original sample set: MRZ reading and forgery detection through the full pipeline."""
    from src.screening import ScreeningService
    from src.registry import Registry
    from src.audit import AuditLog
    from src.tampering import analyze_tampering
    from src.validation import validate_document
    tmp = Path(tempfile.mkdtemp())
    svc = ScreeningService(registry=Registry(tmp / 'r.sqlite'), audit=AuditLog(tmp / 'a.jsonl'))
    man = json.loads((ROOT / 'samples' / 'manifest.json').read_text())
    rows = []
    for s in man['samples']:
        if s['family'] != 'identity':
            continue
        raw = (ROOT / s['file']).read_bytes()
        t0 = time.perf_counter()
        d = svc.analyze_document(raw, today=TODAY)
        from src.screening import mrz_read_reliable
        rec = dict(document_type=d['document_type'], fields=d['fields'], mrz=d['mrz'], viz_fields=d['viz'].fields,
                   viz_confidences=d['viz'].confidences, printed_dates=d['viz'].printed_dates)
        val = validate_document(rec, today=TODAY, context=dict(mrz_read_reliable=mrz_read_reliable(d)))
        zones = dict(portrait=d['portrait_box'], mrz=d['mrz_box'], fields=d['viz'].boxes)
        tam = analyze_tampering(d['rectified'], raw_bytes=raw, zones=zones, validation=val, portrait=d['portrait'])
        dt = time.perf_counter() - t0
        m = d['mrz']
        got = dict(passport_number=m.get('document_number'), date_of_birth=m.get('date_of_birth'),
                   date_of_expiry=m.get('date_of_expiry'), nationality=m.get('nationality'))
        mrz_ok = bool(m.get('overall_check_digit_valid')) and all(got.get(k) == v for k, v in s['expected'].items())
        uc = tam['use_cases']
        rows.append(dict(file=s['file'], cond=s['capture_condition'], tampered=s['tampered'], tamper_type=s.get('tamper_type'),
                         mrz_ok=mrz_ok, secs=dt, pixel_score=tam['pixel_score'], detected=tam['tampering_detected'],
                         pixel_detected=bool(tam['pixel_score'] is not None and tam['pixel_score'] >= tam['image_threshold']),
                         cases={k: v['detected'] for k, v in uc.items()},
                         doc_type=d['document_type']))
    readable = [r for r in rows if r['cond'] != 'partial']
    tam = [r for r in rows if r['tampered']]
    gen = [r for r in rows if not r['tampered']]
    by_type = collections.defaultdict(lambda: [0, 0])
    for r in tam:
        by_type[r['tamper_type']][0] += r['detected']
        by_type[r['tamper_type']][1] += 1
    fp_by_cond = collections.defaultdict(lambda: [0, 0])
    for r in gen:
        fp_by_cond[r['cond']][0] += r['detected']
        fp_by_cond[r['cond']][1] += 1
    report['legacy'] = dict(
        samples=len(rows),
        mrz_exact_readable=f"{sum(r['mrz_ok'] for r in readable)}/{len(readable)} ({pct(sum(r['mrz_ok'] for r in readable), len(readable))}%)",
        mrz_exact_all=f"{sum(r['mrz_ok'] for r in rows)}/{len(rows)}",
        classified_as_passport=pct(sum(r['doc_type'] == 'passport' for r in readable), len(readable)),
        tamper_detection_full_pipeline=dict(recall=f"{sum(r['detected'] for r in tam)}/{len(tam)} ({pct(sum(r['detected'] for r in tam), len(tam))}%)",
                                            false_alarms=f"{sum(r['detected'] for r in gen)}/{len(gen)} ({pct(sum(r['detected'] for r in gen), len(gen))}%)",
                                            recall_by_type={k: f'{a}/{b}' for k, (a, b) in sorted(by_type.items())},
                                            false_alarms_by_condition={k: f'{a}/{b}' for k, (a, b) in sorted(fp_by_cond.items()) if a}),
        tamper_pixel_model_only=dict(auc=auc([r['pixel_score'] or 0 for r in rows], [r['tampered'] for r in rows]),
                                     recall=pct(sum(r['pixel_detected'] for r in tam), len(tam)),
                                     false_alarm_rate=pct(sum(r['pixel_detected'] for r in gen), len(gen))),
        mean_secs_per_document=round(float(np.mean([r['secs'] for r in rows])), 3))
    base = ROOT / 'outputs' / 'baseline_original_system.json'
    if base.exists():
        report['legacy']['original_system'] = json.loads(base.read_text())


def bench_synthetic_ocr(report, n):
    """Fresh documents of all five types under random capture conditions."""
    from synthdocs import render_document, simulate_capture, fonts
    from src.screening import ScreeningService
    from src.registry import Registry
    from src.audit import AuditLog
    tmp = Path(tempfile.mkdtemp())
    svc = ScreeningService(registry=Registry(tmp / 'r.sqlite'), audit=AuditLog(tmp / 'a.jsonl'))
    rng = random.Random(20260916)
    holdout = fonts.available(fonts.MRZ_HOLDOUT)
    stats = collections.defaultdict(lambda: collections.Counter())
    lat = []
    for i in range(n):
        dt = ['passport', 'visa', 'national_id', 'driving_licence', 'residence_permit'][i % 5]
        sev = rng.choice([0, 1, 2, 3])
        font = rng.choice(holdout) if holdout and i % 2 else None
        doc = render_document(dt, seed=rng.randrange(1 << 30), today=TODAY, mrz_font=font)
        img, _, _, meta = simulate_capture(doc.image, random.Random(rng.randrange(1 << 30)), severity=sev)
        raw = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        t0 = time.perf_counter()
        d = svc.analyze_document(raw, today=TODAY)
        lat.append(time.perf_counter() - t0)
        keys = (dt, f'severity_{sev}', 'all')
        for k in keys:
            stats[k]['docs'] += 1
            stats[k]['classified'] += d['document_type'] == dt
        if doc.mrz_lines:
            ok = d['mrz'].get('normalized_lines') == doc.mrz_lines
            for k in keys + (('holdout_font' if font else 'train_font'),):
                stats[k]['mrz_docs'] += 1
                stats[k]['mrz_exact'] += ok
        for f, v in doc.viz_text.items():
            if f == 'document_type_code':
                continue
            exp = doc.fields.get(f, v)
            exp = exp.isoformat() if isinstance(exp, date) else exp
            got = d['fields'].get(f)
            for k in keys:
                stats[k]['fields'] += 1
                stats[k]['fields_ok'] += str(got) == str(exp)
    out = {}
    for k, c in sorted(stats.items()):
        out[k] = dict(documents=c['docs'], classification=pct(c['classified'], c['docs']),
                      mrz_exact=pct(c['mrz_exact'], c['mrz_docs']) if c['mrz_docs'] else None,
                      field_accuracy=pct(c['fields_ok'], c['fields']) if c['fields'] else None)
    report['synthetic_capture'] = dict(results=out, latency_ms=dict(p50=round(1000 * float(np.percentile(lat, 50))),
                                                                   p95=round(1000 * float(np.percentile(lat, 95)))))


def bench_tamper_test(report, limit):
    from training.train_tamper import read_split
    from src.tampering import tamper_heatmap, pixel_model_available, _calibration
    if not pixel_model_available() or not (ROOT / 'data' / 'tamper' / 'test.jsonl').exists():
        return
    metas = read_split(ROOT / 'data' / 'tamper', 'test')[:limit]
    thr = _calibration()['image_threshold']
    scores, labels, per = [], [], collections.defaultdict(list)
    inter = union = 0
    for m in metas:
        img = cv2.imread(str(ROOT / 'data' / 'tamper' / 'test' / f"{m['name']}.jpg"))
        mask = cv2.imread(str(ROOT / 'data' / 'tamper' / 'test' / f"{m['name']}_mask.png"), 0) > 127
        prob = tamper_heatmap(img)
        s = float(cv2.blur(prob, (7, 7)).max())
        scores.append(s); labels.append(m['tampered'])
        inter += int(((prob > 0.5) & mask).sum()); union += int(((prob > 0.5) | mask).sum())
        for op in set(m['ops']):
            per[op].append(s >= thr)
    s, l = np.array(scores), np.array(labels)
    report['tamper_test_split'] = dict(documents=len(metas), auc=auc(scores, labels), threshold=round(thr, 4),
                                       recall=pct(int((s[l] >= thr).sum()), int(l.sum())),
                                       false_alarm_rate=pct(int((s[~l] >= thr).sum()), int((~l).sum())),
                                       pixel_iou=round(inter / max(union, 1), 3),
                                       recall_by_type={k: pct(sum(v), len(v)) for k, v in sorted(per.items())})


def bench_face(report):
    from src import face_verification as fv
    sample = Path(__import__('matplotlib').get_data_path()) / 'sample_data' / 'grace_hopper.jpg'
    if not (fv.models_available() and sample.exists()):
        return
    photo = cv2.imread(str(sample))
    from synthdocs import render_document
    doc = render_document('passport', seed=42, today=TODAY)
    x0, y0, x1, y1 = doc.portrait_box
    doc.image[y0:y1, x0:x1] = cv2.resize(photo[40:560, 30:480], (x1 - x0, y1 - y0))
    rng = random.Random(3)
    sims = []
    for i in range(10):
        live = cv2.convertScaleAbs(photo, alpha=rng.uniform(0.5, 1.2), beta=rng.uniform(-20, 30))
        if i % 2:
            live = cv2.flip(live, 1)
        live = cv2.GaussianBlur(live, (0, 0), rng.uniform(0.3, 1.5))
        sims.append(fv.verify_faces(doc.image, live)['similarity'])
    report['face'] = dict(genuine_pairs=len(sims), genuine_match_rate=pct(sum(s is not None and s >= 0.363 for s in sims), len(sims)),
                          genuine_similarity_min=round(min(s for s in sims if s is not None), 3),
                          note='Only one real face photo is available offline (matplotlib sample); impostor rates need a '
                               'consented face dataset. SFace published LFW accuracy: 99.40%.')


def write_markdown(report: dict) -> str:
    L = ['# Benchmark report', '', f"Generated {report['generated_at']} on {report['hardware'].get('gpu') or 'CPU'}. "
         'All documents are synthetic.', '']
    lg = report.get('legacy')
    if lg:
        o = lg.get('original_system', {})
        L += ['## Original sample set (never used for training; unseen MRZ font)', '',
              '| Metric | Original system | This system |', '|---|---|---|',
              f"| Fully correct MRZ reads (readable captures) | {o.get('mrz_exact_readable', 'n/a')} | {lg['mrz_exact_readable']} |",
              f"| Forged documents detected | {o.get('tamper_recall', 'n/a')} | {lg['tamper_detection_full_pipeline']['recall']} |",
              f"| False forgery alarms on genuine captures | {o.get('tamper_false_alarms', 'n/a')} | {lg['tamper_detection_full_pipeline']['false_alarms']} |",
              f"| Seconds per document | {o.get('mean_secs_per_document', 'n/a')} | {lg['mean_secs_per_document']} |", '',
              f"Detection by forgery type: {lg['tamper_detection_full_pipeline']['recall_by_type']}", '']
    t = report.get('tamper_test_split')
    if t:
        L += ['## Forgery localiser on held-out synthetic test documents', '',
              f"{t['documents']} documents, AUC {t['auc']}, recall {t['recall']}% at {t['false_alarm_rate']}% false alarms "
              f"(threshold {t['threshold']} calibrated on the validation split), pixel IoU {t['pixel_iou']}.", '',
              '| Forgery type | Recall % |', '|---|---|'] + [f'| {k} | {v} |' for k, v in t['recall_by_type'].items()] + ['']
    sc = report.get('synthetic_capture')
    if sc:
        L += ['## Fresh synthetic captures (all document types, random capture conditions)', '',
              f"End-to-end document analysis latency: p50 {sc['latency_ms']['p50']} ms, p95 {sc['latency_ms']['p95']} ms.", '',
              '| Slice | Documents | Type classified % | MRZ exact % | Field accuracy % |', '|---|---|---|---|---|']
        for k, v in sc['results'].items():
            L.append(f"| {k} | {v['documents']} | {v['classification']} | {v['mrz_exact']} | {v['field_accuracy']} |")
        L.append('')
    f = report.get('face')
    if f:
        L += ['## Face verification', '', f"Genuine pairs matched: {f['genuine_match_rate']}% (lowest similarity "
              f"{f['genuine_similarity_min']}, threshold 0.363). {f['note']}", '']
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--docs', type=int, default=150)
    ap.add_argument('--tamper-test', type=int, default=600)
    ap.add_argument('--skip-legacy', action='store_true')
    ap.add_argument('--update', action='store_true', help='keep sections of the existing report that are not re-run')
    a = ap.parse_args()
    existing = ROOT / 'outputs' / 'benchmark_report.json'
    report = json.loads(existing.read_text()) if a.update and existing.exists() else {}
    report.update(generated_at=time.strftime('%Y-%m-%d %H:%M'), today_for_validation=TODAY.isoformat())
    import torch
    report['hardware'] = dict(gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
    steps = [('face', lambda: bench_face(report)), ('tamper_test', lambda: bench_tamper_test(report, a.tamper_test))]
    if not a.skip_legacy:
        steps.append(('legacy', lambda: bench_legacy(report)))
    if a.docs:
        steps.append(('synthetic', lambda: bench_synthetic_ocr(report, a.docs)))
    for name, fn in steps:
        t0 = time.time()
        fn()
        print(f'{name} done in {time.time() - t0:.0f}s', flush=True)
        (ROOT / 'outputs' / 'benchmark_report.json').write_text(json.dumps(report, indent=1))
        (ROOT / 'outputs' / 'BENCHMARK.md').write_text(write_markdown(report), encoding='utf-8')
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
