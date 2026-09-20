"""Train the capture advisor: predicts from capture-quality features whether the MRZ will be read
correctly, so the officer is asked to recapture before an unreliable image is screened.

Labels come from running the real MRZ reader on synthetic captures under random conditions.

    python -m training.train_capture_advisor --n 1500
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date
from pathlib import Path

import cv2
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from src.capture_advisor import FEATURES, capture_features
from src.document import locate_document, rectify
from src.mrz_reader import read_mrz
from synthdocs import render_document, simulate_capture

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=1500)
    a = ap.parse_args()
    rng = random.Random(99)
    X, y, conds = [], [], []
    for i in range(a.n):
        dt = rng.choice(['passport', 'visa', 'national_id', 'residence_permit'])
        doc = render_document(dt, seed=rng.randrange(1 << 30), today=date(2026, 9, 16))
        severity = rng.choice([0, 1, 2, 3, 3, 4])
        img, _, _, meta = simulate_capture(doc.image, random.Random(rng.randrange(1 << 30)), severity=severity)
        quad = locate_document(img)
        X.append([capture_features(img, quad)[k] for k in FEATURES])
        rect, _ = rectify(img, quad)
        r = read_mrz(rect)
        if not r.mrz.overall_check_digit_valid:
            alt = read_mrz(img)
            r = alt if alt.confidence > r.confidence else r
        y.append(int(r.mrz.valid_format and r.mrz.normalized_lines == doc.mrz_lines))
        conds.append(meta['conditions'])
        if i % 100 == 0:
            print(f'{i}/{a.n} success rate so far {np.mean(y):.2f}', flush=True)
    X, y = np.array(X, np.float32), np.array(y)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=0, stratify=y)
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0)
    clf.fit(Xtr, ytr)
    p = clf.predict_proba(Xte)[:, 1]
    metrics = dict(samples=len(y), read_success_rate=round(float(y.mean()), 3), test_auc=round(float(roc_auc_score(yte, p)), 4))
    # Operating point: flag for recapture when predicted success < 0.5.
    flag = p < 0.5
    metrics['recapture_flag_rate'] = round(float(flag.mean()), 3)
    metrics['failures_caught'] = round(float(flag[yte == 0].mean()), 3) if (yte == 0).any() else None
    metrics['good_captures_flagged'] = round(float(flag[yte == 1].mean()), 3)
    clf.fit(X, y)
    joblib.dump(dict(model=clf, features=FEATURES, metrics=metrics), ROOT / 'models' / 'capture_advisor.joblib')
    (ROOT / 'models' / 'capture_advisor.json').write_text(json.dumps(metrics, indent=1))
    print(json.dumps(metrics, indent=1))


if __name__ == '__main__':
    main()
