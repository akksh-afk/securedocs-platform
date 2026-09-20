"""Train the neural MRZ line reader (CTC) and export it to ONNX.

    python -m training.mrz_data            # build data/mrz_lines first
    python -m training.train_mrz_reader --epochs 14
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from rapidfuzz.distance import Levenshtein

from src.mrz_utils import MRZ_ALPHABET
from src.nets import MRZNet

ROOT = Path(__file__).resolve().parents[1]
CHAR_TO_ID = {c: i + 1 for i, c in enumerate(MRZ_ALPHABET)}


def load_split(folder: Path, name: str):
    imgs = np.load(folder / f'{name}_images.npy', mmap_mode='r')
    labels = json.loads((folder / f'{name}_labels.json').read_text())
    return imgs, labels


def encode(labels):
    L = max(len(t) for t in labels)
    tgt = np.zeros((len(labels), L), np.int64)
    lens = np.zeros(len(labels), np.int64)
    for i, t in enumerate(labels):
        ids = [CHAR_TO_ID[c] for c in t]
        tgt[i, :len(ids)] = ids
        lens[i] = len(ids)
    return torch.from_numpy(tgt), torch.from_numpy(lens)


def normalize(x: torch.Tensor) -> torch.Tensor:
    """Per-image standardisation, identical to the runtime preprocessing."""
    x = x.float()
    m = x.mean(dim=(1, 2, 3), keepdim=True)
    s = x.std(dim=(1, 2, 3), keepdim=True)
    return (x - m) / (s + 1e-3)


def augment(x: torch.Tensor) -> torch.Tensor:
    """GPU augmentation on uint8->float batches (N,1,H,W) in 0..255."""
    N = x.shape[0]
    dev = x.device
    x = x.float()
    # Photometric: contrast, brightness, gamma.
    c = torch.empty(N, 1, 1, 1, device=dev).uniform_(0.45, 1.35)
    b = torch.empty(N, 1, 1, 1, device=dev).uniform_(-50, 50)
    mean = x.mean(dim=(2, 3), keepdim=True)
    x = ((x - mean) * c + mean + b).clamp(0, 255)
    g = torch.empty(N, 1, 1, 1, device=dev).uniform_(0.6, 1.6)
    x = 255 * (x / 255).clamp(1e-4, 1) ** g
    # Geometry: horizontal scale/shift and a little vertical shift.
    theta = torch.zeros(N, 2, 3, device=dev)
    theta[:, 0, 0] = torch.empty(N, device=dev).uniform_(0.93, 1.05)
    theta[:, 1, 1] = torch.empty(N, device=dev).uniform_(0.9, 1.1)
    theta[:, 0, 2] = torch.empty(N, device=dev).uniform_(-0.03, 0.03)
    theta[:, 1, 2] = torch.empty(N, device=dev).uniform_(-0.08, 0.08)
    grid = F.affine_grid(theta, x.shape, align_corners=False)
    x = F.grid_sample(x, grid, mode='bilinear', padding_mode='border', align_corners=False)
    # Blur on a random subset.
    blur_mask = (torch.rand(N, 1, 1, 1, device=dev) < 0.25).float()
    k = torch.tensor([1., 2., 1.], device=dev)
    k = (k[:, None] * k[None, :] / 16).view(1, 1, 3, 3)
    x = blur_mask * F.conv2d(x, k, padding=1) + (1 - blur_mask) * x
    # Horizontal camera shake along the text line: the direction that merges 0/8 and 3/8.
    klen = int(torch.randint(1, 7, (1,))) * 2 + 1
    shake = (torch.rand(N, 1, 1, 1, device=dev) < 0.2).float()
    hk = torch.full((1, 1, 1, klen), 1.0 / klen, device=dev)
    x = shake * F.conv2d(F.pad(x, (klen // 2, klen // 2, 0, 0), mode='replicate'), hk) + (1 - shake) * x
    # Sensor noise.
    sigma = torch.empty(N, 1, 1, 1, device=dev).uniform_(0, 14)
    x = x + torch.randn_like(x) * sigma
    # Glare / occlusion bars on a few samples.
    W = x.shape[-1]
    cols = torch.arange(W, device=dev).view(1, 1, 1, W)
    bw = torch.randint(8, 40, (N, 1, 1, 1), device=dev)
    bx = (torch.rand(N, 1, 1, 1, device=dev) * (W - bw)).long()
    bars = (torch.rand(N, 1, 1, 1, device=dev) < 0.12) & (cols >= bx) & (cols < bx + bw)
    x = torch.where(bars, x * 0.35 + 255 * 0.65, x)
    return x.clamp(0, 255)


def greedy_decode(logits: torch.Tensor) -> list[str]:
    ids = logits.squeeze(2).argmax(1).cpu().numpy()  # (N, T)
    out = []
    for row in ids:
        prev, s = 0, []
        for k in row:
            if k != prev and k != 0:
                s.append(MRZ_ALPHABET[k - 1])
            prev = k
        out.append(''.join(s))
    return out


@torch.no_grad()
def evaluate(model, imgs, labels, device, bs=512):
    model.eval()
    preds = []
    for i in range(0, len(imgs), bs):
        x = torch.from_numpy(np.ascontiguousarray(imgs[i:i + bs]))[:, None].to(device)
        with torch.autocast('cuda', dtype=torch.float16, enabled=device.type == 'cuda'):
            logits = model(normalize(x))
        preds.extend(greedy_decode(logits.float()))
    exact = float(np.mean([p == t for p, t in zip(preds, labels)]))
    cer = sum(Levenshtein.distance(p, t) for p, t in zip(preds, labels)) / sum(len(t) for t in labels)
    model.train()
    return exact, cer


def export_onnx(model, path: Path):
    model = model.eval().cpu()
    dummy = torch.randn(1, 1, 32, 512)
    torch.onnx.export(model, dummy, str(path), input_names=['line'], output_names=['logits'], opset_version=17,
                      dynamo=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(ROOT / 'data' / 'mrz_lines'))
    ap.add_argument('--epochs', type=int, default=14)
    ap.add_argument('--bs', type=int, default=256)
    ap.add_argument('--lr', type=float, default=2e-3)
    ap.add_argument('--out', default=str(ROOT / 'models'))
    ap.add_argument('--init', default='')
    a = ap.parse_args()
    torch.manual_seed(0)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    data = Path(a.data)
    tr_imgs, tr_labels = load_split(data, 'train')
    print('loading train images into RAM:', tr_imgs.shape, flush=True)
    tr_x = torch.from_numpy(np.ascontiguousarray(tr_imgs))
    tr_t, tr_l = encode(tr_labels)
    val = {name: load_split(data, name) for name in ('val_seen', 'val_holdout') if (data / f'{name}_labels.json').exists()}

    model = MRZNet().to(device)
    if a.init:
        model.load_state_dict(torch.load(a.init, map_location=device))
    print('params:', sum(p.numel() for p in model.parameters()) / 1e6, 'M', flush=True)
    steps_per_epoch = len(tr_x) // a.bs
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=a.epochs * steps_per_epoch, pct_start=0.15)
    scaler = torch.amp.GradScaler(enabled=device.type == 'cuda')
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    best, history = -1.0, []
    for epoch in range(a.epochs):
        t0 = time.time()
        perm = torch.randperm(len(tr_x))
        run = 0.0
        for step in range(steps_per_epoch):
            idx = perm[step * a.bs:(step + 1) * a.bs]
            x = tr_x[idx][:, None].to(device, non_blocking=True)
            tgt, lens = tr_t[idx].to(device), tr_l[idx].to(device)
            with torch.autocast('cuda', dtype=torch.float16, enabled=device.type == 'cuda'):
                logits = model(normalize(augment(x)))
            logp = F.log_softmax(logits.float().squeeze(2), dim=1).permute(2, 0, 1)  # (T, N, C)
            in_lens = torch.full((logp.shape[1],), logp.shape[0], dtype=torch.long, device=device)
            loss = F.ctc_loss(logp, tgt, in_lens, lens, blank=0, zero_infinity=True)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            scaler.step(opt); scaler.update(); sched.step()
            run = 0.98 * run + 0.02 * loss.item() if step else loss.item()
            if step % 200 == 0:
                print(f'epoch {epoch} step {step}/{steps_per_epoch} loss {run:.4f}', flush=True)
        metrics = {name: evaluate(model, im, lab, device) for name, (im, lab) in val.items()}
        history.append(dict(epoch=epoch, loss=run, secs=round(time.time() - t0), **{f'{k}_exact': v[0] for k, v in metrics.items()},
                            **{f'{k}_cer': v[1] for k, v in metrics.items()}))
        print(json.dumps(history[-1]), flush=True)
        score = metrics['val_seen'][0] if 'val_seen' in metrics else -run
        if score > best:
            best = score
            torch.save(model.state_dict(), out / 'mrz_reader.pt')
    model.load_state_dict(torch.load(out / 'mrz_reader.pt', map_location=device))
    try:
        export_onnx(model, out / 'mrz_reader.onnx')
        print('exported', out / 'mrz_reader.onnx')
    except Exception as e:  # the optional `onnx` package enables torch-free deployment
        print('ONNX export skipped:', e)
    (out / 'mrz_reader_training.json').write_text(json.dumps(dict(history=history, alphabet=MRZ_ALPHABET,
                                                                  input=[1, 32, 512], blank=0), indent=1))


if __name__ == '__main__':
    main()
