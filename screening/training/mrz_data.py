"""Generate MRZ text-line training images.

Each sample is a grey 32x512 crop of one MRZ line, rendered in a random font on a
security-print background, degraded like a checkpoint capture, and cropped with the
same kind of positional slack the runtime line segmenter produces.

    python -m training.mrz_data --out data/mrz_lines --train 160000 --val 6000
"""
from __future__ import annotations

import argparse
import json
import math
import random
import string
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from src.mrz_utils import MRZ_ALPHABET, generate_mrz
from synthdocs import fonts
from synthdocs.capture import photometric, sample_conditions
from synthdocs.identity import GIVEN, SURNAMES, NATIONALITIES, random_person, rand_docno, yymmdd
from synthdocs.render import _background

LINE_H, LINE_W = 32, 512
MAX_LABEL = 44


def _random_mrz(rng: random.Random) -> list[str]:
    fmt = rng.choice(['TD3', 'TD3', 'MRVA', 'TD2', 'MRVB', 'TD1', 'TD1'])
    if rng.random() < 0.22:  # structure-free lines so the model reads glyphs, not MRZ grammar
        width = {'TD3': 44, 'MRVA': 44, 'TD2': 36, 'MRVB': 36, 'TD1': 30}[fmt]
        n = 3 if fmt == 'TD1' else 2
        lines = []
        for _ in range(n):
            chars = []
            while len(chars) < width:
                if rng.random() < 0.18:
                    chars.extend('<' * rng.randint(1, 12))
                else:
                    chars.append(rng.choice(string.ascii_uppercase + string.digits))
            lines.append(''.join(chars[:width]))
        return lines
    p = random_person(rng, date(2026, 9, 16))
    sur = p.surname if rng.random() < 0.6 else ''.join(rng.choice(string.ascii_uppercase) for _ in range(rng.randint(2, 14)))
    giv = p.given_names if rng.random() < 0.6 else ' '.join(
        ''.join(rng.choice(string.ascii_uppercase) for _ in range(rng.randint(2, 10))) for _ in range(rng.randint(1, 3)))
    code = {'TD3': 'P' + rng.choice('<<<DSO'), 'TD2': rng.choice(['I<', 'AC', 'ID', 'P<']),
            'MRVA': 'V' + rng.choice('<<ABC'), 'MRVB': 'V' + rng.choice('<<ABC'), 'TD1': rng.choice(['ID', 'I<', 'IR', 'AC', 'AR'])}[fmt]
    state = rng.choice(list(NATIONALITIES) + [''.join(rng.choice(string.ascii_uppercase) for _ in range(3))])
    opt = '' if rng.random() < 0.6 else ''.join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(rng.randint(3, 14)))
    return generate_mrz(fmt, document_code=code, issuing_state=state, document_number=rand_docno(rng),
                        nationality=rng.choice(list(NATIONALITIES)), date_of_birth=yymmdd(p.date_of_birth),
                        sex=rng.choice('MFX<'), date_of_expiry=f'{rng.randint(0, 99):02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}',
                        surname=sur, given_names=giv, optional_data=opt, optional_data_2=opt[::-1])


def render_block(lines: list[str], rng: random.Random, font_pool: list[str]):
    """Render an MRZ block; return (BGR image, per-line boxes, cap height, pitch)."""
    n = len(lines[0])
    pitch = rng.uniform(18, 34)
    font_name = rng.choice(font_pool)
    size = max(10, int(pitch * rng.uniform(1.4, 1.75)))
    font = fonts.load(font_name, size)
    cap = font.getbbox('H')
    cap_h = cap[3] - cap[1]
    gap = cap_h * rng.uniform(1.35, 1.95)
    margin_x, margin_y = int(pitch * rng.uniform(1.5, 4)), int(cap_h * rng.uniform(1.2, 3))
    W = int(n * pitch + 2 * margin_x)
    H = int((len(lines) - 1) * gap + cap_h + 2 * margin_y)
    bg = _background(W, H, rng) if rng.random() < 0.75 else np.full((H, W, 3), rng.randint(190, 250), np.uint8)
    im = Image.fromarray(cv2.cvtColor(bg, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(im)
    ink = rng.randint(0, 60)
    color = (ink, ink, ink + rng.randint(0, 30))
    stroke = 1 if rng.random() < 0.12 else 0
    zero_style = rng.choices(['plain', 'slash', 'dot', 'backslash'], weights=[0.55, 0.25, 0.12, 0.08])[0]
    squeeze = rng.uniform(0.8, 1.08) if rng.random() < 0.5 else 1.0
    boxes = []
    for li, line in enumerate(lines):
        y = margin_y + li * gap - cap[1]
        for ci, ch in enumerate(line):
            bb = font.getbbox(ch)
            gw = bb[2] - bb[0]
            cx = margin_x + ci * pitch + (pitch - gw) / 2 - bb[0] + rng.uniform(-0.6, 0.6)
            yy = y + rng.uniform(-0.5, 0.5)
            if squeeze != 1.0 and gw > 2:
                # Per-glyph width change: renders the glyph on its own layer and rescales it.
                layer = Image.new('L', (int(gw + 6), int(bb[3] + 6)), 0)
                ImageDraw.Draw(layer).text((3 - bb[0], 3), ch, font=font, fill=255, stroke_width=stroke, stroke_fill=255)
                nw = max(1, int(layer.width * squeeze))
                layer = layer.resize((nw, layer.height), Image.BILINEAR)
                im.paste(Image.new('RGB', layer.size, color), (int(margin_x + ci * pitch + (pitch - nw) / 2), int(yy - 3 + 3)), layer)
            else:
                d.text((cx, yy), ch, font=font, fill=color, stroke_width=stroke, stroke_fill=color)
            if ch == '0' and zero_style != 'plain':
                gx0, gx1 = margin_x + ci * pitch + pitch * 0.3, margin_x + ci * pitch + pitch * 0.7
                gy0, gy1 = y + cap[1] + cap_h * 0.2, y + cap[1] + cap_h * 0.8
                lw = max(1, int(cap_h * 0.09))
                if zero_style == 'slash':
                    d.line((gx0, gy1, gx1, gy0), fill=color, width=lw)
                elif zero_style == 'backslash':
                    d.line((gx0, gy0, gx1, gy1), fill=color, width=lw)
                else:
                    r_ = max(1, cap_h * 0.07)
                    cxm, cym = (gx0 + gx1) / 2, (gy0 + gy1) / 2
                    d.ellipse((cxm - r_, cym - r_, cxm + r_, cym + r_), fill=color)
        boxes.append((margin_x, margin_y + li * gap, margin_x + n * pitch, margin_y + li * gap + cap_h))
    if rng.random() < 0.25:  # printed box around the MRZ (borders end up at crop edges)
        bx0 = margin_x - rng.uniform(0.4, 1.4) * pitch
        bx1 = margin_x + n * pitch + rng.uniform(0.4, 1.4) * pitch
        d.rectangle((bx0, margin_y - cap_h * rng.uniform(0.4, 1.0), bx1, H - margin_y + cap_h * rng.uniform(0.2, 1.0)),
                    outline=tuple(int(c) + 60 for c in color), width=rng.choice([1, 2, 3]))
    img = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    if rng.random() < 0.3:  # stroke-weight variation (ink spread / thin print)
        k = np.ones((2, 2), np.uint8)
        img = cv2.erode(img, k) if rng.random() < 0.5 else cv2.dilate(img, k)
    return img, boxes, cap_h, pitch


def _degrade_geometry(img, boxes, rng):
    h, w = img.shape[:2]
    angle = rng.uniform(-2.5, 2.5)
    shear = rng.uniform(-0.08, 0.08)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    M[0, 1] += shear
    out = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    new_boxes = []
    for x0, y0, x1, y1 in boxes:
        pts = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        pts = (M[:, :2] @ pts.T).T + M[:, 2]
        new_boxes.append(pts)
    return out, new_boxes


def crop_line(img: np.ndarray, quad: np.ndarray, cap_h: float, pitch: float, rng: random.Random | None) -> np.ndarray:
    """Rectify one text line from its 4-point quad with random slack; returns grey LINE_H x LINE_W."""
    tl, tr, br, bl = quad
    if rng is not None:
        top, bot = rng.uniform(-0.12, 0.55) * cap_h, rng.uniform(-0.12, 0.55) * cap_h
        left, right = rng.uniform(-0.35, 2.2) * pitch, rng.uniform(-0.35, 2.2) * pitch
    else:
        top = bot = 0.3 * cap_h
        left = right = 0.6 * pitch
    ux = (tr - tl) / max(np.linalg.norm(tr - tl), 1e-6)
    uy = (bl - tl) / max(np.linalg.norm(bl - tl), 1e-6)
    src = np.float32([tl - ux * left - uy * top, tr + ux * right - uy * top,
                      br + ux * right + uy * bot, bl - ux * left + uy * bot])
    dst = np.float32([[0, 0], [LINE_W, 0], [LINE_W, LINE_H], [0, LINE_H]])
    M = cv2.getPerspectiveTransform(src, dst)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.warpPerspective(gray, M, (LINE_W, LINE_H), flags=cv2.INTER_AREA if np.linalg.norm(tr - tl) > LINE_W else cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)


def make_samples(seed: int, font_pool: list[str]) -> list[tuple[np.ndarray, str]]:
    rng = random.Random(seed)
    lines = _random_mrz(rng)
    img, boxes, cap_h, pitch = render_block(lines, rng, font_pool)
    img, quads = _degrade_geometry(img, boxes, rng)
    scale = rng.uniform(9, 30) / cap_h  # captured MRZ cap height in pixels
    if scale < 1:
        small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        img = cv2.resize(small, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
    img = photometric(img, rng, sample_conditions(rng, rng.choice([0, 1, 1, 2, 2, 3])), strength=rng.uniform(0.5, 1.0))
    out = []
    for q, text in zip(quads, lines):
        out.append((crop_line(img, q, cap_h, pitch, rng), text))
    return out


def _init_worker():
    # One OpenCV thread per process: otherwise every worker spawns a full thread pool and they thrash.
    cv2.setNumThreads(1)


def _worker(args):
    start, count, font_pool = args
    imgs, labels = [], []
    s = start
    while len(imgs) < count:
        for im, t in make_samples(s, font_pool):
            if len(imgs) < count:
                imgs.append(im); labels.append(t)
        s += 1
    return np.stack(imgs), labels


def build(out: Path, name: str, n: int, seed0: int, font_pool: list[str], workers: int):
    out.mkdir(parents=True, exist_ok=True)
    chunk = 2000
    jobs = [(seed0 + k * 10_000, min(chunk, n - k * chunk), font_pool) for k in range(math.ceil(n / chunk))]
    mm = np.lib.format.open_memmap(out / f'{name}_images.npy', mode='w+', dtype=np.uint8, shape=(n, LINE_H, LINE_W))
    labels = []
    pos = 0
    with Pool(workers, initializer=_init_worker) as pool:
        for arr, labs in pool.imap(_worker, jobs):
            mm[pos:pos + len(arr)] = arr
            labels.extend(labs)
            pos += len(arr)
            print(f'{name}: {pos}/{n}', flush=True)
    mm.flush()
    (out / f'{name}_labels.json').write_text(json.dumps(labels))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='data/mrz_lines')
    ap.add_argument('--train', type=int, default=160_000)
    ap.add_argument('--val', type=int, default=6_000)
    ap.add_argument('--workers', type=int, default=14)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--skip-val', action='store_true')
    a = ap.parse_args()
    train_fonts = fonts.available(fonts.MRZ_TRAIN_MONO) + fonts.available(fonts.MRZ_TRAIN_PITCHED)
    holdout = fonts.available(fonts.MRZ_HOLDOUT)
    print('train fonts:', train_fonts, '\nheld-out fonts:', holdout)
    out = Path(a.out)
    if not a.skip_val:
        build(out, 'val_seen', a.val, 900_000_000, train_fonts, a.workers)
        if holdout:
            build(out, 'val_holdout', a.val, 950_000_000, holdout, a.workers)
    build(out, 'train', a.train, a.seed, train_fonts, a.workers)


if __name__ == '__main__':
    main()
