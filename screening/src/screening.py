"""End-to-end screening: capture check -> OCR (MRZ + printed zone) -> validation -> face
verification -> tampering -> watchlist/identity registry -> risk -> audit trail."""
from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timezone

import cv2
import numpy as np

from . import face_verification as fv
from .audit import AuditLog, sha256_bytes
from .config import load_policy
from .document import capture_guidance, classify_document, locate_document, rectify
from .mrz_reader import model_available as mrz_model_available, read_mrz
from .registry import Registry
from .risk import assess_risk, evidence
from .tampering import analyze_tampering, pixel_model_available
from .validation import MRZ_TYPES, merge_fields, validate_document
from .viz_ocr import extract_fields

SEVERITY_ORDER = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1, 'info': 0}


def mrz_read_reliable(d: dict) -> bool:
    """A failed check digit only suggests forgery when the capture was clear and the recogniser was sure."""
    chars = [c for line in d['mrz_read'].char_confidence for c in line]
    if d['capture']['quality_score'] < 45 or not chars or float(np.percentile(chars, 5)) < 0.6:
        return False
    box = d.get('mrz_box')
    if box:
        x0, y0, x1, y1 = [max(0, int(v)) for v in box]
        g = cv2.cvtColor(d['rectified'], cv2.COLOR_BGR2GRAY)[y0:y1, x0:x1].astype(np.float32)
        if g.shape[0] >= 5 and g.shape[1] >= 5:
            # Sharp text has similar horizontal and vertical gradient energy; motion blur along the
            # line suppresses one of them and merges look-alike glyphs such as 0 and 8.
            ratio = float(np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0)).mean() / (np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1)).mean() + 1e-6))
            if ratio < 0.5 or ratio > 2.0:
                return False
    return True


def decode_image(data: bytes | np.ndarray | str) -> tuple[np.ndarray, bytes | None]:
    if isinstance(data, np.ndarray):
        return data, None
    if isinstance(data, str):
        with open(data, 'rb') as fh:
            data = fh.read()
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError('Unsupported or corrupt image')
    try:  # honour EXIF orientation from phone cameras
        from PIL import Image, ImageOps
        import io
        pil = Image.open(io.BytesIO(data))
        if pil.getexif().get(0x0112, 1) != 1:
            img = cv2.cvtColor(np.asarray(ImageOps.exif_transpose(pil).convert('RGB')), cv2.COLOR_RGB2BGR)
    except Exception:
        pass
    return img, data


def _box_from_quads(quads, H: np.ndarray | None, pad: int = 12):
    if not quads:
        return None
    pts = np.concatenate([np.asarray(q, np.float32).reshape(-1, 2) for q in quads])
    if H is not None:
        pts = cv2.perspectiveTransform(pts[None], H)[0]
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    return [int(x0) - pad, int(y0) - pad, int(x1) + pad, int(y1) + pad]


def annotate(rect: np.ndarray, tamper: dict, mrz_box, portrait_box) -> str:
    """Rectified document with findings drawn on it, as a base64 JPEG for the officer UI."""
    import base64
    vis = rect.copy()
    heat = tamper.get('_heatmap')
    if heat is not None and tamper.get('pixel_score') is not None and tamper['pixel_score'] >= (tamper.get('image_threshold') or 1):
        overlay = cv2.applyColorMap((np.clip(heat, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_JET)
        alpha = (np.clip(heat, 0, 1) * 0.55)[..., None]
        vis = (vis * (1 - alpha) + overlay * alpha).astype(np.uint8)
    if mrz_box:
        cv2.rectangle(vis, tuple(mrz_box[:2]), tuple(mrz_box[2:]), (255, 160, 0), 2)
    if portrait_box:
        cv2.rectangle(vis, tuple(portrait_box[:2]), tuple(portrait_box[2:]), (0, 200, 0), 2)
    for r in tamper.get('regions', []):
        if tamper.get('pixel_score', 0) >= (tamper.get('image_threshold') or 1):
            x0, y0, x1, y1 = r['box']
            cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 0, 255), 3)
            cv2.putText(vis, r['label'], (x0, max(15, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
    for c in tamper.get('copy_move', []):
        x0, y0, x1, y1 = c['clone_box']
        cv2.rectangle(vis, (x0, y0), (x1, y1), (255, 0, 255), 3)
    ok, enc = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return 'data:image/jpeg;base64,' + base64.b64encode(enc.tobytes()).decode()


def _json_safe(obj):
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items() if not str(k).startswith('_') and k not in ('embedding', 'live_embedding')}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


class ScreeningService:
    def __init__(self, registry: Registry | None = None, audit: AuditLog | None = None, policy: dict | None = None):
        self.policy = policy or load_policy()
        self.registry = registry or Registry()
        self.audit = audit or AuditLog()

    def status(self) -> dict:
        from .mrz_reader import model_error
        return dict(mrz_reader=mrz_model_available(), mrz_reader_error=model_error(), tamper_model=pixel_model_available(),
                    face_models=fv.models_available(),
                    policy_version=self.policy.get('policy_version'), registry=self.registry.stats(),
                    audit=self.audit.verify())

    # ---- single document -----------------------------------------------------------------
    def analyze_document(self, data, hint: str | None = None, today: date | None = None) -> dict:
        t = {}
        t0 = time.perf_counter()
        img, raw = decode_image(data)
        quad = locate_document(img)
        rect, H = rectify(img, quad)
        t['localise'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        mrz_read = read_mrz(rect)
        if not (mrz_read.mrz.valid_format and mrz_read.mrz.overall_check_digit_valid):
            alt = read_mrz(img)  # rectification can fail on unusual framing; the reader handles raw frames too
            if alt.confidence > mrz_read.confidence:
                mrz_read = alt
                # Quads from a 90/270-degree view are not in original-image coordinates.
                mrz_box = _box_from_quads(alt.line_quads, H) if alt.rotation in (0, 180) else None
            else:
                mrz_box = _box_from_quads(mrz_read.line_quads, None)
        else:
            mrz_box = _box_from_quads(mrz_read.line_quads, None)
        mrz = mrz_read.mrz.to_dict()
        t['mrz'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        prelim = classify_document(mrz, None, rect.shape, hint)
        portrait = fv.analyze_document_portrait(rect, self.policy)
        pbox = None
        if portrait.get('portrait'):
            x, y, w, h = portrait['portrait']['bbox']
            pbox = [int(x - w * 0.35), int(y - h * 0.45), int(x + w * 1.35), int(y + h * 1.4)]
        t['face'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        exclude = [b for b in (mrz_box, pbox) if b]
        viz = extract_fields(rect, prelim['document_type'], exclude_boxes=exclude)
        t['viz_ocr'] = time.perf_counter() - t0
        cls = classify_document(mrz, viz.doc_type_scores, rect.shape, hint)
        dtype = cls['document_type']
        fields, sources = merge_fields(dtype, mrz, viz.fields, today or date.today())
        guidance = capture_guidance(img, quad, rect, mrz_box)
        return dict(image=img, rectified=rect, raw=raw, quad=quad, mrz_read=mrz_read, mrz=mrz, mrz_box=mrz_box,
                    portrait=portrait, portrait_box=pbox, viz=viz, classification=cls, document_type=dtype,
                    fields=fields, field_sources=sources, capture=guidance, timings=t)

    # ---- full screening --------------------------------------------------------------------
    def screen(self, document, *, live_photo=None, companion=None, document_type: str | None = None,
               companion_type: str | None = None, context: dict | None = None, persist: bool = True,
               today: date | None = None, include_images: bool = False) -> dict:
        started = time.perf_counter()
        context = dict(context or {})
        today = today or date.today()
        screening_id = uuid.uuid4().hex
        policy = self.policy
        docs = [self.analyze_document(document, document_type, today)]
        if companion is not None:
            docs.append(self.analyze_document(companion, companion_type, today))

        passport = next((d for d in docs if d['document_type'] == 'passport'), None)
        items: list[dict] = []
        reports = []
        live_img = decode_image(live_photo)[0] if live_photo is not None else None
        face_result, face_embedding = None, None

        for idx, d in enumerate(docs):
            t = d['timings']
            record = dict(document_type=d['document_type'], fields=d['fields'], mrz=d['mrz'], viz_fields=d['viz'].fields,
                          viz_confidences=d['viz'].confidences, printed_dates=d['viz'].printed_dates)
            ctx = dict(context)
            ctx['mrz_read_reliable'] = mrz_read_reliable(d)
            if d['document_type'] == 'visa':
                prior = self.registry.entries_on_visa(d['fields'].get('document_number'))
                if prior is not None and 'prior_entries' not in ctx:
                    ctx['prior_entries'] = prior
            comp = None
            if d['document_type'] == 'visa' and passport is not None and passport is not d:
                comp = dict(fields=passport['fields'])
            t0 = time.perf_counter()
            validation = validate_document(record, today=today, policy=policy, companion=comp, context=ctx)
            t['validation'] = time.perf_counter() - t0

            t0 = time.perf_counter()
            zones = dict(portrait=d['portrait_box'], mrz=d['mrz_box'], fields=d['viz'].boxes)
            tamper = analyze_tampering(d['rectified'], raw_bytes=d['raw'], zones=zones, validation=validation,
                                       portrait=d['portrait'], policy=policy)
            t['tampering'] = time.perf_counter() - t0

            label = f"{d['document_type']}" + (' (companion)' if idx else '')
            for chk in validation['checks']:
                if chk['status'] in ('FAIL', 'WARN'):
                    items.append(evidence(chk['category'], chk['severity'], chk['id'], f"{label}: {chk['message']}",
                                          'validation', chk['status']))
            for case, data in tamper['use_cases'].items():
                if not data['evidence']:
                    continue
                strongest = max(data['evidence'], key=lambda e: SEVERITY_ORDER.get(e['severity'], 0))
                if strongest['source'] in ('mrz_viz_consistency', 'mrz_check_digits'):
                    continue  # already counted through validation
                items.append(evidence('tampering', strongest['severity'], f'tamper_{case}',
                                      f"{label}: {case.replace('_', ' ')} - {strongest['message']}", 'tampering',
                                      'FAIL' if data['detected'] else 'WARN'))
            q = d['capture']['quality_score']
            if q < 45:
                items.append(evidence('capture', 'medium', 'capture_quality', f'{label}: capture quality is low ({q:.0f}/100).',
                                      'capture', 'WARN'))

            hits = self.registry.check_watchlist(d['fields'])
            for h in hits:
                items.append(evidence('watchlist', 'critical', f"watchlist_{h['list_type'].lower()}",
                                      f"{label}: {h['list_type'].replace('_', ' ')} watchlist match on {h['match'].replace('_', ' ')}"
                                      f" ({h['reason']}).", 'registry'))
            reports.append(dict(document_type=d['document_type'], role='companion' if idx else 'primary',
                                classification=d['classification'], capture=d['capture'],
                                document_quad=d['quad'].round(1).tolist() if d['quad'] is not None else None,
                                ocr=dict(fields=d['fields'], field_sources=d['field_sources'], mrz=d['mrz'],
                                         mrz_read=d['mrz_read'].to_dict(), printed_zone=d['viz'].to_dict()),
                                validation=validation, tampering=tamper, watchlist_hits=hits,
                                annotated_image=annotate(d['rectified'], tamper, d['mrz_box'], d['portrait_box']) if include_images else None,
                                timings_ms={k: round(v * 1000) for k, v in t.items()}))

        # ---- face verification (primary document portrait vs live traveller) ----
        primary = passport or docs[0]
        face_block = dict(performed=False)
        if live_img is not None:
            t0 = time.perf_counter()
            face_result = fv.verify_faces(primary['rectified'], live_img, policy, doc_analysis=primary['portrait'])
            face_embedding = face_result.get('live_embedding')
            face_block = dict(performed=True, **face_result, elapsed_ms=round((time.perf_counter() - t0) * 1000))
            if face_result['decision'] == 'NO_MATCH':
                items.append(evidence('identity', 'critical', 'face_mismatch',
                                      f"Traveller does not match the document photo (similarity {face_result['similarity']:.2f}).", 'face'))
            elif face_result['decision'] == 'INCONCLUSIVE':
                items.append(evidence('identity', 'medium', 'face_inconclusive',
                                      'Face verification inconclusive: compare the traveller with the photo manually.', 'face', 'WARN'))
        if face_embedding is None and primary['portrait'].get('registry_grade'):
            face_embedding = primary['portrait'].get('embedding')

        # ---- identity registry: multiple identities / shared documents ----
        conflicts = self.registry.find_conflicts(primary['fields'], face_embedding, policy['face']['registry_duplicate_threshold'])
        for c in conflicts:
            items.append(evidence('identity', c['severity'], c['pattern'], c['message'], 'registry'))

        mrz_expected = any(d['document_type'] in MRZ_TYPES for d in docs)
        mrz_verified = all(d['mrz'].get('valid_format') and d['mrz'].get('overall_check_digit_valid')
                           for d in docs if d['document_type'] in MRZ_TYPES)
        risk = assess_risk(items, capture_quality=min(d['capture']['quality_score'] for d in docs), mrz_expected=mrz_expected,
                           mrz_verified=mrz_verified, face_checked=face_block['performed'], policy=policy)

        elapsed = time.perf_counter() - started
        report = dict(screening_id=screening_id, screened_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                      checkpoint_id=context.get('checkpoint_id'), officer_id=context.get('officer_id'), risk=risk,
                      documents=reports, face_verification=face_block, identity_conflicts=conflicts,
                      processing_ms=round(elapsed * 1000), policy_version=policy.get('policy_version'),
                      models=dict(mrz_reader='neural' if mrz_model_available() else 'unavailable',
                                  tamper=pixel_model_available(), face=fv.models_available()))
        report = _json_safe(report)

        if persist:
            for d in docs:
                if d['document_type'] == 'visa' and risk['disposition'] == 'CLEAR' and context.get('record_entry', True):
                    self.registry.record_travel(screening_id, (passport or d)['fields'].get('document_number'),
                                                d['fields'].get('document_number'), 'ENTRY', context.get('checkpoint_id'))
            self.registry.enroll(screening_id, primary['fields'], face_embedding, context.get('checkpoint_id'))
            audit = self.audit.append(dict(
                event='screening', screening_id=screening_id, checkpoint_id=context.get('checkpoint_id'),
                officer_id=context.get('officer_id'), inputs=[sha256_bytes(d['raw']) if d['raw'] else None for d in docs],
                live_photo=sha256_bytes(live_photo) if isinstance(live_photo, bytes) else None,
                documents=[dict(type=d['document_type'], number=d['fields'].get('document_number'),
                                issuing_state=d['fields'].get('issuing_state'), surname=d['fields'].get('surname'),
                                date_of_birth=d['fields'].get('date_of_birth')) for d in docs],
                risk_score=risk['risk_score'], risk_band=risk['risk_band'], disposition=risk['disposition'],
                reasons=[r['code'] for r in risk['reasons']], policy_version=policy.get('policy_version'),
                models=report['models']))
            report['audit'] = dict(seq=audit['seq'], hash=audit['hash'])
        return report
