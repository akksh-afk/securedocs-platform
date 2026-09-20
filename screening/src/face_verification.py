"""Module 4: face detection and verification (document portrait vs. the person presenting it).

Detector: YuNet; recogniser: SFace (OpenCV Zoo, Apache-2.0). Similarity is cosine between
L2-normalised 128-d embeddings; OpenCV's published SFace threshold is 0.363."""
from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from .config import MODEL_DIR, load_policy

YUNET = MODEL_DIR / 'face_detection_yunet_2023mar.onnx'
SFACE = MODEL_DIR / 'face_recognition_sface_2021dec.onnx'


def models_available() -> bool:
    return YUNET.exists() and SFACE.exists()


@lru_cache(maxsize=1)
def _detector():
    return cv2.FaceDetectorYN.create(str(YUNET), '', (320, 320), 0.6, 0.3, 5000)


@lru_cache(maxsize=1)
def _recognizer():
    return cv2.FaceRecognizerSF.create(str(SFACE), '')


def detect_faces(img: np.ndarray, score_threshold: float = 0.6, max_side: int = 1280) -> list[dict]:
    """Detect faces; returns dicts with bbox (x, y, w, h), 5 landmarks, score, and raw row."""
    if not models_available():
        return _haar_faces(img)
    scale = min(1.0, max_side / max(img.shape[:2]))
    work = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
    det = _detector()
    det.setScoreThreshold(score_threshold)
    faces = []
    # Small portraits on documents: also try an upscaled pass.
    for up in (1.0, 2.0):
        view = work if up == 1.0 else cv2.resize(work, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
        if max(view.shape[:2]) > 2600:
            continue
        det.setInputSize((view.shape[1], view.shape[0]))
        _, rows = det.detect(view)
        if rows is None:
            continue
        for r in rows:
            r = r.copy()
            r[:14] = r[:14] / (scale * up)
            faces.append(r)
        if faces:
            break
    out = []
    for r in sorted(faces, key=lambda r: -r[2] * r[3]):
        x, y, w, h = [float(v) for v in r[:4]]
        if any(abs(x - o['bbox'][0]) < w * 0.3 and abs(y - o['bbox'][1]) < h * 0.3 for o in out):
            continue
        out.append(dict(bbox=[round(x), round(y), round(w), round(h)], score=round(float(r[14]), 3),
                        landmarks=[[round(float(r[4 + 2 * i])), round(float(r[5 + 2 * i]))] for i in range(5)], _row=r))
    return out


def _haar_faces(img: np.ndarray) -> list[dict]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    return [dict(bbox=[int(x), int(y), int(w), int(h)], score=None, landmarks=None, _row=None)
            for x, y, w, h in cascade.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))]


def embed(img: np.ndarray, face: dict) -> np.ndarray | None:
    if face.get('_row') is None or not models_available():
        return None
    rec = _recognizer()
    aligned = rec.alignCrop(img, face['_row'])
    feat = rec.feature(aligned).flatten().astype(np.float32)
    return feat / (np.linalg.norm(feat) + 1e-9)


def _face_quality(img: np.ndarray, face: dict, min_px: int) -> list[str]:
    x, y, w, h = face['bbox']
    issues = []
    if min(w, h) < min_px:
        issues.append(f'face is small ({int(min(w, h))}px); matching is less reliable')
    crop = img[max(0, y):y + h, max(0, x):x + w]
    if crop.size:
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if cv2.Laplacian(g, cv2.CV_64F).var() < 25:
            issues.append('face region is blurred')
        if g.mean() < 45:
            issues.append('face region is very dark')
        if (g > 245).mean() > 0.25:
            issues.append('glare over the face')
    if face.get('score') is not None and face['score'] < 0.75:
        issues.append('low face-detection confidence')
    return issues


def _strip(face: dict | None) -> dict | None:
    return None if face is None else {k: v for k, v in face.items() if k != '_row'}


def analyze_document_portrait(doc_img: np.ndarray, policy: dict | None = None) -> dict:
    """Locate the holder portrait; compare it with the ghost image when one is present."""
    fp = (policy or load_policy())['face']
    faces = detect_faces(doc_img)
    result = dict(face_count=len(faces), portrait=None, ghost=None, ghost_similarity=None, issues=[], embedding=None)
    if not faces:
        result['issues'].append('no face detected on the document (portrait missing, covered or replaced by a non-photo)')
        return result
    portrait = faces[0]
    result['portrait'] = _strip(portrait)
    result['issues'] += _face_quality(doc_img, portrait, fp['min_face_px'])
    emb = embed(doc_img, portrait)
    result['embedding'] = emb
    result['registry_grade'] = portrait.get('score') is not None and portrait['score'] >= fp['registry_min_detection_score']
    # A secondary, clearly smaller face is the ghost image printed for anti-substitution.
    for g in faces[1:]:
        if g['bbox'][2] < portrait['bbox'][2] * 0.75:
            result['ghost'] = _strip(g)
            ge = embed(doc_img, g)
            if emb is not None and ge is not None:
                sim = float(np.dot(emb, ge))
                result['ghost_similarity'] = round(sim, 3)
                if sim < fp['cosine_inconclusive_floor']:
                    result['issues'].append('ghost image does not match the main portrait: possible photo substitution')
            break
    return result


def verify_faces(doc_img: np.ndarray, live_img: np.ndarray, policy: dict | None = None,
                 doc_analysis: dict | None = None) -> dict:
    """1:1 verification of the document portrait against a live capture of the traveller."""
    fp = (policy or load_policy())['face']
    doc = doc_analysis or analyze_document_portrait(doc_img, policy)
    live_faces = detect_faces(live_img)
    out = dict(decision='INCONCLUSIVE', similarity=None, threshold=fp['cosine_match_threshold'],
               document_face_found=doc['portrait'] is not None, live_face_count=len(live_faces), issues=list(doc['issues']))
    if not live_faces:
        out['issues'].append('no face detected in the live capture')
        return out
    if len(live_faces) > 1:
        out['issues'].append(f'{len(live_faces)} faces in the live capture; using the largest')
    live = live_faces[0]
    out['issues'] += [f'live: {i}' for i in _face_quality(live_img, live, fp['min_face_px'])]
    if doc['embedding'] is None:
        out['issues'].append('document portrait unavailable for matching')
        return out
    live_emb = embed(live_img, live)
    if live_emb is None:
        return out
    sim = float(np.dot(doc['embedding'], live_emb))
    out['similarity'] = round(sim, 4)
    out['live_embedding'] = live_emb
    if sim >= fp['cosine_match_threshold']:
        out['decision'] = 'MATCH'
    elif sim < fp['cosine_inconclusive_floor']:
        out['decision'] = 'NO_MATCH'
    return out
