"""End-to-end MRZ reading benchmark.

Legacy set: samples/generated (original generator, Consolas MRZ font never used in training).
Synthetic set: freshly rendered documents of every MRZ-bearing type captured under random
conditions, using both training fonts and the held-out fonts.

    python -m evaluation.eval_mrz --legacy --synthetic 300
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import time
from datetime import date
from pathlib import Path

import cv2

from src.mrz_reader import read_mrz

ROOT = Path(__file__).resolve().parents[1]


def eval_legacy(out: dict):
    man = json.loads((ROOT / 'samples' / 'manifest.json').read_text())
    by = collections.defaultdict(lambda: [0, 0])
    rows, t_total = [], 0.0
    for s in man['samples']:
        if s['family'] != 'identity':
            continue
        img = cv2.imread(str(ROOT / s['file']))
        t0 = time.perf_counter()
        r = read_mrz(img)
        dt = time.perf_counter() - t0
        t_total += dt
        m = r.mrz
        got = dict(passport_number=m.document_number, date_of_birth=m.date_of_birth, date_of_expiry=m.date_of_expiry,
                   nationality=m.nationality)
        ok = all(got.get(k) == v for k, v in s['expected'].items()) and m.overall_check_digit_valid
        cond = ('tampered_' if s['tampered'] else '') + s['capture_condition']
        by[cond][0] += ok
        by[cond][1] += 1
        rows.append(dict(file=s['file'], ok=ok, secs=round(dt, 2), lines=r.raw_lines, repairs=m.repairs))
    readable = [r for r in rows if 'partial' not in r['file']]
    out['legacy'] = dict(
        all=f"{sum(r['ok'] for r in rows)}/{len(rows)}",
        excluding_mrz_covered=f"{sum(r['ok'] for r in readable)}/{len(readable)}",
        rate_excluding_covered=round(sum(r['ok'] for r in readable) / len(readable), 4),
        mean_secs=round(t_total / len(rows), 3),
        by_condition={k: f'{a}/{b}' for k, (a, b) in sorted(by.items())},
        failures=[r for r in rows if not r['ok']][:40])


def eval_synthetic(out: dict, n: int, seed: int = 777):
    from synthdocs import render_document, simulate_capture
    from synthdocs import fonts
    rng = random.Random(seed)
    holdout = fonts.available(fonts.MRZ_HOLDOUT)
    stats = collections.defaultdict(lambda: [0, 0])
    t_total = 0.0
    fails = []
    for i in range(n):
        dt = rng.choice(['passport', 'visa', 'national_id', 'residence_permit'])
        font_group = 'holdout_font' if holdout and i % 2 else 'train_font'
        font = rng.choice(holdout) if font_group == 'holdout_font' else None
        doc = render_document(dt, seed=rng.randrange(1 << 30), today=date(2026, 9, 16), mrz_font=font)
        severity = rng.choice([0, 1, 2, 3])
        img, _, _, meta = simulate_capture(doc.image, random.Random(rng.randrange(1 << 30)), severity=severity)
        t0 = time.perf_counter()
        r = read_mrz(img)
        t_total += time.perf_counter() - t0
        ok = r.mrz.valid_format and r.mrz.normalized_lines == doc.mrz_lines
        for key in (font_group, f'severity_{severity}', dt, 'all'):
            stats[key][0] += ok
            stats[key][1] += 1
        if not ok and len(fails) < 30:
            fails.append(dict(doc_type=dt, font=font_group, conditions=meta['conditions'], expected=doc.mrz_lines,
                              got=r.raw_lines, valid=r.mrz.overall_check_digit_valid))
    out['synthetic'] = dict(exact_mrz={k: f'{a}/{b} ({a / b:.1%})' for k, (a, b) in sorted(stats.items())},
                            mean_secs=round(t_total / n, 3), failures=fails)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--legacy', action='store_true')
    ap.add_argument('--synthetic', type=int, default=0)
    ap.add_argument('--out', default=str(ROOT / 'outputs' / 'eval_mrz.json'))
    a = ap.parse_args()
    out: dict = {}
    if a.legacy:
        eval_legacy(out)
        print(json.dumps({k: v for k, v in out['legacy'].items() if k != 'failures'}, indent=1))
    if a.synthetic:
        eval_synthetic(out, a.synthetic)
        print(json.dumps({k: v for k, v in out['synthetic'].items() if k != 'failures'}, indent=1))
    Path(a.out).parent.mkdir(exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
