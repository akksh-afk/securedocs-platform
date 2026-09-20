"""Train the forgery localisation network (U-Net with SRM noise-residual inputs).

    python -m training.tamper_data
    python -m training.train_tamper --epochs 30
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from src.nets import SegUNet

ROOT = Path(__file__).resolve().parents[1]
CROP = 384


def read_split(folder: Path, split: str) -> list[dict]:
    return [json.loads(l) for l in (folder / f'{split}.jsonl').read_text().splitlines() if l.strip()]


class CropSet(Dataset):
    def __init__(self, folder: Path, metas: list[dict], epoch_size: int):
        self.folder, self.metas, self.epoch_size = folder, metas, epoch_size

    def __len__(self):
        return self.epoch_size

    def __getitem__(self, i):
        rng = random.Random()
        m = rng.choice(self.metas)
        img = cv2.imread(str(self.folder / 'train' / f"{m['name']}.jpg"))
        mask = cv2.imread(str(self.folder / 'train' / f"{m['name']}_mask.png"), cv2.IMREAD_GRAYSCALE)
        H, W = img.shape[:2]
        ys, xs = np.nonzero(mask[::4, ::4])
        u = rng.random()
        if len(ys) and u < 0.5:
            k = rng.randrange(len(ys))
            cy, cx = ys[k] * 4 + rng.randint(-CROP // 3, CROP // 3), xs[k] * 4 + rng.randint(-CROP // 3, CROP // 3)
        elif u < 0.8:
            # Hard negatives: centre on genuine ink (text, stamps, portrait edges) outside forged areas.
            hsv = cv2.cvtColor(img[::4, ::4], cv2.COLOR_BGR2HSV)
            ink = ((hsv[..., 2] < 110) | ((hsv[..., 1] > 70) & (hsv[..., 2] < 215))) & (mask[::4, ::4] == 0)
            iy, ix = np.nonzero(ink)
            if len(iy):
                k = rng.randrange(len(iy))
                cy, cx = iy[k] * 4, ix[k] * 4
            else:
                cy, cx = rng.randint(0, H), rng.randint(0, W)
        else:
            cy, cx = rng.randint(0, H), rng.randint(0, W)
        y0 = int(np.clip(cy - CROP // 2, 0, max(0, H - CROP)))
        x0 = int(np.clip(cx - CROP // 2, 0, max(0, W - CROP)))
        img = img[y0:y0 + CROP, x0:x0 + CROP]
        mask = mask[y0:y0 + CROP, x0:x0 + CROP]
        if img.shape[0] < CROP or img.shape[1] < CROP:
            img = cv2.copyMakeBorder(img, 0, CROP - img.shape[0], 0, CROP - img.shape[1], cv2.BORDER_REFLECT)
            mask = cv2.copyMakeBorder(mask, 0, CROP - mask.shape[0], 0, CROP - mask.shape[1], cv2.BORDER_CONSTANT, value=0)
        if rng.random() < 0.3:  # global photometric jitter only; local statistics carry the evidence
            img = cv2.convertScaleAbs(img, alpha=rng.uniform(0.8, 1.2), beta=rng.uniform(-20, 20))
        x = torch.from_numpy(img).permute(2, 0, 1).float() / 255.0
        y = torch.from_numpy((mask > 127).astype(np.float32))[None]
        return x, y


def pad32(x: np.ndarray):
    H, W = x.shape[:2]
    ph, pw = (-H) % 32, (-W) % 32
    return cv2.copyMakeBorder(x, 0, ph, 0, pw, cv2.BORDER_REFLECT), (H, W)


def image_score(prob: np.ndarray) -> float:
    """Document-level score: strongest locally-consistent evidence (7x7 mean, max)."""
    return float(cv2.blur(prob, (7, 7)).max())


def roc_auc(scores, labels) -> float:
    s = np.asarray(scores); l = np.asarray(labels).astype(bool)
    pos, neg = s[l], s[~l]
    if not len(pos) or not len(neg):
        return float('nan')
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order)); ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


@torch.no_grad()
def evaluate(model, folder: Path, metas: list[dict], device, limit: int | None = None):
    model.eval()
    scores, labels, inter, union = [], [], 0.0, 0.0
    per_op: dict[str, list[float]] = {}
    for m in metas[:limit]:
        img = cv2.imread(str(folder / 'val' / f"{m['name']}.jpg"))
        mask = cv2.imread(str(folder / 'val' / f"{m['name']}_mask.png"), cv2.IMREAD_GRAYSCALE) > 127
        padded, (H, W) = pad32(img)
        x = torch.from_numpy(padded).permute(2, 0, 1)[None].float().to(device) / 255.0
        with torch.autocast('cuda', dtype=torch.float16, enabled=device.type == 'cuda'):
            prob = torch.sigmoid(model(x).float())[0, 0, :H, :W].cpu().numpy()
        s = image_score(prob)
        scores.append(s); labels.append(m['tampered'])
        pred = prob > 0.5
        inter += float((pred & mask).sum()); union += float((pred | mask).sum())
        for op in set(m['ops']):
            per_op.setdefault(op, []).append(s)
    model.train()
    scores_np, labels_np = np.array(scores), np.array(labels)
    neg = np.sort(scores_np[~labels_np])
    out = dict(auc=roc_auc(scores, labels), pixel_iou=inter / max(union, 1.0))
    for fpr in (0.01, 0.03, 0.05):
        thr = float(neg[min(len(neg) - 1, int(np.ceil(len(neg) * (1 - fpr))))]) if len(neg) else 0.5
        out[f'recall_at_fpr{int(fpr * 100)}'] = float((scores_np[labels_np] > thr).mean()) if labels_np.any() else float('nan')
        out[f'threshold_at_fpr{int(fpr * 100)}'] = thr
        if fpr == 0.03:
            out['recall_by_op_at_fpr3'] = {op: round(float((np.array(v) > thr).mean()), 3) for op, v in sorted(per_op.items())}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(ROOT / 'data' / 'tamper'))
    ap.add_argument('--epochs', type=int, default=30)
    ap.add_argument('--bs', type=int, default=16)
    ap.add_argument('--lr', type=float, default=1e-3)
    ap.add_argument('--steps', type=int, default=400, help='steps per epoch')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--out', default=str(ROOT / 'models'))
    a = ap.parse_args()
    torch.manual_seed(0)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    folder = Path(a.data)
    train_metas, val_metas = read_split(folder, 'train'), read_split(folder, 'val')
    loader = DataLoader(CropSet(folder, train_metas, a.steps * a.bs), batch_size=a.bs, num_workers=a.workers,
                        pin_memory=True, persistent_workers=True, drop_last=True)
    model = SegUNet(1, base=24, forensic=True).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=a.epochs * a.steps, pct_start=0.1)
    scaler = torch.amp.GradScaler(enabled=device.type == 'cuda')
    pos_weight = torch.tensor([3.0], device=device)
    out = Path(a.out); out.mkdir(exist_ok=True)
    best, history = -1.0, []
    for epoch in range(a.epochs):
        t0, run = time.time(), 0.0
        for step, (x, y) in enumerate(loader):
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            with torch.autocast('cuda', dtype=torch.float16, enabled=device.type == 'cuda'):
                logits = model(x)
            logits = logits.float()
            bce = F.binary_cross_entropy_with_logits(logits, y, pos_weight=pos_weight)
            p = torch.sigmoid(logits)
            dice = 1 - (2 * (p * y).sum((1, 2, 3)) + 1) / (p.sum((1, 2, 3)) + y.sum((1, 2, 3)) + 1)
            loss = bce + dice.mean()
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            scaler.step(opt); scaler.update(); sched.step()
            run = 0.98 * run + 0.02 * loss.item() if step else loss.item()
            if step % 100 == 0:
                print(f'epoch {epoch} step {step}/{a.steps} loss {run:.4f}', flush=True)
        metrics = evaluate(model, folder, val_metas, device, limit=None if epoch % 5 == 4 or epoch == a.epochs - 1 else 250)
        history.append(dict(epoch=epoch, loss=round(run, 4), secs=round(time.time() - t0), **metrics))
        print(json.dumps(history[-1]), flush=True)
        score = metrics['auc'] + metrics['pixel_iou']
        if score > best:
            best = score
            torch.save(model.state_dict(), out / 'tamper.pt')
    model.load_state_dict(torch.load(out / 'tamper.pt', map_location=device))
    final = evaluate(model, folder, val_metas, device)
    calib = dict(image_threshold=final['threshold_at_fpr3'], region_threshold=0.5, target_fpr=0.03, val_metrics=final,
                 note='Thresholds calibrated on synthetic validation documents; recalibrate on real data before deployment.')
    (out / 'tamper_calibration.json').write_text(json.dumps(calib, indent=1))
    (out / 'tamper_training.json').write_text(json.dumps(history, indent=1))
    print('final', json.dumps(final))


if __name__ == '__main__':
    main()
