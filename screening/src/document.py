"""Document localisation, rectification, capture guidance and document-type classification."""
from __future__ import annotations

import cv2
import numpy as np

from .capture_robustness import quality_metrics

DOC_TYPES = ('passport', 'visa', 'national_id', 'driving_licence', 'residence_permit')
ASPECT = {'passport': 1.42, 'visa': 1.5, 'national_id': 1.586, 'driving_licence': 1.586, 'residence_permit': 1.586}


def _order(pts: np.ndarray) -> np.ndarray:
    pts = pts.reshape(4, 2).astype(np.float32)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]  # clockwise from top-left-ish in image coordinates
    start = np.argmin(pts.sum(axis=1))
    return np.roll(pts, -start, axis=0)


def _quad_score(q: np.ndarray, area_img: float) -> float:
    q = _order(q)
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    if min(w, h) < 1:
        return -1
    aspect = max(w, h) / min(w, h)
    area = cv2.contourArea(q)
    if area < 0.12 * area_img or area > 0.985 * area_img or not 1.2 <= aspect <= 1.85:
        return -1
    # Prefer large quads with right angles.
    cos = []
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cos.append(abs(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6)))
    return area / area_img - 1.5 * max(cos)


def locate_document(img: np.ndarray) -> np.ndarray | None:
    """Return the ordered 4-corner quad of the document in ``img`` or None if it fills the frame."""
    scale = 800 / max(img.shape[:2])
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    area_img = small.shape[0] * small.shape[1]
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    cands = []
    med = float(np.median(gray))
    for lo, hi in ((0.5 * med, 1.3 * med), (20, 60), (50, 150)):
        edges = cv2.Canny(gray, lo, hi)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
        cands += cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
    for ch in (lab[..., 0], lab[..., 1], lab[..., 2]):
        _, th = cv2.threshold(cv2.GaussianBlur(ch, (7, 7), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for t in (th, 255 - th):
            t = cv2.morphologyEx(t, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
            cands += cv2.findContours(t, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    best, best_s = None, 0.0
    for c in cands:
        if cv2.contourArea(c) < 0.12 * area_img:
            continue
        hull = cv2.convexHull(c)
        peri = cv2.arcLength(hull, True)
        quad = None
        for eps in (0.02, 0.03, 0.05):
            ap = cv2.approxPolyDP(hull, eps * peri, True)
            if len(ap) == 4:
                quad = ap.reshape(4, 2).astype(np.float32)
                break
        if quad is None:
            quad = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)
        s = _quad_score(quad, area_img)
        if s > best_s:
            best, best_s = quad, s
    return None if best is None else _order(best) / scale


def rectify(img: np.ndarray, quad: np.ndarray | None, target_w: int = 1500) -> tuple[np.ndarray, np.ndarray]:
    """Warp the document to a fronto-parallel landscape view. Returns (image, homography orig->rectified)."""
    if quad is None:
        h, w = img.shape[:2]
        if h > w:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
            H = np.array([[0, -1, h - 1], [1, 0, 0], [0, 0, 1]], np.float64)
            h, w = w, h
        else:
            H = np.eye(3)
        s = target_w / w
        S = np.diag([s, s, 1.0])
        return cv2.resize(img, (target_w, int(round(h * s))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC), S @ H
    q = _order(quad)
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    if h > w:  # portrait-held capture: rotate corner order so the long side is horizontal
        q = np.roll(q, -1, axis=0)
        w, h = h, w
    out_w, out_h = target_w, int(round(target_w * h / w))
    dst = np.float32([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]])
    H = cv2.getPerspectiveTransform(q, dst)
    return cv2.warpPerspective(img, H, (out_w, out_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE), H


def capture_guidance(img: np.ndarray, quad: np.ndarray | None, rectified: np.ndarray, mrz_box=None) -> dict:
    """Actionable advice for the officer on how to capture a better image."""
    q = quality_metrics(img)
    tips = []
    if q.blur_score < 35:
        tips.append('Hold the camera steady or tap to focus: the image is blurred.')
    if q.brightness < 60:
        tips.append('Increase lighting: the capture is too dark.')
    if q.brightness > 220:
        tips.append('Reduce exposure or move away from direct light.')
    if q.glare_ratio > 0.01:
        tips.append('Tilt the document slightly to remove reflections (glare detected).')
    if mrz_box:
        x0, y0, x1, y1 = [max(0, int(v)) for v in mrz_box]
        roi = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY)[y0:y1, x0:x1]
        if roi.size and (roi > 245).mean() > 0.05:
            tips.append('Glare covers the machine readable zone: tilt the document.')
    coverage = None
    if quad is not None:
        coverage = float(cv2.contourArea(quad.astype(np.float32)) / (img.shape[0] * img.shape[1]))
        if coverage < 0.3:
            tips.append('Move closer: the document fills too little of the frame.')
        qq = _order(quad)
        top, bottom = np.linalg.norm(qq[1] - qq[0]), np.linalg.norm(qq[2] - qq[3])
        left, right = np.linalg.norm(qq[3] - qq[0]), np.linalg.norm(qq[2] - qq[1])
        skew = max(top, bottom) / max(1, min(top, bottom)), max(left, right) / max(1, min(left, right))
        if max(skew) > 1.12:
            tips.append('Hold the camera parallel to the document (strong perspective).')
    if min(img.shape[:2]) < 600:
        tips.append('Use a higher capture resolution.')
    from .capture_advisor import predict_read_success
    success = predict_read_success(img, quad)
    if success is not None and success < 0.5:
        tips.insert(0, f'Recapture recommended: predicted chance of a reliable read is {success:.0%}.')
    d = q.to_dict()
    d.update(document_found=quad is not None, frame_coverage=round(coverage, 3) if coverage is not None else None,
             predicted_read_success=round(success, 3) if success is not None else None, tips=tips)
    return d


def classify_document(mrz: dict | None, viz_scores: dict | None, rectified_shape, hint: str | None = None) -> dict:
    scores = {t: 0.0 for t in DOC_TYPES}
    evidence = []
    if hint in scores:
        scores[hint] += 2.0
        evidence.append(f'operator selected {hint}')
    if mrz and mrz.get('valid_format'):
        code = (mrz.get('document_code') or '')
        fmt = mrz.get('format')
        if fmt == 'TD3' and code.startswith('P'):
            scores['passport'] += 3; evidence.append('TD3 passport MRZ')
        elif fmt in ('MRVA', 'MRVB'):
            scores['visa'] += 3; evidence.append(f'{fmt} visa MRZ')
        elif fmt in ('TD1', 'TD2'):
            if code[1:2] in ('R', 'P') or code in ('AR', 'IR'):
                scores['residence_permit'] += 2.5; evidence.append(f'{fmt} MRZ with permit code {code}')
            else:
                scores['national_id'] += 2.5; evidence.append(f'{fmt} ID-card MRZ ({code})')
            scores['residence_permit'] += 0.4; scores['national_id'] += 0.4
    for t, s in (viz_scores or {}).items():
        if t in scores and s >= 0.85:
            scores[t] += 1.5 * s
            evidence.append(f'printed keyword for {t} ({s:.2f})')
        elif t in scores and s >= 0.7:  # partly garbled title on a poor capture
            scores[t] += 0.8 * s
            evidence.append(f'partial printed keyword for {t} ({s:.2f})')
    if not (mrz and mrz.get('valid_format')):
        scores['driving_licence'] += 0.25  # the only supported type without a machine readable zone
    if rectified_shape is not None:
        h, w = rectified_shape[:2]
        aspect = w / max(h, 1)
        for t, a in ASPECT.items():
            scores[t] += max(0.0, 0.5 - abs(aspect - a) * 4)
    best = max(scores, key=scores.get)
    total = sum(max(v, 0) for v in scores.values()) or 1.0
    return dict(document_type=best, confidence=round(scores[best] / total, 3), scores={k: round(v, 2) for k, v in scores.items()},
                evidence=evidence)
