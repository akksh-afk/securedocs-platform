from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np


@dataclass
class CaptureQuality:
    width: int
    height: int
    blur_score: float
    brightness: float
    contrast: float
    glare_ratio: float
    noise_score: float
    clipped_ratio: float
    document_quad_found: bool
    quality_score: float
    warnings: list[str]

    def to_dict(self):
        return asdict(self)


def quality_metrics(img: np.ndarray) -> CaptureQuality:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    # Measure at a fixed scale so scores are comparable across camera resolutions.
    s = 1200 / max(gray.shape)
    if s < 1:
        gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    contrast = float(gray.std())
    glare = float(np.mean(gray >= 250))
    clipped = float(np.mean((gray <= 2) | (gray >= 253)))
    noise = float(np.std(gray.astype(np.float32) - cv2.GaussianBlur(gray, (0, 0), 1.0).astype(np.float32)))

    score = 100.0
    score -= min(40.0, max(0.0, 60.0 - min(60.0, blur)) * 0.55)
    score -= min(25.0, abs(brightness - 145.0) * 0.17)
    score -= min(20.0, max(0.0, 18.0 - contrast) * 1.0)
    score -= min(20.0, glare * 100.0 * 0.7)
    score -= min(10.0, max(0.0, noise - 7.0) * 0.45)
    score = float(np.clip(score, 0, 100))

    warnings = []
    if blur < 35: warnings.append('Severe blur detected.')
    if brightness < 55: warnings.append('Very dark capture.')
    if brightness > 225: warnings.append('Overexposed capture.')
    if glare > 0.02: warnings.append('Strong glare/highlight detected.')
    if contrast < 18: warnings.append('Low contrast detected.')
    if noise > 18: warnings.append('Heavy sensor/compression noise detected.')
    return CaptureQuality(img.shape[1], img.shape[0], round(blur, 2), round(brightness, 2), round(contrast, 2), round(glare, 5),
                          round(noise, 2), round(clipped, 5), False, round(score, 2), warnings)
