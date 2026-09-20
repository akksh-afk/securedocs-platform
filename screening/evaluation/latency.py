"""End-to-end screening latency (all modules, including face verification and audit write).

    python -m evaluation.latency --n 30            # uses the GPU when available
    set CUDA_VISIBLE_DEVICES=-1 && python -m evaluation.latency --n 30   # CPU only
"""
from __future__ import annotations

import argparse
import json
import random
import tempfile
import time
from datetime import date
from pathlib import Path

import cv2
import numpy as np

from src.audit import AuditLog
from src.registry import Registry
from src.screening import ScreeningService
from synthdocs import render_document, simulate_capture

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=30)
    a = ap.parse_args()
    import torch
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    tmp = Path(tempfile.mkdtemp())
    svc = ScreeningService(registry=Registry(tmp / 'r.sqlite'), audit=AuditLog(tmp / 'a.jsonl'))
    face = cv2.imread(str(Path(__import__('matplotlib').get_data_path()) / 'sample_data' / 'grace_hopper.jpg'))
    live = cv2.imencode('.jpg', face)[1].tobytes() if face is not None else None
    rng = random.Random(5)
    enc = lambda im: cv2.imencode('.jpg', im, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
    warm = render_document('passport', seed=1, today=date.today())
    svc.screen(enc(warm.image), live_photo=live, persist=False)
    times = []
    for i in range(a.n):
        dt = ['passport', 'visa', 'national_id', 'driving_licence', 'residence_permit'][i % 5]
        doc = render_document(dt, seed=rng.randrange(1 << 30), today=date.today())
        img, _, _, _ = simulate_capture(doc.image, random.Random(i), severity=rng.choice([0, 1, 2]))
        data = enc(img)
        t0 = time.perf_counter()
        svc.screen(data, live_photo=live if dt == 'passport' else None)
        times.append(time.perf_counter() - t0)
    t = np.array(times) * 1000
    out = dict(device=device, documents=a.n, p50_ms=round(float(np.percentile(t, 50))), p95_ms=round(float(np.percentile(t, 95))),
               mean_ms=round(float(t.mean())))
    print(json.dumps(out))
    path = ROOT / 'outputs' / 'latency.json'
    prev = json.loads(path.read_text()) if path.exists() else {}
    prev[device] = out
    path.write_text(json.dumps(prev, indent=1))


if __name__ == '__main__':
    main()
