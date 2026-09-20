"""Capture advisor: a trained model that predicts whether a capture is good enough to screen."""
from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from .capture_robustness import quality_metrics
from .config import MODEL_DIR

FEATURES = ['blur', 'brightness', 'contrast', 'glare', 'noise', 'clipped', 'quality', 'doc_found', 'coverage', 'skew',
            'min_side', 'local_contrast_p10', 'saturated_blocks', 'dark_blocks', 'edge_density']


def capture_features(img: np.ndarray, quad: np.ndarray | None) -> dict:
    q = quality_metrics(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    s = 800 / max(gray.shape)
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    bh, bw = small.shape[0] // 8, small.shape[1] // 8
    blocks = small[:bh * 8, :bw * 8].reshape(8, bh, 8, bw).transpose(0, 2, 1, 3).reshape(64, -1)
    stds = blocks.std(axis=1)
    coverage, skew = 0.0, 1.0
    if quad is not None:
        coverage = float(cv2.contourArea(quad.astype(np.float32)) / (img.shape[0] * img.shape[1]))
        top, bottom = np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3])
        left, right = np.linalg.norm(quad[3] - quad[0]), np.linalg.norm(quad[2] - quad[1])
        skew = float(max(max(top, bottom) / max(1, min(top, bottom)), max(left, right) / max(1, min(left, right))))
    return dict(blur=q.blur_score, brightness=q.brightness, contrast=q.contrast, glare=q.glare_ratio, noise=q.noise_score,
                clipped=q.clipped_ratio, quality=q.quality_score, doc_found=float(quad is not None), coverage=coverage,
                skew=skew, min_side=float(min(img.shape[:2])), local_contrast_p10=float(np.percentile(stds, 10)),
                saturated_blocks=float((blocks.mean(axis=1) > 235).mean()), dark_blocks=float((blocks.mean(axis=1) < 40).mean()),
                edge_density=float((cv2.Canny(small, 60, 160) > 0).mean()))


@lru_cache(maxsize=1)
def _bundle():
    p = MODEL_DIR / 'capture_advisor.joblib'
    try:
        import joblib
        return joblib.load(p) if p.exists() else None
    except ImportError:
        return None


MIN_AUC = 0.75  # below this the advisor would misdirect officers more than it helps


def predict_read_success(img: np.ndarray, quad: np.ndarray | None) -> float | None:
    b = _bundle()
    if b is None or b.get('metrics', {}).get('test_auc', 0) < MIN_AUC:
        return None
    f = capture_features(img, quad)
    x = np.array([[f[k] for k in b['features']]], np.float32)
    return float(b['model'].predict_proba(x)[0, 1])
