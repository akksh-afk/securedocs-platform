"""Simulate how a document looks when captured at a checkpoint (phone camera or scanner)."""
from __future__ import annotations

import math
import random

import cv2
import numpy as np

CONDITIONS = ['low_light', 'overexposed', 'glare', 'shadow', 'motion_blur', 'defocus', 'noise', 'jpeg', 'tiny',
              'color_cast', 'occlusion', 'rotate180', 'rotate90']


def _backdrop(h: int, w: int, rng: random.Random) -> np.ndarray:
    kind = rng.choice(['solid', 'wood', 'fabric', 'scanner', 'clutter', 'dark'])
    nprng = np.random.default_rng(rng.randrange(1 << 30))
    if kind == 'scanner':
        return np.full((h, w, 3), rng.randint(225, 252), np.uint8)
    base = np.array([rng.randint(20, 200) for _ in range(3)], np.float32)
    if kind == 'dark':
        base = base * 0.25
    img = np.ones((h, w, 3), np.float32) * base
    if kind == 'wood':
        yy = np.arange(h, dtype=np.float32)[:, None]
        xx = np.arange(w, dtype=np.float32)[None, :]
        stripes = np.sin(yy / rng.uniform(4, 12) + np.sin(xx / rng.uniform(40, 120)) * 3) * 18
        img += stripes[..., None]
    img += cv2.GaussianBlur(nprng.normal(0, 25, (h, w, 1)).astype(np.float32), (0, 0), rng.uniform(1, 6))[..., None] \
        if kind == 'fabric' else nprng.normal(0, 6, (h, w, 1)).astype(np.float32)
    if kind == 'clutter':
        img = np.clip(img, 0, 255).astype(np.uint8)
        for _ in range(rng.randint(3, 10)):
            x0, y0 = rng.randint(0, w - 1), rng.randint(0, h - 1)
            cv2.rectangle(img, (x0, y0), (x0 + rng.randint(40, w // 2), y0 + rng.randint(20, h // 3)),
                          tuple(float(rng.randint(0, 255)) for _ in range(3)), -1)
        for _ in range(rng.randint(2, 8)):
            txt = ''.join(rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ') for _ in range(rng.randint(5, 25)))
            cv2.putText(img, txt, (rng.randint(0, w - 50), rng.randint(20, h - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                        rng.uniform(0.5, 1.5), (20, 20, 20), 2, cv2.LINE_AA)
        img = img.astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def _motion_kernel(length: int, angle: float) -> np.ndarray:
    k = np.zeros((length, length), np.float32)
    c = length // 2
    dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    for t in np.linspace(-c, c, length * 2):
        x, y = int(round(c + t * dx)), int(round(c + t * dy))
        if 0 <= x < length and 0 <= y < length:
            k[y, x] = 1
    return k / max(k.sum(), 1)


def photometric(img: np.ndarray, rng: random.Random, conditions: list[str], strength: float = 1.0) -> np.ndarray:
    out = img.astype(np.float32)
    h, w = out.shape[:2]
    nprng = np.random.default_rng(rng.randrange(1 << 30))
    s = strength
    if 'color_cast' in conditions:
        out *= np.array([rng.uniform(0.8, 1.2) for _ in range(3)], np.float32)
    if 'shadow' in conditions:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        a = rng.uniform(0, 2 * math.pi)
        proj = (xx * math.cos(a) + yy * math.sin(a)) / max(h, w)
        edge = rng.uniform(proj.min(), proj.max())
        field = 1 - (1 - rng.uniform(0.35, 0.7)) * s / (1 + np.exp(-(proj - edge) * rng.uniform(15, 60)))
        out *= field[..., None]
    if 'low_light' in conditions:
        gain = rng.uniform(0.22, 0.55) ** s
        out = 255 * (np.clip(out * gain, 0, 255) / 255) ** rng.uniform(1.0, 1.5)
    if 'overexposed' in conditions:
        out = out * rng.uniform(1.3, 1.9) * s + rng.uniform(10, 60) * s
    if 'glare' in conditions:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        for _ in range(rng.randint(1, 3)):
            cx, cy = rng.uniform(0.1, 0.9) * w, rng.uniform(0.1, 0.9) * h
            sx, sy = rng.uniform(0.03, 0.18) * w, rng.uniform(0.03, 0.18) * h
            ang = rng.uniform(0, math.pi)
            xr = (xx - cx) * math.cos(ang) + (yy - cy) * math.sin(ang)
            yr = -(xx - cx) * math.sin(ang) + (yy - cy) * math.cos(ang)
            blob = np.exp(-(xr ** 2 / (2 * sx ** 2) + yr ** 2 / (2 * sy ** 2)))
            out += (blob * rng.uniform(120, 300) * s)[..., None]
    out = np.clip(out, 0, 255)
    if 'motion_blur' in conditions:
        out = cv2.filter2D(out, -1, _motion_kernel(max(3, int(rng.uniform(4, 22) * s) | 1), rng.uniform(0, 180)))
    if 'defocus' in conditions:
        out = cv2.GaussianBlur(out, (0, 0), rng.uniform(1.0, 3.5) * s)
    if 'noise' in conditions:
        sigma = rng.uniform(5, 24) * s
        out = out + nprng.normal(0, sigma, out.shape).astype(np.float32) * (0.5 + out / 510)
    out = np.clip(out, 0, 255).astype(np.uint8)
    if 'tiny' in conditions:
        f = rng.uniform(0.28, 0.5) / max(s, 0.5)
        small = cv2.resize(out, (max(16, int(w * f)), max(16, int(h * f))), interpolation=cv2.INTER_AREA)
        out = cv2.resize(small, (w, h), interpolation=rng.choice([cv2.INTER_LINEAR, cv2.INTER_CUBIC]))
    if 'jpeg' in conditions:
        ok, enc = cv2.imencode('.jpg', out, [cv2.IMWRITE_JPEG_QUALITY, int(rng.uniform(18, 55))])
        out = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    if 'occlusion' in conditions:
        skin = tuple(float(c) for c in rng.choice([(150, 180, 220), (110, 140, 190), (70, 100, 150)]))
        cx = rng.choice([0, w]) + rng.randint(-40, 40)
        cy = rng.randint(int(h * 0.2), int(h * 0.8))
        cv2.ellipse(out, (cx, cy), (int(w * 0.09), int(h * 0.16)), rng.uniform(-30, 30), 0, 360, skin, -1)
    return out


def sample_conditions(rng: random.Random, severity: int) -> list[str]:
    if severity <= 0:
        return rng.sample(['jpeg', 'noise', 'color_cast'], rng.randint(0, 1))
    pool = [c for c in CONDITIONS if not c.startswith('rotate')]
    conds = rng.sample(pool, min(len(pool), severity + rng.randint(0, 1)))
    if 'low_light' in conds and 'overexposed' in conds:
        conds.remove('overexposed')
    if 'motion_blur' in conds and 'defocus' in conds:
        conds.remove('defocus')
    if rng.random() < 0.07:
        conds.append('rotate180')
    elif rng.random() < 0.04:
        conds.append('rotate90')
    return conds


def simulate_capture(doc_img: np.ndarray, rng: random.Random, masks: dict[str, np.ndarray] | None = None,
                     severity: int | None = None, conditions: list[str] | None = None,
                     perspective: float | None = None, out_long: int | None = None,
                     flat: bool = False):
    """Place the document into a camera frame and degrade it.

    Returns (image, warped_masks, homography, meta). ``homography`` maps document-canvas
    coordinates to capture coordinates, so annotation boxes can be projected.
    ``flat=True`` skips the backdrop/geometry (a flatbed-scanner style capture).
    """
    masks = masks or {}
    severity = rng.choice([0, 1, 1, 2, 2, 3]) if severity is None else severity
    conditions = sample_conditions(rng, severity) if conditions is None else list(conditions)
    dh, dw = doc_img.shape[:2]
    if flat:
        Hm = np.eye(3, dtype=np.float64)
        frame = doc_img.copy()
        warped = {k: v.copy() for k, v in masks.items()}
    else:
        out_long = out_long or rng.randint(1100, 1900)
        aspect = rng.choice([4 / 3, 16 / 9, dw / dh * rng.uniform(1.02, 1.2)])
        FW, FH = out_long, int(out_long / aspect)
        fill = rng.uniform(0.62, 0.95)
        scale = min(FW * fill / dw, FH * fill / dh)
        sw, sh = dw * scale, dh * scale
        cx, cy = FW / 2 + rng.uniform(-0.5, 0.5) * (FW - sw), FH / 2 + rng.uniform(-0.5, 0.5) * (FH - sh)
        p = (rng.uniform(0.0, 0.07) if perspective is None else perspective)
        ang = math.radians(rng.uniform(-7, 7))
        corners = []
        for ux, uy in [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]:
            x, y = ux * sw, uy * sh
            x, y = x * math.cos(ang) - y * math.sin(ang), x * math.sin(ang) + y * math.cos(ang)
            corners.append((cx + x + rng.uniform(-p, p) * sw, cy + y + rng.uniform(-p, p) * sh))
        src = np.float32([[0, 0], [dw, 0], [dw, dh], [0, dh]])
        dst = np.float32(corners)
        Hm = cv2.getPerspectiveTransform(src, dst).astype(np.float64)
        frame = _backdrop(FH, FW, rng)
        shade = np.zeros((FH, FW), np.uint8)
        cv2.fillConvexPoly(shade, np.int32(dst + np.float32([rng.uniform(3, 14), rng.uniform(3, 14)])), 255)
        shade = cv2.GaussianBlur(shade, (0, 0), 9).astype(np.float32) / 255 * rng.uniform(0.15, 0.4)
        frame = (frame.astype(np.float32) * (1 - shade[..., None])).astype(np.uint8)
        warped_doc = cv2.warpPerspective(doc_img, Hm, (FW, FH), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT,
                                         dst=frame.copy())
        alpha = cv2.warpPerspective(np.full((dh, dw), 255, np.uint8), Hm, (FW, FH), flags=cv2.INTER_LINEAR)
        a = (alpha.astype(np.float32) / 255)[..., None]
        frame = (warped_doc * a + frame * (1 - a)).astype(np.uint8)
        warped = {k: cv2.warpPerspective(v, Hm, (FW, FH), flags=cv2.INTER_LINEAR) for k, v in masks.items()}
        warped['document'] = alpha
    frame = photometric(frame, rng, conditions)
    rot = 180 if 'rotate180' in conditions else 90 if 'rotate90' in conditions else 0
    if rot:
        code = cv2.ROTATE_180 if rot == 180 else cv2.ROTATE_90_CLOCKWISE
        FH, FW = frame.shape[:2]
        frame = cv2.rotate(frame, code)
        warped = {k: cv2.rotate(v, code) for k, v in warped.items()}
        R = np.array([[-1, 0, FW - 1], [0, -1, FH - 1], [0, 0, 1]], np.float64) if rot == 180 else \
            np.array([[0, -1, FH - 1], [1, 0, 0], [0, 0, 1]], np.float64)
        Hm = R @ Hm
    return frame, warped, Hm, dict(severity=severity, conditions=conditions, rotation=rot)


def project_box(box, Hm) -> np.ndarray:
    x0, y0, x1, y1 = box
    pts = np.float32([[[x0, y0]], [[x1, y0]], [[x1, y1]], [[x0, y1]]])
    return cv2.perspectiveTransform(pts, Hm).reshape(4, 2)
