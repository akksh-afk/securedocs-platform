"""Forgery operations with ground-truth masks.

Covers the problem statement's use cases: photo replacement, text manipulation (including
dates), stamp forgery (cloned or fabricated stamps) and MRZ alteration. Each operation mixes
crude and skilled variants so a detector cannot shortcut on one artefact.
"""
from __future__ import annotations

import random
import string
from datetime import date, timedelta

import cv2
import numpy as np
from PIL import Image, ImageDraw

from src.mrz_utils import check_digit
from . import fonts
from .identity import ENTRIES, GIVEN, SURNAMES, VISA_TYPES
from .portraits import portrait
from .render import SynthDoc, _stamp, blend_ink, fmt_date

TAMPER_TYPES = ['text_replace', 'date_replace', 'photo_replace', 'stamp_clone', 'stamp_forge', 'mrz_edit',
                'glyph_copy_move', 'legacy_patch']
TEXT_FIELDS = ['document_number', 'surname', 'given_names', 'nationality', 'sex', 'visa_type', 'number_of_entries',
               'duration_of_stay_days', 'passport_number', 'permit_type', 'licence_categories']
DATE_FIELDS = ['date_of_birth', 'date_of_expiry', 'valid_from', 'date_of_issue']


def _pad_box(box, pad, shape):
    H, W = shape[:2]
    x0, y0, x1, y1 = box
    return max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)


def _mark(doc: SynthDoc, box=None, mask=None, dilate: int = 2):
    if mask is None:
        mask = np.zeros(doc.image.shape[:2], np.uint8)
        x0, y0, x1, y1 = box
        mask[y0:y1, x0:x1] = 255
    if dilate:
        mask = cv2.dilate(mask, np.ones((dilate * 2 + 1, dilate * 2 + 1), np.uint8))
    doc.tamper_mask = np.maximum(doc.tamper_mask, mask)


def _ring_median(img, box):
    x0, y0, x1, y1 = box
    H, W = img.shape[:2]
    outer = img[max(0, y0 - 4):min(H, y1 + 4), max(0, x0 - 4):min(W, x1 + 4)].reshape(-1, 3)
    return np.median(outer, axis=0).astype(np.uint8)


def _erase(doc: SynthDoc, box, rng: random.Random, method: str | None = None) -> str:
    img = doc.image
    x0, y0, x1, y1 = box
    method = method or rng.choice(['flat', 'flat', 'inpaint', 'inpaint', 'background', 'clone'])
    if method == 'flat':
        img[y0:y1, x0:x1] = _ring_median(img, box)
    elif method == 'inpaint':
        roi = img[y0:y1, x0:x1]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        bg = cv2.medianBlur(gray, 21)
        ink = ((bg.astype(np.int16) - gray) > 25).astype(np.uint8) * 255
        ink = cv2.dilate(ink, np.ones((5, 5), np.uint8))
        full = np.zeros(img.shape[:2], np.uint8); full[y0:y1, x0:x1] = ink
        cv2.inpaint(img, full, rng.choice([3, 5]), rng.choice([cv2.INPAINT_TELEA, cv2.INPAINT_NS]), dst=img)
    elif method == 'background' and doc.background is not None:
        img[y0:y1, x0:x1] = doc.background[y0:y1, x0:x1]
        if rng.random() < 0.5:
            img[y0:y1, x0:x1] = cv2.GaussianBlur(img[y0:y1, x0:x1], (3, 3), 0.8)
    else:  # clone background texture from a nearby text-free strip
        h, w = y1 - y0, x1 - x0
        H, W = img.shape[:2]
        for _ in range(20):
            sx, sy = rng.randint(0, max(0, W - w - 1)), rng.randint(0, max(0, H - h - 1))
            if abs(sy - y0) > h * 2 and doc.tamper_mask[sy:sy + h, sx:sx + w].max() == 0:
                img[y0:y1, x0:x1] = img[sy:sy + h, sx:sx + w].copy()
                break
        else:
            img[y0:y1, x0:x1] = _ring_median(img, box); method = 'flat'
    return method


def _recompress_region(img, box, rng):
    x0, y0, x1, y1 = box
    if x1 - x0 < 4 or y1 - y0 < 4:
        return
    q = rng.randint(35, 80)
    ok, enc = cv2.imencode('.jpg', img[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, q])
    if ok:
        img[y0:y1, x0:x1] = cv2.imdecode(enc, cv2.IMREAD_COLOR)


def _render_text(doc: SynthDoc, xy, text, font_name, size, color, rng, antialias=True):
    img = doc.image
    x, y = xy
    font = fonts.load(font_name, size)
    bb = font.getbbox(text)
    w, h = bb[2] + 8, bb[3] + 8
    layer = Image.new('L', (max(1, w), max(1, h)), 0)
    ImageDraw.Draw(layer).text((0, 0), text, font=font, fill=255)
    a = np.asarray(layer, np.float32) / 255.0
    if not antialias:
        a = (a > 0.5).astype(np.float32)
    H, W = img.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x) + a.shape[1]), min(H, int(y) + a.shape[0])
    a = a[y0 - int(y):y1 - int(y), x0 - int(x):x1 - int(x), None]
    roi = img[y0:y1, x0:x1].astype(np.float32)
    col = np.array(color[::-1], np.float32)  # RGB -> BGR
    img[y0:y1, x0:x1] = np.clip(roi * (1 - a) + col * a, 0, 255).astype(np.uint8)
    return (x0 + bb[0], y0 + bb[1], x0 + bb[2], y0 + bb[3])


def _new_value(field: str, old: str, doc: SynthDoc, rng: random.Random) -> str:
    if field in ('document_number', 'passport_number'):
        chars = list(old)
        for i in rng.sample(range(len(chars)), min(len(chars), rng.randint(1, 3))):
            pool = string.digits if chars[i].isdigit() else string.ascii_uppercase
            chars[i] = rng.choice(pool.replace(chars[i], ''))
        return ''.join(chars)
    if field == 'surname':
        return rng.choice([s for s in SURNAMES if s != old])
    if field == 'given_names':
        return rng.choice([g for g in GIVEN if g != old])
    if field == 'sex':
        return {'M': 'F', 'F': 'M'}.get(old, 'M')
    if field == 'nationality':
        return rng.choice(['UTO', 'IND', 'GBR', 'USA', 'DEU'])
    if field == 'visa_type':
        return rng.choice([v for v in VISA_TYPES if v != old])
    if field == 'number_of_entries':
        return rng.choice([e for e in ENTRIES if e != old])
    if field == 'duration_of_stay_days':
        return f'{rng.choice([90, 180, 365, 730])} DAYS'
    return ''.join(rng.choice(string.ascii_uppercase) if c.isalpha() else rng.choice(string.digits) if c.isdigit() else c
                   for c in old)


def _new_date_text(field: str, doc: SynthDoc, old_text: str, rng: random.Random) -> str:
    d = doc.fields.get(field)
    if not isinstance(d, date):
        return _new_value(field, old_text, doc, rng)
    if field == 'date_of_birth':
        nd = d - timedelta(days=rng.choice([365 * rng.randint(2, 15), rng.randint(30, 900)]))
    else:
        nd = d + timedelta(days=rng.choice([365 * rng.randint(1, 8), rng.randint(40, 700)]))
    # Keep the printed pattern: find which strftime pattern reproduces the old text.
    for pattern in ('%d %b %Y', '%d/%m/%Y', '%d.%m.%Y', '%Y-%m-%d', '%d-%m-%Y'):
        if fmt_date(d, pattern) == old_text:
            return fmt_date(nd, pattern)
    return fmt_date(nd, '%d %b %Y')


def text_replace(doc: SynthDoc, rng: random.Random, fields: list[str] | None = None) -> bool:
    candidates = [f for f in (fields or TEXT_FIELDS) if f in doc.boxes]
    if not candidates:
        return False
    fld = rng.choice(candidates)
    old = doc.viz_text[fld]
    new = _new_date_text(fld, doc, old, rng) if fld in DATE_FIELDS else _new_value(fld, old, doc, rng)
    box = _pad_box(doc.boxes[fld], rng.randint(2, 7), doc.image.shape)
    font_name, size, color = doc.fonts_used[fld]
    method = _erase(doc, box, rng)
    # A perfect background erase plus identical re-rendering leaves no pixel evidence at all.
    # Such edits are caught by the MRZ/printed-zone checks instead of being labelled for the pixel model.
    skilled = rng.random() < 0.5 and method != 'background'
    if not skilled:
        font_name = rng.choice(fonts.available(fonts.VIZ_FONTS))
        size = int(size * rng.uniform(0.85, 1.15))
        color = tuple(int(np.clip(c + rng.randint(-30, 30), 0, 255)) for c in color)
    x0, y0 = doc.boxes[fld][0], doc.boxes[fld][1]
    font = fonts.load(font_name, size)
    tb = font.getbbox(new)
    nb = _render_text(doc, (x0 - tb[0] + rng.randint(-3, 3), y0 - tb[1] + rng.randint(-3, 3)), new, font_name, size,
                      color, rng, antialias=skilled or rng.random() < 0.6)
    region = (min(box[0], nb[0]), min(box[1], nb[1]), max(box[2], nb[2]), max(box[3], nb[3]))
    region = _pad_box(region, 2, doc.image.shape)
    if rng.random() < 0.35 or method == 'background':
        _recompress_region(doc.image, region, rng)
    _mark(doc, region)
    doc.viz_text[fld] = new
    doc.tamper_ops.append(dict(type='date_replace' if fld in DATE_FIELDS else 'text_replace', field=fld, old=old,
                               new=new, erase=method, skilled=skilled, box=list(map(int, region))))
    return True


def date_replace(doc: SynthDoc, rng: random.Random) -> bool:
    return text_replace(doc, rng, [f for f in DATE_FIELDS if f in doc.boxes])


def photo_replace(doc: SynthDoc, rng: random.Random) -> bool:
    if doc.portrait_box is None:
        return False
    x0, y0, x1, y1 = doc.portrait_box
    grow = rng.choice([0, 0, 4, 10])
    dx, dy = rng.randint(-6, 6), rng.randint(-6, 6)
    bx0, by0 = max(0, x0 - grow + dx), max(0, y0 - grow + dy)
    bx1, by1 = min(doc.image.shape[1], x1 + grow + dx), min(doc.image.shape[0], y1 + grow + dy)
    face = portrait(rng.randrange(1 << 30), bx1 - bx0, by1 - by0, prefer_real=0.3)
    style = rng.choice(['color', 'blur', 'sharp', 'feather', 'jpeg', 'misaligned'])
    if style == 'misaligned' or rng.random() < 0.5:
        grow = max(grow, rng.choice([4, 8, 14]))
        dx, dy = rng.choice([-1, 1]) * rng.randint(5, 14), rng.choice([-1, 1]) * rng.randint(3, 10)
        bx0, by0 = max(0, x0 - grow + dx), max(0, y0 - grow + dy)
        bx1, by1 = min(doc.image.shape[1], x1 + grow + dx), min(doc.image.shape[0], y1 + grow + dy)
        face = portrait(rng.randrange(1 << 30), bx1 - bx0, by1 - by0, prefer_real=0.3)
    if style == 'color':
        face = np.clip(face.astype(np.float32) * np.array([rng.uniform(0.8, 1.2) for _ in range(3)]), 0, 255).astype(np.uint8)
    elif style == 'blur':
        face = cv2.GaussianBlur(face, (0, 0), rng.uniform(1.2, 2.5))
    elif style == 'sharp':
        face = cv2.addWeighted(face, 1.8, cv2.GaussianBlur(face, (0, 0), 2), -0.8, 0)
    elif style == 'jpeg':
        ok, enc = cv2.imencode('.jpg', face, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(20, 55)])
        face = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    roi = doc.image[by0:by1, bx0:bx1]
    if style == 'feather':
        m = np.zeros(face.shape[:2], np.float32)
        cv2.rectangle(m, (6, 6), (m.shape[1] - 7, m.shape[0] - 7), 1.0, -1)
        m = cv2.GaussianBlur(m, (0, 0), 4)[..., None]
        face = (face * m + roi * (1 - m)).astype(np.uint8)
    doc.image[by0:by1, bx0:bx1] = face
    if rng.random() < 0.5:
        cv2.rectangle(doc.image, (bx0, by0), (bx1 - 1, by1 - 1), (90, 90, 90), rng.choice([1, 2, 3]))
    _mark(doc, (bx0, by0, bx1, by1))
    doc.tamper_ops.append(dict(type='photo_replace', style=style, box=[bx0, by0, bx1, by1]))
    return True


def _free_spot(doc: SynthDoc, w: int, h: int, rng: random.Random):
    H, W = doc.image.shape[:2]
    for _ in range(30):
        x, y = rng.randint(int(W * 0.3), max(int(W * 0.3) + 1, W - w - 10)), rng.randint(int(H * 0.12), max(int(H * 0.12) + 1, int(H * 0.72) - h))
        if doc.tamper_mask[y:y + h, x:x + w].max() == 0:
            return x, y
    return None


def stamp_clone(doc: SynthDoc, rng: random.Random) -> bool:
    H, W = doc.image.shape[:2]
    if not doc.stamp_boxes:
        s = int(W * rng.uniform(0.13, 0.18))
        alpha, ink = _stamp(s, rng)
        doc.stamp_boxes.append(blend_ink(doc.image, alpha, ink, int(W * rng.uniform(0.55, 0.75)), int(H * rng.uniform(0.25, 0.5))))
    sx0, sy0, sx1, sy1 = rng.choice(doc.stamp_boxes)
    w, h = sx1 - sx0, sy1 - sy0
    spot = _free_spot(doc, w, h, rng)
    if spot is None or w < 10 or h < 10:
        return False
    dx, dy = spot
    src = doc.image[sy0:sy1, sx0:sx1].astype(np.float32)
    bg = cv2.medianBlur(doc.image[sy0:sy1, sx0:sx1], 31).astype(np.float32)
    ratio = np.clip(src / np.maximum(bg, 1), 0, 1)
    ink = (ratio.mean(axis=2) < 0.86).astype(np.uint8) * 255
    angle, scale = rng.uniform(-12, 12), rng.uniform(0.95, 1.05)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    ratio = cv2.warpAffine(ratio, M, (w, h), borderValue=(1, 1, 1))
    ink = cv2.warpAffine(ink, M, (w, h))
    dst = doc.image[dy:dy + h, dx:dx + w].astype(np.float32)
    ink_only = rng.random() < 0.5
    if ink_only:  # multiply transfer: only the ink moves
        out = dst * ratio
    else:  # rectangular copy-move, carries the source background along
        out = cv2.warpAffine(src, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    doc.image[dy:dy + h, dx:dx + w] = np.clip(out, 0, 255).astype(np.uint8)
    m = np.zeros((H, W), np.uint8)
    m[dy:dy + h, dx:dx + w] = cv2.dilate(ink, np.ones((7, 7), np.uint8)) if ink_only else 255
    _mark(doc, mask=m, dilate=3)
    doc.tamper_ops.append(dict(type='stamp_clone', source=[sx0, sy0, sx1, sy1], box=[dx, dy, dx + w, dy + h]))
    return True


def stamp_forge(doc: SynthDoc, rng: random.Random) -> bool:
    H, W = doc.image.shape[:2]
    s = int(W * rng.uniform(0.11, 0.18))
    spot = _free_spot(doc, s, s, rng)
    if spot is None:
        return False
    x, y = spot
    alpha, ink = _stamp(s, rng, rng.choice(['ADMITTED', 'EXTENDED STAY', 'VISA VALID', 'ENTRY PERMITTED']))
    # Digitally added stamps differ physically from pressed ink: opaque colour hiding the print,
    # flat untextured ink, a pasted scan on its own paper patch, or a low-resolution image.
    style = rng.choice(['opaque', 'flat_ink', 'pasted_patch', 'lowres'])
    if style == 'flat_ink':
        alpha = (alpha > 0.15).astype(np.float32) * rng.uniform(0.8, 1.0)
    if style == 'lowres':
        f = rng.uniform(0.25, 0.45)
        alpha = cv2.resize(cv2.resize(alpha, None, fx=f, fy=f, interpolation=cv2.INTER_AREA), (s, s), interpolation=cv2.INTER_LINEAR)
    roi = doc.image[y:y + s, x:x + s].astype(np.float32)
    a = alpha[:roi.shape[0], :roi.shape[1], None]
    ink_c = np.array(ink, np.float32)
    if style in ('opaque', 'flat_ink'):  # normal blend hides the underlying print instead of multiplying
        out = roi * (1 - a) + ink_c * a
    elif style == 'pasted_patch':
        paper = np.full_like(roi, np.median(roi.reshape(-1, 3), axis=0)) + np.float32(rng.uniform(-14, 14))
        out = paper * (1 - a) + paper * (ink_c / 255.0) * a
    else:
        out = roi * (1 - a) + roi * (ink_c / 255.0) * a
    doc.image[y:y + s, x:x + s] = np.clip(out, 0, 255).astype(np.uint8)
    m = np.zeros((H, W), np.uint8)
    if style == 'pasted_patch':
        m[y:y + s, x:x + s] = 255
    else:
        m[y:y + s, x:x + s][alpha[:roi.shape[0], :roi.shape[1]] > 0.08] = 255
    _mark(doc, mask=m, dilate=4)
    doc.stamp_boxes.append((x, y, x + s, y + s))
    doc.tamper_ops.append(dict(type='stamp_forge', style=style, box=[x, y, x + s, y + s]))
    return True


_MRZ_FIELD_SPANS = {'TD3': {'document_number': (1, 0, 9, 9), 'date_of_birth': (1, 13, 19, 19), 'date_of_expiry': (1, 21, 27, 27)},
                    'MRVA': {'document_number': (1, 0, 9, 9), 'date_of_birth': (1, 13, 19, 19), 'date_of_expiry': (1, 21, 27, 27)},
                    'TD1': {'document_number': (0, 5, 14, 14), 'date_of_birth': (1, 0, 6, 6), 'date_of_expiry': (1, 8, 14, 14)}}


def mrz_edit(doc: SynthDoc, rng: random.Random, field: str | None = None, new_value: str | None = None) -> bool:
    spans = _MRZ_FIELD_SPANS.get(doc.mrz_format or '')
    if not spans or not doc.mrz_render:
        return False
    field = field or rng.choice(list(spans))
    li, a, b, cdi = spans[field]
    lines = list(doc.mrz_lines)
    old = lines[li][a:b]
    if new_value is None:
        chars = list(old)
        for i in rng.sample(range(len(chars)), rng.randint(1, 2)):
            chars[i] = rng.choice(string.digits.replace(chars[i], '')) if chars[i].isdigit() else rng.choice(string.ascii_uppercase)
        new_value = ''.join(chars)
    new_line = lines[li][:a] + new_value + lines[li][b:]
    fix_cd = rng.random() < 0.6  # a skilled forger recomputes the field check digit
    if fix_cd:
        new_line = new_line[:cdi] + check_digit(new_value) + new_line[cdi + 1:]
    changed = [i for i in range(len(new_line)) if new_line[i] != lines[li][i]]
    if not changed:
        return False
    r = doc.mrz_render
    font = fonts.load(r['font'], r['size'])
    y = r['y0'] + li * r['line_gap']
    cap = font.getbbox('H')
    union = None
    method = rng.choice(['flat', 'inpaint', 'background'])
    for ci in changed:
        cx0 = int(r['x0'] + ci * r['pitch']); cx1 = int(r['x0'] + (ci + 1) * r['pitch'])
        cell = _pad_box((cx0, int(y + cap[1]), cx1, int(y + cap[3])), 3, doc.image.shape)
        _erase(doc, cell, rng, method)
        if method == 'background':
            _recompress_region(doc.image, cell, rng)
        ch = new_line[ci]
        bb = font.getbbox(ch)
        cx = r['x0'] + ci * r['pitch'] + (r['pitch'] - (bb[2] - bb[0])) / 2 - bb[0] + rng.uniform(-1.5, 1.5)
        _render_text(doc, (cx, y + rng.uniform(-1.5, 1.5)), ch, r['font'], r['size'], r['color'], rng)
        union = cell if union is None else (min(union[0], cell[0]), min(union[1], cell[1]), max(union[2], cell[2]), max(union[3], cell[3]))
        _mark(doc, cell)
    lines[li] = new_line
    doc.mrz_lines = lines
    doc.tamper_ops.append(dict(type='mrz_edit', field=field, old=old, new=new_value, check_digit_recomputed=fix_cd,
                               box=list(map(int, union))))
    return True


def glyph_copy_move(doc: SynthDoc, rng: random.Random) -> bool:
    """Replace one digit of a field with a digit glyph cut from elsewhere on the same document."""
    fields = [f for f in DATE_FIELDS + ['document_number'] if f in doc.boxes and any(c.isdigit() for c in doc.viz_text[f])]
    if len(fields) < 2:
        return False
    tgt, src = rng.sample(fields, 2)
    font_name, size, _ = doc.fonts_used[tgt]
    sfont_name, ssize, _ = doc.fonts_used[src]
    font, sfont = fonts.load(font_name, size), fonts.load(sfont_name, ssize)

    def glyph_boxes(fld, fnt):
        text = doc.viz_text[fld]
        x0, y0 = doc.boxes[fld][0], doc.boxes[fld][1]
        base = fnt.getbbox(text)
        out = []
        for i, ch in enumerate(text):
            if ch.isdigit():
                left = fnt.getlength(text[:i]); right = fnt.getlength(text[:i + 1])
                out.append((i, ch, (int(x0 - base[0] + left), doc.boxes[fld][1], int(x0 - base[0] + right), doc.boxes[fld][3])))
        return out

    tg, sg = glyph_boxes(tgt, font), glyph_boxes(src, sfont)
    options = [(t, s) for t in tg for s in sg if t[1] != s[1]]
    if not options:
        return False
    (ti, tch, tb), (si, sch, sb) = rng.choice(options)
    w, h = tb[2] - tb[0], tb[3] - tb[1]
    patch = doc.image[sb[1]:sb[3], sb[0]:sb[2]]
    if patch.size == 0 or w < 3 or h < 3:
        return False
    doc.image[tb[1]:tb[3], tb[0]:tb[2]] = cv2.resize(patch, (w, h), interpolation=cv2.INTER_LINEAR)
    region = _pad_box(tb, 2, doc.image.shape)
    _mark(doc, region)
    old = doc.viz_text[tgt]
    doc.viz_text[tgt] = old[:ti] + sch + old[ti + 1:]
    doc.tamper_ops.append(dict(type='glyph_copy_move', field=tgt, old=old, new=doc.viz_text[tgt], box=list(map(int, region))))
    return True


def legacy_patch(doc: SynthDoc, rng: random.Random) -> bool:
    """The crude style used by the original sample generator: flat rectangle + Hershey text."""
    fields = [f for f in DATE_FIELDS + ['document_number', 'surname'] if f in doc.boxes]
    if not fields:
        return False
    fld = rng.choice(fields)
    x0, y0, x1, y1 = _pad_box(doc.boxes[fld], rng.randint(6, 25), doc.image.shape)
    x1 = min(doc.image.shape[1], x1 + rng.randint(0, 120))
    color = tuple(int(c) for c in _ring_median(doc.image, (x0, y0, x1, y1)) + np.array([rng.randint(-8, 8)] * 3))
    cv2.rectangle(doc.image, (x0, y0), (x1, y1), color, -1)
    old = doc.viz_text[fld]
    new = _new_date_text(fld, doc, old, rng) if fld in DATE_FIELDS else _new_value(fld, old, doc, rng)
    scale = (y1 - y0) / 55
    cv2.putText(doc.image, new, (x0 + 10, y1 - int((y1 - y0) * 0.25)), cv2.FONT_HERSHEY_SIMPLEX, scale, (20, 20, 20),
                max(1, int(scale * 2.5)), cv2.LINE_AA)
    _mark(doc, (x0, y0, x1, y1))
    doc.viz_text[fld] = new
    doc.tamper_ops.append(dict(type='legacy_patch', field=fld, old=old, new=new, box=[x0, y0, x1, y1]))
    return True


_OPS = {'text_replace': text_replace, 'date_replace': date_replace, 'photo_replace': photo_replace,
        'stamp_clone': stamp_clone, 'stamp_forge': stamp_forge, 'mrz_edit': mrz_edit,
        'glyph_copy_move': glyph_copy_move, 'legacy_patch': legacy_patch}


def apply_tamper(doc: SynthDoc, kind: str | None = None, rng: random.Random | None = None,
                 consistent_mrz: float = 0.3) -> bool:
    """Apply one forgery. With probability ``consistent_mrz`` a VIZ date/number edit is mirrored
    into the MRZ, simulating a forger who keeps both zones consistent."""
    rng = rng or random.Random()
    kind = kind or rng.choice(TAMPER_TYPES)
    ok = _OPS[kind](doc, rng)
    if ok and kind in ('text_replace', 'date_replace') and rng.random() < consistent_mrz:
        op = doc.tamper_ops[-1]
        if op['field'] in ('document_number', 'date_of_birth', 'date_of_expiry') and doc.mrz_format in _MRZ_FIELD_SPANS:
            from src.dates import parse_date
            new = op['new']
            if op['field'] != 'document_number':
                d = parse_date(new)
                new = d.strftime('%y%m%d') if d else None
            if new and len(new) == _MRZ_FIELD_SPANS[doc.mrz_format][op['field']][2] - _MRZ_FIELD_SPANS[doc.mrz_format][op['field']][1]:
                mrz_edit(doc, rng, op['field'], new)
    return ok
