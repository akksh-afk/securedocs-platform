"""Portrait provider: procedural avatars, or real face photos placed in data/faces/ (with consent)."""
from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
FACE_DIR = ROOT / 'data' / 'faces'

SKIN = [(255, 224, 196), (241, 194, 160), (224, 172, 128), (198, 134, 94), (161, 102, 66), (120, 75, 48), (90, 56, 38)]
HAIR = [(20, 20, 20), (45, 30, 20), (80, 55, 35), (140, 100, 60), (200, 170, 110), (160, 160, 160), (110, 40, 20)]
CLOTHES = [(40, 50, 80), (20, 20, 20), (220, 220, 225), (120, 30, 40), (40, 90, 60), (90, 90, 100), (200, 170, 120)]
BACKDROP = [(245, 245, 245), (225, 235, 245), (230, 230, 225), (210, 225, 240), (250, 250, 240)]


def avatar(seed: int, w: int = 300, h: int = 380) -> np.ndarray:
    rng = random.Random(seed)
    im = Image.new('RGB', (w, h), rng.choice(BACKDROP))
    d = ImageDraw.Draw(im)
    skin = rng.choice(SKIN)
    hair = rng.choice(HAIR)
    cx = w // 2 + rng.randint(-10, 10)
    fw, fh = int(w * rng.uniform(0.34, 0.44)), int(h * rng.uniform(0.40, 0.48))
    top = int(h * rng.uniform(0.12, 0.2))
    d.pieslice((cx - int(w * 0.48), int(h * 0.74), cx + int(w * 0.48), int(h * 1.35)), 180, 360, fill=rng.choice(CLOTHES))
    d.rectangle((cx - fw // 5, top + fh - 20, cx + fw // 5, int(h * 0.8)), fill=skin)
    style = rng.randrange(4)
    if style in (1, 3):
        d.ellipse((cx - fw // 2 - 18, top - 12, cx + fw // 2 + 18, top + int(fh * 1.05)), fill=hair)
    d.ellipse((cx - fw // 2 - 9, top + fh // 3, cx - fw // 2 + 9, top + fh // 3 + 38), fill=skin)
    d.ellipse((cx + fw // 2 - 9, top + fh // 3, cx + fw // 2 + 9, top + fh // 3 + 38), fill=skin)
    d.ellipse((cx - fw // 2, top, cx + fw // 2, top + fh), fill=skin)
    if style != 2:
        d.chord((cx - fw // 2 - 4, top - 14, cx + fw // 2 + 4, top + int(fh * 0.55)), 180, 360, fill=hair)
    ey = top + int(fh * 0.45)
    ex = int(fw * 0.2)
    for sx in (-1, 1):
        x = cx + sx * ex
        d.ellipse((x - 13, ey - 6, x + 13, ey + 6), fill=(245, 245, 245))
        d.ellipse((x - 6, ey - 6, x + 6, ey + 6), fill=rng.choice([(60, 40, 20), (40, 60, 90), (30, 30, 30), (70, 90, 50)]))
        d.line((x - 15, ey - 16 + rng.randint(-3, 3), x + 15, ey - 18), fill=hair, width=4)
    d.line((cx, ey + 8, cx - 6, ey + int(fh * 0.22)), fill=tuple(int(c * 0.8) for c in skin), width=3)
    my = top + int(fh * 0.76)
    d.arc((cx - 22, my - 10, cx + 22, my + 8), 20, 160, fill=(150, 70, 70), width=4)
    if rng.random() < 0.25:
        d.ellipse((cx - ex - 20, ey - 17, cx - ex + 20, ey + 17), outline=(30, 30, 30), width=3)
        d.ellipse((cx + ex - 20, ey - 17, cx + ex + 20, ey + 17), outline=(30, 30, 30), width=3)
        d.line((cx - ex + 20, ey, cx + ex - 20, ey), fill=(30, 30, 30), width=3)
    im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.6, 1.4)))
    arr = np.asarray(im).astype(np.float32)
    arr += np.random.default_rng(seed).normal(0, 3, arr.shape)
    return cv2.cvtColor(np.clip(arr, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)


@lru_cache(maxsize=1)
def real_face_files() -> tuple[str, ...]:
    files = []
    if FACE_DIR.exists():
        files = sorted(str(p) for p in FACE_DIR.rglob('*') if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.webp'})
    return tuple(files)


@lru_cache(maxsize=64)
def _load_face(path: str) -> np.ndarray | None:
    return cv2.imread(path)


def portrait(seed: int, w: int, h: int, prefer_real: float = 0.0) -> np.ndarray:
    """Portrait image resized/cropped to (w, h). Real faces are used with probability ``prefer_real``."""
    rng = random.Random(seed)
    files = real_face_files()
    img = None
    if files and rng.random() < prefer_real:
        img = _load_face(files[seed % len(files)])
    if img is None:
        img = avatar(seed)
    ih, iw = img.shape[:2]
    target = w / h
    if iw / ih > target:
        nw = int(ih * target); x0 = (iw - nw) // 2
        img = img[:, x0:x0 + nw]
    else:
        nh = int(iw / target); y0 = max(0, (ih - nh) // 3)
        img = img[y0:y0 + nh]
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
