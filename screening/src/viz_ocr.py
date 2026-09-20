"""Visual inspection zone (VIZ) OCR: printed label/value field extraction with Tesseract.

Works across passports, visas, ID cards, driving licences and permits by locating printed
labels (fuzzy, multi-word, numbered ISO 18013 labels like '4b.') and taking the value either
inline after the label or on the line directly beneath it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np
import pytesseract
from pytesseract import Output
from rapidfuzz import fuzz

from .dates import parse_date
from .icao import to_code
from .runtime_utils import find_tesseract

_TESS = find_tesseract()
if _TESS:
    pytesseract.pytesseract.tesseract_cmd = _TESS

LABELS: dict[str, list[str]] = {
    'document_number': ['PASSPORT NO', 'PASSPORT NUMBER', 'DOCUMENT NO', 'DOCUMENT NUMBER', 'ID NO', 'ID NUMBER', 'CARD NO',
                        'PERMIT NO', 'LICENCE NO', 'LICENSE NO', 'DL NO', 'VISA NO', 'VISA NUMBER'],
    'surname': ['SURNAME', 'LAST NAME', 'FAMILY NAME'],
    'given_names': ['GIVEN NAMES', 'GIVEN NAME', 'FIRST NAME', 'FORENAMES'],
    'name': ['NAME', 'FULL NAME', 'NAME OF HOLDER'],
    'nationality': ['NATIONALITY', 'CITIZENSHIP'],
    'date_of_birth': ['DATE OF BIRTH', 'BIRTH DATE', 'DOB'],
    'sex': ['SEX', 'GENDER'],
    'place_of_birth': ['PLACE OF BIRTH'],
    'date_of_issue': ['DATE OF ISSUE', 'ISSUE DATE', 'ISSUED ON'],
    'date_of_expiry': ['DATE OF EXPIRY', 'EXPIRY DATE', 'DATE OF EXPIRATION', 'VALID UNTIL', 'VALID TILL', 'VALID THRU'],
    'valid_from': ['VALID FROM'],
    'visa_type': ['TYPE CATEGORY', 'VISA TYPE', 'TYPE OF VISA', 'CATEGORY'],
    'number_of_entries': ['ENTRIES', 'NO OF ENTRIES', 'NUMBER OF ENTRIES'],
    'duration_of_stay_days': ['DURATION OF STAY', 'STAY DURATION', 'PERIOD OF STAY', 'DURATION'],
    'issuing_authority': ['AUTHORITY', 'ISSUING AUTHORITY'],
    'place_of_issue': ['ISSUED AT', 'PLACE OF ISSUE'],
    'permit_type': ['TYPE OF PERMIT', 'PERMIT TYPE'],
    'remarks': ['REMARKS'],
    'licence_categories': ['CATEGORIES', 'VEHICLE CATEGORIES', 'CLASS'],
    'issuing_state': ['CODE', 'COUNTRY CODE'],
}
DOC_KEYWORDS = {
    'passport': ['PASSPORT', 'PASSEPORT'],
    'visa': ['VISA'],
    'national_id': ['IDENTITY CARD', 'NATIONAL IDENTITY', 'ID CARD'],
    'driving_licence': ['DRIVING LICENCE', 'DRIVING LICENSE', 'DRIVER LICENSE', 'DRIVERS LICENSE'],
    'residence_permit': ['RESIDENCE PERMIT', 'WORK PERMIT', 'RESIDENT PERMIT'],
}
DATE_FIELDS = ('date_of_birth', 'date_of_issue', 'date_of_expiry', 'valid_from')


@dataclass
class Word:
    text: str
    conf: float
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def h(self):
        return self.y1 - self.y0

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


@dataclass
class VIZResult:
    fields: dict = field(default_factory=dict)
    raw_values: dict = field(default_factory=dict)
    confidences: dict = field(default_factory=dict)
    boxes: dict = field(default_factory=dict)
    doc_type_scores: dict = field(default_factory=dict)
    text: str = ''
    mean_confidence: float = 0.0
    scale: float = 1.0
    printed_dates: list = field(default_factory=list)

    def to_dict(self):
        return dict(fields=self.fields, raw_values=self.raw_values, confidences=self.confidences, boxes=self.boxes,
                    doc_type_scores=self.doc_type_scores, mean_confidence=round(self.mean_confidence, 2))


def _clean_token(s: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def _letters(s: str) -> str:
    return re.sub(r'[^A-Z]', '', s.upper())


def prepare(img: np.ndarray, target_w: int = 1800) -> tuple[np.ndarray, float]:
    """Upscale and suppress light security printing (guilloche) while keeping anti-aliased
    glyph edges: each pixel is divided by the local paper brightness and soft-thresholded."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    scale = target_w / gray.shape[1]
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    paper = cv2.GaussianBlur(cv2.dilate(gray, np.ones((15, 15), np.uint8)), (0, 0), 9)
    ratio = gray.astype(np.float32) / np.maximum(paper.astype(np.float32), 1)
    return (np.clip((ratio - 0.42) / 0.48, 0, 1) * 255).astype(np.uint8), scale


def _text_chunks(prep: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Detect printed text chunks: lines split wherever glyph height jumps (small label ->
    large value), so each chunk can be normalised to a common text height."""
    ink = (prep < 128).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    if n <= 1:
        return []
    hs, ws = stats[1:, cv2.CC_STAT_HEIGHT], stats[1:, cv2.CC_STAT_WIDTH]
    keep = (hs >= 5) & (hs <= prep.shape[0] * 0.08) & (ws <= prep.shape[1] * 0.3)
    if not keep.any():
        return []
    mask = np.isin(lab, np.nonzero(keep)[0] + 1).astype(np.uint8) * 255
    med = float(np.median(hs[keep]))
    joined = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(med * 1.2)), 1)))
    n2, _, stats2, _ = cv2.connectedComponentsWithStats(joined, 8)
    chunks = []
    for i in range(1, n2):
        x, y, w, h, _ = stats2[i]
        if h < 5 or w < 8:
            continue
        sub = mask[y:y + h, x:x + w]
        prof = (sub > 0).sum(1) > max(1, 0.02 * w)
        rows, yy = [], 0
        while yy < h:
            if prof[yy]:
                y0 = yy
                while yy < h and prof[yy]:
                    yy += 1
                if yy - y0 >= 5:
                    rows.append((y0, yy))
            yy += 1
        for a0, a1 in rows:
            band = sub[a0:a1]
            nc, _, st, _ = cv2.connectedComponentsWithStats((band > 0).astype(np.uint8), 8)
            comps = sorted([tuple(st[j, :4]) for j in range(1, nc) if st[j, cv2.CC_STAT_HEIGHT] >= 3], key=lambda c: c[0])
            if not comps:
                continue
            groups = [[comps[0]]]
            for k, c in enumerate(comps[1:], 1):
                prev = groups[-1]
                right = comps[k:k + 4]
                left_h = float(np.median([g[3] for g in prev[-4:]]))
                right_h = float(np.median([d[3] for d in right]))
                gap = c[0] - max(g[0] + g[2] for g in prev)
                ratio = max(left_h, right_h) / max(1.0, min(left_h, right_h))
                if gap > 0.25 * max(left_h, right_h) and ratio > 1.3 and len(prev) >= 2 and len(right) >= 2:
                    groups.append([c])
                else:
                    prev.append(c)
            for g in groups:
                gx0 = min(c[0] for c in g); gx1 = max(c[0] + c[2] for c in g)
                gy0 = min(c[1] for c in g); gy1 = max(c[1] + c[3] for c in g)
                chunks.append((x + gx0, y + a0 + gy0, x + gx1, y + a0 + gy1))
    return sorted(chunks, key=lambda b: (b[1], b[0]))


_ROW_H, _GAP, _PAD_X = 34, 24, 20
_MAX_CHUNKS, _PAGE_H = 160, 7000


def ocr_words(img: np.ndarray) -> tuple[list[Word], float]:
    """OCR every detected chunk after normalising it to a common text height, stacked into
    montage pages so Tesseract's page-layout analysis cannot drop or merge lines."""
    prep, scale = prepare(img)
    chunks = [c for c in _text_chunks(prep) if (c[2] - c[0]) >= 0.25 * (c[3] - c[1]) and (c[3] - c[1]) >= 6]
    if len(chunks) > _MAX_CHUNKS:  # noise or clutter: keep the largest, most text-like chunks
        chunks = sorted(sorted(chunks, key=lambda c: -(c[2] - c[0]) * (c[3] - c[1]))[:_MAX_CHUNKS], key=lambda b: (b[1], b[0]))
    rows = []
    for x0, y0, x1, y1 in chunks:
        h = y1 - y0
        s = float(np.clip(_ROW_H / max(h, 1), 0.6, 4.0))
        pad = int(h * 0.25) + 2
        ox, oy = max(0, x0 - pad), max(0, y0 - pad)
        crop = cv2.resize(prep[oy:y1 + pad, ox:x1 + pad], None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        if crop.shape[1] > 6000:
            shrink = 6000 / crop.shape[1]
            crop = cv2.resize(crop, (6000, max(1, int(crop.shape[0] * shrink))), interpolation=cv2.INTER_AREA)
            s *= shrink
        rows.append((crop, s, ox, oy))
    pages, cur, height = [], [], _GAP
    for r in rows:
        if cur and height + r[0].shape[0] + _GAP > _PAGE_H:
            pages.append(cur)
            cur, height = [], _GAP
        cur.append(r)
        height += r[0].shape[0] + _GAP
    if cur:
        pages.append(cur)
    words = []
    for page in pages:
        width = max(r[0].shape[1] for r in page)
        canvas = np.full((sum(r[0].shape[0] + _GAP for r in page) + _GAP, width + 2 * _PAD_X), 255, np.uint8)
        spans, y = [], _GAP
        for crop, s, ox, oy in page:
            canvas[y:y + crop.shape[0], _PAD_X:_PAD_X + crop.shape[1]] = crop
            spans.append((y, y + crop.shape[0], s, ox, oy))
            y += crop.shape[0] + _GAP
        try:
            data = pytesseract.image_to_data(canvas, config='--oem 1 --psm 6', output_type=Output.DICT, timeout=60)
        except (pytesseract.TesseractError, RuntimeError):
            continue  # an unreadable page degrades the printed-zone read; it must not fail screening
        for i, t in enumerate(data['text']):
            t = (t or '').strip()
            conf = float(data['conf'][i]) if str(data['conf'][i]) not in ('', '-1') else -1
            if not t or conf < 0:
                continue
            L, T, W_, H_ = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
            cy = T + H_ / 2
            for r0, r1, s, ox, oy in spans:
                if r0 - _GAP / 2 <= cy <= r1 + _GAP / 2:
                    words.append(Word(t, conf, int(ox + (L - _PAD_X) / s), int(oy + (T - r0) / s),
                                      int(ox + (L + W_ - _PAD_X) / s), int(oy + (T + H_ - r0) / s)))
                    break
    return words, scale


def _lines(words: list[Word]) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.cy, w.x0)):
        for r in rows:
            if abs(w.cy - np.mean([x.cy for x in r])) < max(6, 0.45 * max(w.h, np.median([x.h for x in r]))):
                r.append(w)
                break
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w.x0) for r in rows]


def _segments(row: list[Word]) -> list[list[Word]]:
    """Split a visual row into columns separated by wide gaps."""
    if not row:
        return []
    segs = [[row[0]]]
    for w in row[1:]:
        prev = segs[-1][-1]
        if w.x0 - prev.x1 > max(prev.h, w.h) * 2.2:
            segs.append([w])
        else:
            segs[-1].append(w)
    return segs


_LABEL_INDEX = [(fld, lab.replace(' ', '')) for fld, labs in LABELS.items() for lab in labs]


def _label_hits(seg: list[Word]) -> list[tuple[int, int, str, float]]:
    """Non-overlapping label matches in a segment as (start, end, field, score).

    Matching runs on letters concatenated across word boundaries, so OCR splits such as
    'Iss: e:' or merges such as 'iDNo:.' still align with 'DATE OF ISSUE' / 'ID NO'."""
    letters = [_letters(w.text) for w in seg]
    cands = []
    for i in range(len(seg)):
        if not letters[i]:
            continue
        for n in range(1, 5):
            if i + n > len(seg):
                break
            joined = ''.join(letters[i:i + n])
            for fld, compact in _LABEL_INDEX:
                if len(joined) > len(compact) + max(2, len(compact) // 4) or len(joined) < len(compact) * 0.6:
                    continue
                score = fuzz.ratio(joined, compact)
                if len(compact) >= 8 and 6 <= len(joined) < len(compact):
                    # Truncated or partly garbled long label ('Catego1' -> CATEGORIES).
                    score = max(score, fuzz.ratio(joined, compact[:len(joined)]) - 4)
                if score >= (100 if len(compact) <= 4 else 86):
                    cands.append((score + len(compact), i, i + n, fld, score))
    cands.sort(key=lambda c: -c[0])
    taken: set[int] = set()
    hits = []
    for _, a, b, fld, score in cands:
        if taken.intersection(range(a, b)):
            continue
        # Words straight after a colon-terminated label are that label's value, not a new label.
        if a > 0 and seg[a - 1].text.endswith(':'):
            continue
        taken.update(range(a, b))
        hits.append((a, b, fld, score))
    return sorted(hits)


def _normalize_value(fld: str, raw: str):
    v = ' '.join(raw.upper().replace('|', 'I').split()).strip(' :;,.-')
    if not v:
        return None
    if fld in DATE_FIELDS:
        d = parse_date(v)
        return d.isoformat() if d else None
    if fld == 'sex':
        t = _clean_token(v)
        return {'M': 'M', 'MALE': 'M', 'F': 'F', 'FEMALE': 'F', 'X': 'X'}.get(t)
    if fld in ('nationality', 'issuing_state'):
        return to_code(v) or v
    if fld in ('document_number', 'passport_number'):
        return _clean_token(v) or None
    if fld == 'duration_of_stay_days':
        m = re.search(r'(\d{1,4})\s*(DAYS?|MONTHS?|YEARS?)?', v.replace('O', '0'))
        if not m:
            return None
        n, unit = int(m.group(1)), (m.group(2) or 'DAYS')
        return n * (30 if unit.startswith('MONTH') else 365 if unit.startswith('YEAR') else 1)
    if fld == 'number_of_entries':
        for opt in ('SINGLE', 'DOUBLE', 'MULTIPLE'):
            if fuzz.ratio(opt, _letters(v)) >= 75:
                return opt
        m = re.search(r'\d+', v)
        return m.group(0) if m else None
    return v


def _is_name(v) -> bool:
    return isinstance(v, str) and len(_letters(v)) >= max(1, int(len(v.replace(' ', '')) * 0.85))


_VALUE_OK = {
    'sex': lambda v: v in ('M', 'F', 'X'),
    # Document numbers always carry digits; this also rejects headers such as 'PASSEPORT'.
    'document_number': lambda v: isinstance(v, str) and 5 <= len(v) <= 20 and sum(c.isdigit() for c in v) >= 2,
    'passport_number': lambda v: isinstance(v, str) and 5 <= len(v) <= 20 and sum(c.isdigit() for c in v) >= 2,
    'surname': _is_name, 'given_names': _is_name, 'name': _is_name,
    'duration_of_stay_days': lambda v: isinstance(v, int) and 0 < v < 4000,
    'nationality': lambda v: isinstance(v, str) and len(v) == 3,
}


def extract_fields(img: np.ndarray, doc_type_hint: str | None = None,
                   exclude_boxes: list | None = None) -> VIZResult:
    """Extract labelled fields. ``exclude_boxes`` (image coordinates) are blanked before OCR so
    regions such as the MRZ cannot be mistaken for printed values."""
    work = img.copy()
    for x0, y0, x1, y1 in exclude_boxes or []:
        work[max(0, int(y0)):max(0, int(y1)), max(0, int(x0)):max(0, int(x1))] = 255
    words, scale = ocr_words(work)
    res = VIZResult(scale=scale)
    if not words:
        return res
    res.mean_confidence = float(np.mean([w.conf for w in words]))
    rows = _lines(words)
    res.text = '\n'.join(' '.join(w.text for w in r) for r in rows)
    upper = res.text.upper()
    for dt, kws in DOC_KEYWORDS.items():
        res.doc_type_scores[dt] = max((fuzz.partial_ratio(k, upper) if len(upper) >= len(k) else 0) for k in kws) / 100.0

    labels = []  # (field, label words, inline value words, score)
    for r in rows:
        for seg in _segments(r):
            hits = _label_hits(seg)
            for j, (a, b, fld, score) in enumerate(hits):
                nxt = hits[j + 1][0] if j + 1 < len(hits) else len(seg)
                labels.append((fld, seg[a:b], seg[b:nxt], score))
    used = {id(w) for _, lw, _, _ in labels for w in lw}

    candidates = []
    for fld, lab_words, inline, score in labels:
        lx0, ly0 = min(w.x0 for w in lab_words), min(w.y0 for w in lab_words)
        ly1 = max(w.y1 for w in lab_words)
        lh = max(1, ly1 - ly0)
        options = []
        if inline:
            options.append(('inline', inline))
        below = [w for w in words if id(w) not in used and ly1 - lh * 0.3 <= w.y0 <= ly1 + lh * 2.8 and w.x0 >= lx0 - lh * 1.2]
        if below:
            top = min(w.y0 for w in below)
            row = sorted([w for w in below if w.y0 < top + max(w.h for w in below) * 0.6], key=lambda w: w.x0)
            segs = _segments(row)
            if segs and segs[0][0].x0 <= lx0 + lh * 2.5:
                options.append(('below', segs[0]))
        target = fld
        if fld == 'document_number' and doc_type_hint == 'visa' and 'PASSPORT' in _letters(' '.join(w.text for w in lab_words)):
            target = 'passport_number'
        for mode, vw in options:
            raw = ' '.join(w.text for w in vw)
            val = _normalize_value(target, raw)
            if val in (None, '') or not _VALUE_OK.get(target, lambda v: True)(val):
                continue
            conf = float(np.mean([w.conf for w in vw])) * (score / 100) * (1.0 if mode == 'inline' else 0.97)
            candidates.append((conf, target, val, raw, vw))
            break
    for conf, target, val, raw, vw in sorted(candidates, key=lambda c: -c[0]):
        if target in res.fields or any(id(w) in used for w in vw):
            continue
        used.update(id(w) for w in vw)
        res.fields[target] = val
        res.raw_values[target] = raw
        res.confidences[target] = round(conf, 1)
        res.boxes[target] = [int(min(w.x0 for w in vw) / scale), int(min(w.y0 for w in vw) / scale),
                             int(max(w.x1 for w in vw) / scale), int(max(w.y1 for w in vw) / scale)]
    if 'name' in res.fields and 'surname' not in res.fields:
        parts = str(res.fields['name']).split()
        if len(parts) >= 2:
            res.fields.setdefault('given_names', ' '.join(parts[:-1]))
            res.fields['surname'] = parts[-1]
    _label_free_fallbacks(res, rows, used, doc_type_hint)
    lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB).astype(np.float32) if work.ndim == 3 else None

    def dark_ink(w):  # printed text, not coloured stamp ink
        if lab is None:
            return True
        x0, y0, x1, y1 = (int(v / scale) for v in (w.x0, w.y0, w.x1, w.y1))
        roi = lab[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        if roi.size == 0:
            return True
        ink = roi[..., 0] <= np.percentile(roi[..., 0], 40)
        chroma = np.sqrt((roi[..., 1] - 128) ** 2 + (roi[..., 2] - 128) ** 2)
        return float(chroma[ink].mean()) < 18

    res.printed_dates = sorted({d for r in rows for d in printed_dates(' '.join(w.text for w in r if dark_ink(w)))})
    return res


_DATE_RE = re.compile(r'\b(\d{1,2}[ ./-](?:\d{1,2}|[A-Z]{3})[ ./-]\d{4}|\d{4}[ ./-]\d{1,2}[ ./-]\d{1,2})\b')


def printed_dates(text: str) -> list[str]:
    """Every date printed anywhere in the visual zone (ISO strings)."""
    out = []
    for m in _DATE_RE.finditer(text.upper()):
        d = parse_date(m.group(1))
        if d:
            out.append(d.isoformat())
    return out


def _label_free_fallbacks(res: VIZResult, rows, used: set, doc_type_hint: str | None) -> None:
    """When small printed labels are unreadable (noise, blur), fall back on what values look like:
    birth < issue < expiry by chronology, and the long alphanumeric token as the document number."""
    unassigned = [w for r in rows for w in r if id(w) not in used]
    line_text = [' '.join(w.text for w in r if id(w) not in used) for r in rows]
    dates = sorted({d for t in line_text for d in printed_dates(t)} - {str(v) for v in res.fields.values()})
    if doc_type_hint != 'visa' and dates:
        missing = [k for k in ('date_of_birth', 'date_of_issue', 'date_of_expiry') if k not in res.fields]
        known = sorted(str(res.fields[k]) for k in ('date_of_birth', 'date_of_issue', 'date_of_expiry') if k in res.fields)
        pool = list(dates)
        if 'date_of_birth' in missing and pool and (not known or pool[0] < known[0]):
            res.fields['date_of_birth'] = pool.pop(0)
            res.confidences['date_of_birth'] = 50.0
        if 'date_of_expiry' in missing and pool and (not known or pool[-1] > known[-1]):
            res.fields['date_of_expiry'] = pool.pop()
            res.confidences['date_of_expiry'] = 50.0
        if 'date_of_issue' in missing and len(pool) == 1:
            res.fields['date_of_issue'] = pool.pop()
            res.confidences['date_of_issue'] = 45.0
    if 'document_number' not in res.fields:
        cands = [_clean_token(w.text) for w in unassigned]
        cands = [c for c in cands if 8 <= len(c) <= 20 and sum(ch.isdigit() for ch in c) >= 4 and not printed_dates(c)]
        if cands:
            res.fields['document_number'] = max(cands, key=len)
            res.confidences['document_number'] = 45.0
