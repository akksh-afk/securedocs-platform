"""Fit the document-level forgery decision on top of the localisation heatmap.

A single hottest pixel is a fragile document score; this fits a logistic model on heatmap
statistics from the validation split and reports held-out performance on the test split.

    python -m training.fit_tamper_aggregator
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.tampering import heatmap_features, tamper_heatmap, HEATMAP_FEATURES
from training.train_tamper import read_split

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'tamper'


def features(split: str):
    X, y, ops = [], [], []
    for m in read_split(DATA, split):
        img = cv2.imread(str(DATA / split / f"{m['name']}.jpg"))
        f = heatmap_features(tamper_heatmap(img))
        X.append([f[k] for k in HEATMAP_FEATURES]); y.append(int(m['tampered'])); ops.append(m['ops'])
    return np.array(X, np.float32), np.array(y), ops


def at_fpr(scores, y, fpr):
    neg = np.sort(scores[y == 0])
    thr = float(neg[min(len(neg) - 1, int(np.ceil(len(neg) * (1 - fpr))))])
    return thr, float((scores[y == 1] > thr).mean())


def main():
    Xv, yv, _ = features('val')
    Xt, yt, ops_t = features('test')
    clf = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000, class_weight='balanced'))
    clf.fit(Xv, yv)
    pv, pt = clf.predict_proba(Xv)[:, 1], clf.predict_proba(Xt)[:, 1]
    thr, _ = at_fpr(pv, yv, 0.03)
    max_idx = HEATMAP_FEATURES.index('max_blur7')
    base_thr, _ = at_fpr(Xv[:, max_idx], yv, 0.03)
    by_op = {}
    for o, flag, lab in zip(ops_t, pt > thr, yt):
        for op in set(o):
            by_op.setdefault(op, []).append(bool(flag))
    report = dict(
        features=HEATMAP_FEATURES,
        test_documents=int(len(yt)),
        aggregator=dict(test_auc=round(float(roc_auc_score(yt, pt)), 4), threshold=round(thr, 4),
                        test_recall=round(float((pt[yt == 1] > thr).mean()), 3),
                        test_false_alarm_rate=round(float((pt[yt == 0] > thr).mean()), 3),
                        test_recall_by_type={k: round(float(np.mean(v)), 3) for k, v in sorted(by_op.items())}),
        max_pixel_baseline=dict(test_auc=round(float(roc_auc_score(yt, Xt[:, max_idx])), 4),
                                test_recall=round(float((Xt[yt == 1, max_idx] > base_thr).mean()), 3),
                                test_false_alarm_rate=round(float((Xt[yt == 0, max_idx] > base_thr).mean()), 3)),
        note='Threshold chosen for a 3% false-alarm rate on the validation split; figures are on the unseen test split.')
    joblib.dump(dict(model=clf, features=HEATMAP_FEATURES, threshold=thr), ROOT / 'models' / 'tamper_aggregator.joblib')
    (ROOT / 'models' / 'tamper_aggregator.json').write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
