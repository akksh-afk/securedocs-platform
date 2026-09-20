"""Generate document images with pixel-level forgery masks for the tamper localisation network.

Each sample is a rendered document (any of the five types), forged with probability
``--tamper-rate`` (one or two operations), then degraded the way a checkpoint capture would be,
optionally re-rectified from a perspective view, and stored at a fixed working width.

    python -m training.tamper_data --out data/tamper --train 7000 --val 700
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

from synthdocs import DOC_TYPES, TAMPER_TYPES, apply_tamper, render_document
from synthdocs.capture import _backdrop, photometric, sample_conditions

WORK_W = 1024


def _rectification_blur(img: np.ndarray, mask: np.ndarray, rng: random.Random):
    """Warp by a small homography and back again: the resampling a rectified capture goes through."""
    h, w = img.shape[:2]
    j = rng.uniform(0.005, 0.03)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + np.float32([[rng.uniform(-j, j) * w, rng.uniform(-j, j) * h] for _ in range(4)])
    M = cv2.getPerspectiveTransform(src, dst)
    scale = rng.uniform(0.55, 1.0)
    S = np.diag([scale, scale, 1.0]).astype(np.float64)
    fw, fh = int(w * scale), int(h * scale)
    warped = cv2.warpPerspective(img, S @ M, (fw, fh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    back = cv2.warpPerspective(warped, np.linalg.inv(S @ M), (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return back, mask


def make_sample(seed: int, tamper_rate: float) -> tuple[np.ndarray, np.ndarray, dict]:
    rng = random.Random(seed)
    dt = rng.choice(DOC_TYPES)
    doc = render_document(dt, seed=rng.randrange(1 << 30), today=date(2026, 9, 16), prefer_real_face=0.3)
    ops = []
    if rng.random() < tamper_rate:
        for _ in range(rng.choice([1, 1, 1, 2])):
            kind = rng.choice(TAMPER_TYPES)
            if apply_tamper(doc, kind, rng):
                ops.append(kind)
    img, mask = doc.image, doc.tamper_mask
    if rng.random() < 0.6:
        img, mask = _rectification_blur(img, mask, rng)
    severity = rng.choice([0, 0, 1, 1, 2, 3])
    conds = [c for c in sample_conditions(rng, severity) if not c.startswith('rotate') and c != 'occlusion']
    img = photometric(img, rng, conds, strength=rng.uniform(0.5, 1.0))
    if rng.random() < 0.25:  # some background around the page (imperfect crop)
        pad = [rng.randint(0, 60) for _ in range(4)]
        bd = _backdrop(img.shape[0] + pad[0] + pad[1], img.shape[1] + pad[2] + pad[3], rng)
        bd[pad[0]:pad[0] + img.shape[0], pad[2]:pad[2] + img.shape[1]] = img
        img = bd
        mask = cv2.copyMakeBorder(mask, pad[0], pad[1], pad[2], pad[3], cv2.BORDER_CONSTANT, value=0)
    s = WORK_W / img.shape[1]
    img = cv2.resize(img, (WORK_W, int(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    mask = cv2.resize(mask, (WORK_W, img.shape[0]), interpolation=cv2.INTER_NEAREST)
    q = rng.choice([80, 88, 92, 95])  # applied once when the sample is written
    meta = dict(seed=seed, doc_type=dt, tampered=bool(ops), ops=[o['type'] for o in doc.tamper_ops],
                conditions=conds, jpeg=q, mask_ratio=float((mask > 0).mean()))
    return img, mask, meta


def _init_worker():
    # One OpenCV thread per process: otherwise every worker spawns a full thread pool and they thrash.
    cv2.setNumThreads(1)


def _worker(args):
    out, split, idx, seed, rate = args
    img, mask, meta = make_sample(seed, rate)
    name = f'{idx:06d}'
    cv2.imwrite(str(out / split / f'{name}.jpg'), img, [cv2.IMWRITE_JPEG_QUALITY, meta['jpeg']])
    cv2.imwrite(str(out / split / f'{name}_mask.png'), mask)
    meta['name'] = name
    return meta


def build(out: Path, split: str, n: int, seed0: int, rate: float, workers: int):
    (out / split).mkdir(parents=True, exist_ok=True)
    jobs = [(out, split, i, seed0 + i, rate) for i in range(n)]
    metas = []
    with Pool(workers, initializer=_init_worker) as pool:
        for k, m in enumerate(pool.imap_unordered(_worker, jobs, chunksize=8)):
            metas.append(m)
            if k % 500 == 0:
                print(f'{split}: {k}/{n}', flush=True)
    metas.sort(key=lambda m: m['name'])
    (out / f'{split}.jsonl').write_text('\n'.join(json.dumps(m) for m in metas))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='data/tamper')
    ap.add_argument('--train', type=int, default=7000)
    ap.add_argument('--val', type=int, default=700)
    ap.add_argument('--tamper-rate', type=float, default=0.6)
    ap.add_argument('--workers', type=int, default=14)
    a = ap.parse_args()
    out = Path(a.out)
    build(out, 'val', a.val, 700_000_000, 0.5, a.workers)
    build(out, 'train', a.train, 0, a.tamper_rate, a.workers)


if __name__ == '__main__':
    main()
