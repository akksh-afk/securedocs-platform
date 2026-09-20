"""Render fictional identity documents with pixel-accurate annotations."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import cv2
import numpy as np
from PIL import Image, ImageDraw

from src.mrz_utils import generate_mrz
from . import fonts
from .identity import (ENTRIES, DL_CATEGORIES, NATIONALITIES, PERMIT_TYPES, PLACES, VISA_TYPES, Person,
                       document_dates, rand_docno, random_person, yymmdd)
from .portraits import portrait

DOC_TYPES = ['passport', 'visa', 'national_id', 'driving_licence', 'residence_permit']
CANVAS = {'passport': (1500, 1056), 'visa': (1440, 960), 'national_id': (1284, 810),
          'driving_licence': (1284, 810), 'residence_permit': (1284, 810)}
DATE_FORMATS = ['%d %b %Y', '%d/%m/%Y', '%d.%m.%Y', '%Y-%m-%d', '%d-%m-%Y', '%d %b %Y', '%d %b %Y']
Box = tuple[int, int, int, int]


@dataclass
class SynthDoc:
    image: np.ndarray
    doc_type: str
    fields: dict
    viz_text: dict = field(default_factory=dict)
    boxes: dict = field(default_factory=dict)
    label_boxes: dict = field(default_factory=dict)
    fonts_used: dict = field(default_factory=dict)
    mrz_lines: list = field(default_factory=list)
    mrz_format: str | None = None
    mrz_line_boxes: list = field(default_factory=list)
    mrz_render: dict = field(default_factory=dict)
    portrait_box: Box | None = None
    stamp_boxes: list = field(default_factory=list)
    background: np.ndarray | None = None
    tamper_mask: np.ndarray | None = None
    tamper_ops: list = field(default_factory=list)
    person_seed: int = 0

    @property
    def tampered(self) -> bool:
        return bool(self.tamper_ops)

    def mrz_zone(self, pad: int = 12) -> Box | None:
        if not self.mrz_line_boxes:
            return None
        xs0, ys0, xs1, ys1 = zip(*self.mrz_line_boxes)
        return (min(xs0) - pad, min(ys0) - pad, max(xs1) + pad, max(ys1) + pad)


def fmt_date(d: date, pattern: str) -> str:
    return d.strftime(pattern).upper()


# ---------------------------------------------------------------------------
# Backgrounds and security-print elements
# ---------------------------------------------------------------------------

def _background(w: int, h: int, rng: random.Random) -> np.ndarray:
    base = np.array(rng.choice([(236, 232, 214), (226, 236, 230), (234, 228, 238), (240, 236, 226), (222, 232, 240),
                                (244, 240, 230), (232, 240, 222)]), np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    grad = (xx / w * rng.uniform(-18, 18) + yy / h * rng.uniform(-18, 18))[..., None]
    img = np.clip(base[None, None, :] + grad, 0, 255)
    ink = np.array(rng.choice([(150, 170, 200), (180, 150, 170), (140, 180, 160), (190, 170, 140), (160, 160, 200)]),
                   np.float32)
    layer = np.zeros((h, w), np.float32)
    for _ in range(rng.randint(3, 6)):
        fx, fy = rng.uniform(1.5, 7) * 2 * math.pi / w, rng.uniform(1.5, 7) * 2 * math.pi / h
        phase, amp = rng.uniform(0, 6.3), rng.uniform(0.25, 0.6)
        pattern = np.sin(xx * fx + np.sin(yy * fy + phase) * rng.uniform(1, 4))
        layer += (np.abs(pattern) < 0.045).astype(np.float32) * amp
    cx, cy = rng.uniform(0.3, 0.8) * w, rng.uniform(0.2, 0.7) * h
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    layer += (np.abs(np.sin(r / rng.uniform(5, 11))) < 0.06).astype(np.float32) * rng.uniform(0.15, 0.35)
    layer = np.clip(layer, 0, 1)[..., None] * rng.uniform(0.25, 0.55)
    img = img * (1 - layer) + ink[None, None, :] * layer
    return cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2BGR)


def _stamp(size: int, rng: random.Random, text: str | None = None) -> tuple[np.ndarray, tuple[int, int, int]]:
    """Return (alpha 0..1 float32 of shape size x size, BGR ink colour)."""
    canvas = Image.new('L', (size, size), 0)
    d = ImageDraw.Draw(canvas)
    shape = rng.choice(['circle', 'circle', 'rect', 'oval'])
    lw = max(3, size // 40)
    if shape == 'circle':
        d.ellipse((lw, lw, size - lw, size - lw), outline=255, width=lw)
        d.ellipse((size * 0.14, size * 0.14, size * 0.86, size * 0.86), outline=255, width=max(2, lw // 2))
    elif shape == 'oval':
        d.ellipse((lw, size * 0.18, size - lw, size * 0.82), outline=255, width=lw)
    else:
        d.rectangle((lw, size * 0.22, size - lw, size * 0.78), outline=255, width=lw)
    words = (text or rng.choice(['IMMIGRATION', 'ADMITTED', 'UTOPIA', 'CHECKPOINT', 'ISSUED', 'AUTHORITY'])).split()
    font = fonts.load(rng.choice(fonts.available(['arialbd.ttf', 'timesbd.ttf', 'courbd.ttf', 'DejaVuSans-Bold.ttf']) or ['arial.ttf']),
                      max(10, size // 9))
    y = size * 0.36
    for wtxt in words[:2] + [fmt_date(date(2026, 1, 1) + timedelta(days=rng.randint(0, 900)), '%d %b %Y')]:
        bb = font.getbbox(wtxt)
        d.text(((size - (bb[2] - bb[0])) / 2, y), wtxt, font=font, fill=255)
        y += (bb[3] - bb[1]) * 1.5
    alpha = np.asarray(canvas, np.float32) / 255.0
    texture = cv2.GaussianBlur(np.random.default_rng(rng.randrange(1 << 30)).random((size, size)).astype(np.float32), (0, 0), 2)
    alpha *= np.clip(0.55 + texture * 0.9, 0, 1) * rng.uniform(0.6, 0.95)
    M = cv2.getRotationMatrix2D((size / 2, size / 2), rng.uniform(-35, 35), 1.0)
    alpha = cv2.warpAffine(alpha, M, (size, size))
    ink = rng.choice([(150, 60, 40), (140, 40, 110), (40, 40, 170), (60, 60, 60), (120, 70, 30)])
    return alpha, ink


def blend_ink(img: np.ndarray, alpha: np.ndarray, ink, x: int, y: int) -> Box:
    h, w = alpha.shape
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return (x0, y0, x0, y0)
    a = alpha[y0 - y:y1 - y, x0 - x:x1 - x, None]
    roi = img[y0:y1, x0:x1].astype(np.float32)
    # Multiply blend: ink darkens the print underneath, as real stamp ink does.
    inked = roi * (np.array(ink, np.float32) / 255.0)
    img[y0:y1, x0:x1] = np.clip(roi * (1 - a) + inked * a, 0, 255).astype(np.uint8)
    return (x0, y0, x1, y1)


def _hologram(img: np.ndarray, box: Box, rng: random.Random) -> None:
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    hue = ((np.sin(xx / rng.uniform(9, 20)) + np.cos(yy / rng.uniform(9, 20)) + 2) * 45).astype(np.uint8)
    hsv = np.stack([hue, np.full_like(hue, 160), np.full_like(hue, 255)], -1)
    rainbow = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).astype(np.float32)
    mask = np.zeros((h, w), np.float32)
    cv2.ellipse(mask, (w // 2, h // 2), (w // 2 - 2, h // 2 - 2), 0, 0, 360, 1.0, -1)
    a = (cv2.GaussianBlur(mask, (0, 0), 6) * rng.uniform(0.07, 0.18))[..., None]
    roi = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = np.clip(roi * (1 - a) + rainbow * a, 0, 255).astype(np.uint8)


def _signature(d: ImageDraw.ImageDraw, box: Box, rng: random.Random) -> None:
    x0, y0, x1, y1 = box
    pts = []
    x = x0
    while x < x1:
        pts.append((x, rng.uniform(y0, y1)))
        x += rng.uniform(8, 30)
    color = rng.choice([(20, 30, 110), (15, 15, 15), (30, 30, 80)])
    for i in range(len(pts) - 1):
        d.line((pts[i], pts[i + 1]), fill=color, width=rng.randint(2, 4))


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

class _Writer:
    def __init__(self, im: Image.Image, doc: SynthDoc, rng: random.Random):
        self.im, self.doc, self.rng = im, doc, rng
        self.d = ImageDraw.Draw(im)
        self.value_font = rng.choice(fonts.available(fonts.VIZ_FONTS))
        self.label_font = rng.choice(fonts.available(fonts.VIZ_FONTS))
        self.label_color = rng.choice([(90, 90, 90), (60, 70, 120), (110, 60, 60), (70, 90, 70)])
        self.value_color = rng.choice([(20, 20, 20), (10, 10, 40), (35, 35, 35)])
        self.stacked = rng.random() < 0.75

    def text(self, xy, s, font_name, size, fill) -> Box:
        font = fonts.load(font_name, size)
        self.d.text(xy, s, font=font, fill=fill)
        bb = font.getbbox(s)
        return (int(xy[0] + bb[0]), int(xy[1] + bb[1]), int(xy[0] + bb[2]), int(xy[1] + bb[3]))

    def field(self, key: str, label: str, value: str, x: int, y: int, size: int) -> int:
        """Draw label + value, record annotations, return the y below the field."""
        lsize = max(12, int(size * 0.6))
        if self.stacked:
            lb = self.text((x, y), label, self.label_font, lsize, self.label_color)
            vy = lb[3] + int(size * 0.2)
            vb = self.text((x, vy), value, self.value_font, size, self.value_color)
        else:
            lb = self.text((x, y), label + ':', self.label_font, lsize, self.label_color)
            vb = self.text((lb[2] + int(size * 0.4), y - int(size * 0.25)), value, self.value_font, size, self.value_color)
        self.doc.viz_text[key] = value
        self.doc.boxes[key] = vb
        self.doc.label_boxes[key] = lb
        self.doc.fonts_used[key] = (self.value_font, size, self.value_color)
        return max(vb[3], lb[3]) + int(size * 0.55)


def draw_mrz(im: Image.Image, doc: SynthDoc, lines: list[str], x0: int, y0: int, width: int,
             rng: random.Random, font_name: str | None = None, max_height: int | None = None) -> None:
    d = ImageDraw.Draw(im)
    n = len(lines[0])
    pitch = width / n
    if font_name is None:
        pool = fonts.available(fonts.MRZ_TRAIN_MONO) + fonts.available(fonts.MRZ_TRAIN_PITCHED)
        font_name = rng.choice(pool)
    size = int(pitch * rng.uniform(1.45, 1.7))
    font = fonts.load(font_name, size)
    cap = font.getbbox('H')
    cap_h = cap[3] - cap[1]
    line_gap = int(cap_h * rng.uniform(1.55, 1.9))
    if max_height and (len(lines) - 1) * line_gap + cap[3] > max_height:
        line_gap = int(max(cap_h * 1.3, (max_height - cap[3]) / max(1, len(lines) - 1)))
    color = rng.choice([(10, 10, 10), (25, 25, 25), (15, 15, 35)])
    boxes = []
    for li, line in enumerate(lines):
        y = y0 + li * line_gap
        for ci, ch in enumerate(line):
            bb = font.getbbox(ch)
            cx = x0 + ci * pitch + (pitch - (bb[2] - bb[0])) / 2 - bb[0]
            d.text((cx, y), ch, font=font, fill=color)
        boxes.append((int(x0), int(y + cap[1]), int(x0 + width), int(y + cap[3])))
    doc.mrz_lines = list(lines)
    doc.mrz_line_boxes = boxes
    doc.mrz_render = dict(font=font_name, size=size, pitch=pitch, x0=x0, y0=y0, line_gap=line_gap, color=color)


# ---------------------------------------------------------------------------
# Document layouts
# ---------------------------------------------------------------------------

def _common_header(w: _Writer, W: int, title: str, subtitle: str) -> None:
    hf = w.rng.choice(fonts.available(['arialbd.ttf', 'timesbd.ttf', 'segoeuib.ttf', 'georgia.ttf', 'calibrib.ttf',
                                        'DejaVuSans-Bold.ttf']) or fonts.available(fonts.VIZ_FONTS))
    w.text((int(W * 0.045), int(W * 0.022)), title, hf, int(W * 0.03), (30, 30, 60))
    w.text((int(W * 0.045), int(W * 0.062)), subtitle, w.label_font, int(W * 0.017), (60, 60, 60))
    wm = 'SYNTHETIC SPECIMEN - NOT A VALID DOCUMENT'
    w.text((int(W * 0.5), int(W * 0.03)), wm, w.label_font, int(W * 0.0135), (170, 40, 40))


def _portrait(img: np.ndarray, doc: SynthDoc, box: Box, rng: random.Random, prefer_real: float) -> None:
    x0, y0, x1, y1 = box
    face = portrait(doc.person_seed, x1 - x0, y1 - y0, prefer_real)
    img[y0:y1, x0:x1] = face
    cv2.rectangle(img, (x0, y0), (x1 - 1, y1 - 1), (90, 90, 90), 2)
    doc.portrait_box = box


def _ghost(img: np.ndarray, doc: SynthDoc, box: Box) -> None:
    x0, y0, x1, y1 = box
    face = portrait(doc.person_seed, x1 - x0, y1 - y0)
    g = cv2.cvtColor(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR).astype(np.float32)
    roi = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = np.clip(roi * 0.72 + g * 0.28, 0, 255).astype(np.uint8)


def render_document(doc_type: str | None = None, seed: int | None = None, today: date | None = None,
                    person: Person | None = None, prefer_real_face: float = 0.0,
                    mrz_font: str | None = None, overrides: dict | None = None) -> SynthDoc:
    rng = random.Random(seed)
    today = today or date.today()
    doc_type = doc_type or rng.choice(DOC_TYPES)
    person = person or random_person(rng, today)
    W, H = CANVAS[doc_type]
    img = _background(W, H, rng)
    bg_copy = img.copy()
    doc = SynthDoc(image=img, doc_type=doc_type, fields={}, person_seed=person.face_seed)
    datefmt = rng.choice(DATE_FORMATS)
    overrides = overrides or {}

    if doc_type == 'passport':
        issue, expiry = document_dates(rng, today, 10)
        nationality = 'UTO' if rng.random() < 0.8 else person.nationality
        f = dict(document_type='passport', document_number=rand_docno(rng), surname=person.surname,
                 given_names=person.given_names, nationality=nationality, sex=person.sex,
                 date_of_birth=person.date_of_birth, place_of_birth=person.place_of_birth, date_of_issue=issue,
                 date_of_expiry=expiry, issuing_state='UTO', issuing_authority=rng.choice(PLACES))
    elif doc_type == 'visa':
        issue = today - timedelta(days=rng.randint(0, 400))
        valid_from = issue + timedelta(days=rng.randint(0, 20))
        validity = rng.choice([30, 60, 90, 180, 365, 730, 1825])
        expiry = valid_from + timedelta(days=validity)
        if rng.random() < 0.1:
            valid_from = today + timedelta(days=rng.randint(1, 60)); expiry = valid_from + timedelta(days=validity)
        f = dict(document_type='visa', document_number=rand_docno(rng, prefix_letters=rng.choice([0, 1, 2])),
                 surname=person.surname, given_names=person.given_names, nationality=person.nationality,
                 sex=person.sex, date_of_birth=person.date_of_birth, date_of_issue=issue, valid_from=valid_from,
                 date_of_expiry=expiry, visa_type=rng.choice(VISA_TYPES), number_of_entries=rng.choice(ENTRIES),
                 duration_of_stay_days=rng.choice([15, 30, 60, 90, 180]), passport_number=rand_docno(rng),
                 issuing_state='UTO', place_of_issue=rng.choice(PLACES))
    elif doc_type in ('national_id', 'residence_permit'):
        issue, expiry = document_dates(rng, today, rng.choice([5, 10]))
        f = dict(document_type=doc_type, document_number=rand_docno(rng), surname=person.surname,
                 given_names=person.given_names, nationality='UTO' if doc_type == 'national_id' else person.nationality,
                 sex=person.sex, date_of_birth=person.date_of_birth, date_of_issue=issue, date_of_expiry=expiry,
                 issuing_state='UTO')
        if doc_type == 'residence_permit':
            f['permit_type'] = rng.choice(PERMIT_TYPES)
            f['remarks'] = rng.choice(['EMPLOYMENT AUTHORISED', 'NO EMPLOYMENT', 'STUDY ONLY', 'FAMILY MEMBER'])
    else:  # driving_licence
        issue, expiry = document_dates(rng, today, rng.choice([10, 15, 20]))
        f = dict(document_type='driving_licence', document_number=f"UT{rng.randint(1, 99):02d}{issue.year}{rng.randint(0, 9999999):07d}",
                 surname=person.surname, given_names=person.given_names, date_of_birth=person.date_of_birth,
                 place_of_birth=person.place_of_birth, date_of_issue=issue, date_of_expiry=expiry,
                 issuing_authority=f'RTO {rng.choice(PLACES)}',
                 licence_categories=', '.join(sorted(rng.sample(DL_CATEGORIES, rng.randint(1, 4)))), issuing_state='UTO')
    f.update(overrides)
    doc.fields = f

    # Stamps, holograms and the ghost image are drawn on the numpy canvas; text via PIL.
    if doc_type == 'passport':
        pbox = (int(W * 0.045), int(H * 0.2), int(W * 0.045) + int(W * 0.24), int(H * 0.2) + int(W * 0.24 * 1.28))
    elif doc_type == 'visa':
        pbox = (int(W * 0.04), int(H * 0.2), int(W * 0.04) + int(W * 0.2), int(H * 0.2) + int(W * 0.2 * 1.28))
    else:
        pw = int(W * 0.17)
        pbox = (int(W * 0.04), int(H * 0.2), int(W * 0.04) + pw, int(H * 0.2) + int(pw * 1.28))
    _portrait(img, doc, pbox, rng, prefer_real_face)
    if doc_type in ('passport', 'national_id', 'residence_permit') and rng.random() < 0.7:
        gw = int((pbox[2] - pbox[0]) * 0.42); gh = int(gw * 1.28)
        gx = W - gw - int(W * 0.05); gy = int(H * 0.2)
        _ghost(img, doc, (gx, gy, gx + gw, gy + gh))

    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    w = _Writer(im, doc, rng)
    titles = {'passport': ('REPUBLIC OF UTOPIA', 'PASSPORT / PASSEPORT'), 'visa': ('REPUBLIC OF UTOPIA', 'VISA'),
              'national_id': ('REPUBLIC OF UTOPIA', 'NATIONAL IDENTITY CARD'),
              'driving_licence': ('UTOPIA', 'DRIVING LICENCE'),
              'residence_permit': ('REPUBLIC OF UTOPIA', 'RESIDENCE PERMIT')}
    _common_header(w, W, *titles[doc_type])
    D = lambda d: fmt_date(d, datefmt)
    nat_text = lambda code: code if rng.random() < 0.5 else NATIONALITIES.get(code, code)
    x1 = pbox[2] + int(W * 0.04)
    x2 = x1 + int(W * (0.3 if doc_type == 'passport' else 0.29))
    card = doc_type not in ('passport', 'visa')
    size = int(W * (rng.uniform(0.018, 0.021) if card else rng.uniform(0.02, 0.024)))

    if doc_type == 'passport':
        y = int(H * 0.19)
        y = w.field('document_type_code', 'Type', 'P', x1, y, size) if rng.random() < 0.5 else y
        w.field('issuing_state', 'Code', 'UTO', x2, int(H * 0.19), size)
        y = w.field('document_number', 'Passport No.', f['document_number'], x1, y, size)
        y = w.field('surname', 'Surname', f['surname'], x1, y, size)
        y = w.field('given_names', 'Given Names', f['given_names'], x1, y, size)
        yb = w.field('nationality', 'Nationality', nat_text(f['nationality']), x1, y, size)
        w.field('sex', 'Sex', f['sex'], x2, y, size)
        y = w.field('date_of_birth', 'Date of Birth', D(f['date_of_birth']), x1, yb, size)
        w.field('place_of_birth', 'Place of Birth', f['place_of_birth'], x2, yb, size)
        yb = w.field('date_of_issue', 'Date of Issue', D(f['date_of_issue']), x1, y, size)
        w.field('date_of_expiry', 'Date of Expiry', D(f['date_of_expiry']), x2, y, size)
        w.field('issuing_authority', 'Authority', f['issuing_authority'], x1, yb, size)
        lines = generate_mrz('TD3', document_code='P', issuing_state='UTO', document_number=f['document_number'],
                             nationality=f['nationality'], date_of_birth=yymmdd(f['date_of_birth']), sex=f['sex'],
                             date_of_expiry=yymmdd(f['date_of_expiry']), surname=f['surname'],
                             given_names=f['given_names'])
        mrz_y = int(H * rng.uniform(0.80, 0.83))
    elif doc_type == 'visa':
        y = int(H * 0.19)
        yb = w.field('visa_type', 'Type / Category', f['visa_type'], x1, y, size)
        w.field('document_number', 'Visa No.', f['document_number'], x2, y, size)
        y = w.field('number_of_entries', 'Entries', f['number_of_entries'], x1, yb, size)
        w.field('duration_of_stay_days', 'Duration of Stay', f"{f['duration_of_stay_days']} DAYS", x2, yb, size)
        yb = w.field('valid_from', 'Valid From', D(f['valid_from']), x1, y, size)
        w.field('date_of_expiry', 'Valid Until', D(f['date_of_expiry']), x2, y, size)
        y = w.field('surname', 'Surname', f['surname'], x1, yb, size)
        y = w.field('given_names', 'Given Names', f['given_names'], x1, y, size)
        yb = w.field('passport_number', 'Passport No.', f['passport_number'], x1, y, size)
        w.field('nationality', 'Nationality', nat_text(f['nationality']), x2, y, size)
        y = w.field('date_of_birth', 'Date of Birth', D(f['date_of_birth']), x1, yb, size)
        w.field('sex', 'Sex', f['sex'], x2, yb, size)
        x3 = x2 + int(W * 0.22)
        w.field('place_of_issue', 'Issued At', f['place_of_issue'], x3, int(H * 0.19), int(size * 0.9))
        w.field('date_of_issue', 'Date of Issue', D(f['date_of_issue']), x3, yb, int(size * 0.9))
        lines = generate_mrz('MRVA', document_code='V' + rng.choice('<<ABC'), issuing_state='UTO',
                             document_number=f['document_number'], nationality=f['nationality'],
                             date_of_birth=yymmdd(f['date_of_birth']), sex=f['sex'],
                             date_of_expiry=yymmdd(f['date_of_expiry']), surname=f['surname'],
                             given_names=f['given_names'], optional_data=f['passport_number'])
        mrz_y = int(H * rng.uniform(0.79, 0.82))
    elif doc_type in ('national_id', 'residence_permit'):
        y = int(H * 0.2)
        label = 'ID No.' if doc_type == 'national_id' else 'Permit No.'
        y = w.field('document_number', label, f['document_number'], x1, y, size)
        y = w.field('surname', 'Surname', f['surname'], x1, y, size)
        y = w.field('given_names', 'Given Names', f['given_names'], x1, y, size)
        yb = w.field('date_of_birth', 'Date of Birth', D(f['date_of_birth']), x1, y, size)
        w.field('sex', 'Sex', f['sex'], x2, y, size)
        if doc_type == 'residence_permit':
            y = w.field('permit_type', 'Type of Permit', f['permit_type'], x1, yb, size)
            w.field('nationality', 'Nationality', nat_text(f['nationality']), x2 + int(W * 0.08), yb, size)
            w.field('date_of_expiry', 'Valid Until', D(f['date_of_expiry']), x1, y, size)
            w.field('remarks', 'Remarks', f['remarks'], x2, y, int(size * 0.85))
        else:
            y = w.field('nationality', 'Nationality', nat_text(f['nationality']), x1, yb, size)
            w.field('date_of_expiry', 'Date of Expiry', D(f['date_of_expiry']), x2, yb, size)
            w.field('date_of_issue', 'Date of Issue', D(f['date_of_issue']), x1, y, size)
        lines = generate_mrz('TD1', document_code='ID' if doc_type == 'national_id' else 'IR', issuing_state='UTO',
                             document_number=f['document_number'], nationality=f['nationality'],
                             date_of_birth=yymmdd(f['date_of_birth']), sex=f['sex'],
                             date_of_expiry=yymmdd(f['date_of_expiry']), surname=f['surname'],
                             given_names=f['given_names'])
        mrz_y = int(H * rng.uniform(0.625, 0.645))
    else:
        y = int(H * 0.2)
        num = rng.random() < 0.7
        L = (lambda n, s: f'{n}. {s}') if num else (lambda n, s: s)
        y = w.field('surname', L('1', 'Surname'), f['surname'], x1, y, size)
        y = w.field('given_names', L('2', 'Given Names'), f['given_names'], x1, y, size)
        yb = w.field('date_of_birth', L('3', 'Date of Birth'), D(f['date_of_birth']), x1, y, size)
        w.field('place_of_birth', 'Place of Birth', f['place_of_birth'], x2, y, int(size * 0.9))
        y = w.field('date_of_issue', L('4a', 'Date of Issue'), D(f['date_of_issue']), x1, yb, size)
        w.field('date_of_expiry', L('4b', 'Valid Until'), D(f['date_of_expiry']), x2, yb, size)
        yb = w.field('issuing_authority', L('4c', 'Authority'), f['issuing_authority'], x1, y, int(size * 0.9))
        yb = w.field('document_number', L('5', 'Licence No.'), f['document_number'], x1, yb, size)
        w.field('licence_categories', L('9', 'Categories'), f['licence_categories'], x1, yb, size)
        lines, mrz_y = [], None

    sig_y = int(H * (0.66 if doc_type == 'passport' else 0.62 if doc_type == 'visa' else 0.88))
    if doc_type in ('driving_licence',) or rng.random() < 0.6:
        _signature(w.d, (pbox[0] + 10, min(sig_y, H - 60), pbox[0] + int(W * 0.18), min(sig_y, H - 60) + 40), rng)

    if lines:
        mrz_w = int(W * rng.uniform(0.88, 0.93))
        draw_mrz(im, doc, lines, int((W - mrz_w) / 2 + rng.uniform(-8, 8)), mrz_y, mrz_w, rng, mrz_font,
                 max_height=H - mrz_y - int(H * 0.03))
        doc.mrz_format = {'passport': 'TD3', 'visa': 'MRVA'}.get(doc_type, 'TD1')

    img = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR).copy()
    if rng.random() < 0.6:
        hs = int(W * rng.uniform(0.1, 0.16))
        hx = pbox[2] - hs // 2 + rng.randint(-20, 20); hy = pbox[3] - hs // 2 + rng.randint(-20, 20)
        _hologram(img, (max(0, hx), max(0, hy), min(W, hx + hs), min(H, hy + hs)), rng)
    # Genuine stamps appear on every document type so a forgery detector cannot equate stamps with forgery.
    n_stamps = {'visa': rng.choice([1, 1, 2, 3]), 'passport': rng.choice([0, 1, 1, 2])}.get(doc_type, rng.choice([0, 1, 1, 2]))
    for i in range(n_stamps):
        s = int(W * rng.uniform(0.12, 0.19))
        if i == 0 and doc_type != 'visa' and rng.random() < 0.6:
            sx, sy = pbox[2] - s // 2, pbox[3] - s // 2 - rng.randint(0, 60)
        else:
            sx, sy = int(W * rng.uniform(0.3, 0.85)), int(H * rng.uniform(0.15, 0.6))
        alpha, ink = _stamp(s, rng)
        doc.stamp_boxes.append(blend_ink(img, alpha, ink, sx, sy))
    doc.image = img
    doc.background = bg_copy
    doc.tamper_mask = np.zeros(img.shape[:2], np.uint8)
    return doc
