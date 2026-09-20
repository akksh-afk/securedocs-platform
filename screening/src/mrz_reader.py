"""Neural MRZ reader: locate MRZ text lines, recognise them with the trained CTC network,
and resolve the machine readable zone with check-digit-aware decoding."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np

from .mrz_utils import MRZ_ALPHABET, MRZResult, parse_mrz

LINE_H, LINE_W = 32, 512


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def crop_quad(img: np.ndarray, quad: np.ndarray, top: float, bot: float, left: float, right: float,
              out_w: int = LINE_W, out_h: int = LINE_H) -> np.ndarray:
    """Rectify a text line given its (tl, tr, br, bl) quad, padding each side in pixels."""
    tl, tr, br, bl = [np.asarray(p, np.float32) for p in quad]
    ux = (tr - tl) / max(float(np.linalg.norm(tr - tl)), 1e-6)
    uy = (bl - tl) / max(float(np.linalg.norm(bl - tl)), 1e-6)
    src = np.float32([tl - ux * left - uy * top, tr + ux * right - uy * top,
                      br + ux * right + uy * bot, bl - ux * left + uy * bot])
    dst = np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]])
    M = cv2.getPerspectiveTransform(src, dst)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    shrink = np.linalg.norm(tr - tl) > out_w
    return cv2.warpPerspective(gray, M, (out_w, out_h), flags=cv2.INTER_AREA if shrink else cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)


def _rect_quad(rect) -> np.ndarray:
    """Ordered (tl, tr, br, bl) corners of a cv2.minAreaRect with the long side horizontal."""
    (cx, cy), (w, h), ang = rect
    if h > w:
        w, h, ang = h, w, ang + 90
    if ang > 45:
        ang -= 180
    elif ang < -135:
        ang += 180
    a = np.deg2rad(ang)
    ux, uy = np.array([np.cos(a), np.sin(a)]), np.array([-np.sin(a), np.cos(a)])
    c = np.array([cx, cy])
    tl = c - ux * w / 2 - uy * h / 2
    tr = c + ux * w / 2 - uy * h / 2
    br = c + ux * w / 2 + uy * h / 2
    bl = c - ux * w / 2 + uy * h / 2
    quad = np.float32([tl, tr, br, bl])
    if quad[0][0] > quad[1][0]:  # keep reading direction left->right
        quad = np.float32([br, bl, tl, tr])
    return quad


@dataclass
class LineCandidate:
    quad: np.ndarray          # in original-image coordinates
    height: float
    width: float
    angle: float


def find_text_lines(img: np.ndarray, max_side: int = 1400) -> list[LineCandidate]:
    """Find long, dense horizontal text lines (MRZ candidates) at any scale."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    scale = min(1.0, max_side / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    H, W = small.shape
    # Flatten illumination (shadows, glare gradients, low light).
    bg = cv2.GaussianBlur(small, (0, 0), max(W, H) / 60)
    flat = cv2.normalize(cv2.divide(small, np.maximum(bg, 1), scale=180), None, 0, 255, cv2.NORM_MINMAX)
    found: list[LineCandidate] = []
    for char_h in (W / 90, W / 55, W / 36):
        kh = max(3, int(char_h * 1.6)) | 1
        kw = max(5, int(char_h * 2.2)) | 1
        blackhat = cv2.morphologyEx(flat, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh)))
        gx = cv2.convertScaleAbs(cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=3))
        gx = cv2.GaussianBlur(gx, (3, 3), 0)
        closed = cv2.morphologyEx(gx, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(char_h * 1.8)), max(1, int(char_h * 0.25)))))
        _, th = cv2.threshold(closed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # Document borders and printed boxes fuse with text bands; remove long vertical strokes.
        vert = cv2.morphologyEx(th, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(5, int(char_h * 2.5)))))
        th = cv2.subtract(th, cv2.dilate(vert, np.ones((3, max(3, int(char_h * 0.3))), np.uint8)))
        th = cv2.morphologyEx(th, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(char_h)), max(1, int(char_h * 0.35)))))
        contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            rect = cv2.minAreaRect(c)
            (cx, cy), (w, h), _ = rect
            long_side, short_side = max(w, h), max(1.0, min(w, h))
            if long_side < W * 0.25 or long_side / short_side < 7:
                continue
            quad = _rect_quad(rect)
            ang = float(np.degrees(np.arctan2(quad[1][1] - quad[0][1], quad[1][0] - quad[0][0])))
            if abs(ang) > 30:
                continue
            fill = cv2.contourArea(c) / max(1.0, long_side * short_side)
            if fill < 0.45:
                continue
            found.extend(_split_block(flat, quad, long_side, short_side, ang, char_h, scale))
    return _merge_collinear(_dedupe(found))


def _split_block(flat, quad, long_side, short_side, ang, char_h, scale) -> list[LineCandidate]:
    """A component may hold several merged lines; split it with a row projection profile."""
    out_h = max(8, int(short_side))
    out_w = max(16, int(long_side))
    patch = crop_quad(flat, quad, 0, 0, 0, 0, out_w=out_w, out_h=out_h)
    ink = cv2.morphologyEx(patch, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(out_h)) | 1, max(3, int(out_h)) | 1)))
    profile = cv2.GaussianBlur(ink.mean(axis=1).astype(np.float32).reshape(-1, 1), (1, 3), 0).ravel()
    tl, tr, br, bl = quad
    ux, uy = (tr - tl) / out_w, (bl - tl) / out_h
    lines = []
    thr = profile.max() * 0.3
    rows = profile > thr
    y = 0
    while y < out_h:
        if rows[y]:
            y0 = y
            while y < out_h and rows[y]:
                y += 1
            h = y - y0
            if h >= max(3, out_h * 0.08):
                q = np.float32([tl + uy * y0, tr + uy * y0, tr + uy * y, tl + uy * y])
                lines.append(LineCandidate(q / scale, h / scale, long_side / scale, ang))
        y += 1
    # Only accept a split if it produced lines of comparable height.
    if len(lines) > 1:
        hs = np.array([l.height for l in lines])
        keep = [l for l in lines if l.height > hs.max() * 0.45]
        if len(keep) >= 2:
            return keep
    return [LineCandidate(quad / scale, short_side / scale, long_side / scale, ang)]


def _merge_collinear(lines: list[LineCandidate]) -> list[LineCandidate]:
    """Join pieces of the same text line that an overlapping stamp, glare or finger split apart."""
    lines = sorted(lines, key=lambda l: l.quad[:, 0].min())
    merged: list[LineCandidate] = []
    for l in lines:
        for m in merged:
            # Measure along the text direction of the longer piece: on a rotated capture, pieces of one
            # line sit at different image heights but on the same slanted baseline.
            ref = m if m.width >= l.width else l
            u = ref.quad[1] - ref.quad[0]
            u = u / max(float(np.linalg.norm(u)), 1e-6)
            nrm = np.array([-u[1], u[0]])
            h = max(l.height, m.height)
            off = abs(float(np.dot(l.quad.mean(axis=0) - m.quad.mean(axis=0), nrm)))
            gap = float(np.dot(l.quad, u).min() - np.dot(m.quad, u).max())
            if off < 0.35 * h and min(l.height, m.height) > 0.6 * h and -0.2 * h < gap < 6 * h and abs(l.angle - m.angle) < 3:
                pts = np.concatenate([m.quad, l.quad]).astype(np.float32)
                rect = cv2.minAreaRect(pts)
                q = _rect_quad(rect)
                m.quad, m.width = q, float(np.linalg.norm(q[1] - q[0]))
                m.height = float(np.linalg.norm(q[3] - q[0]))
                break
        else:
            merged.append(LineCandidate(l.quad.copy(), l.height, l.width, l.angle))
    return merged


def _dedupe(lines: list[LineCandidate]) -> list[LineCandidate]:
    out: list[LineCandidate] = []
    for l in sorted(lines, key=lambda l: -l.width):
        c = l.quad.mean(axis=0)
        if any(abs(c[1] - o.quad.mean(axis=0)[1]) < max(l.height, o.height) * 0.6 and
               abs(c[0] - o.quad.mean(axis=0)[0]) < max(l.width, o.width) * 0.3 for o in out):
            continue
        out.append(l)
    return out


def group_lines(lines: list[LineCandidate]) -> list[list[LineCandidate]]:
    """Group candidate lines into plausible 2- or 3-line MRZ blocks, most likely first."""
    ls = sorted(lines, key=lambda l: l.quad.mean(axis=0)[1])
    groups = []
    for i in range(len(ls)):
        for n in (2, 3):
            if i + n > len(ls):
                continue
            g = ls[i:i + n]
            widths = np.array([l.width for l in g])
            heights = np.array([l.height for l in g])
            centers = np.array([l.quad.mean(axis=0) for l in g])
            if widths.min() < widths.max() * 0.5 or heights.min() < heights.max() * 0.4:
                continue
            gaps = np.diff(centers[:, 1])
            h = float(np.median(heights))
            if gaps.min() < h * 0.9 or gaps.max() > h * 3.6:
                continue
            lefts = np.array([l.quad[:, 0].min() for l in g])
            rights = np.array([l.quad[:, 0].max() for l in g])
            # MRZ lines share a left or right edge even when one end is hidden.
            if np.ptp(lefts) > widths.max() * 0.08 and np.ptp(rights) > widths.max() * 0.08:
                continue
            score = float(widths.mean()) - 0.2 * float(np.ptp(widths)) + centers[:, 1].mean() * 0.05
            groups.append((score, g))
    groups.sort(key=lambda s: -s[0])
    return [g for _, g in groups]


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------

def _softmax(x, axis):
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def ctc_greedy(probs: np.ndarray):
    """probs (T, C). Returns (text, per-char alternatives [(char, prob)...], per-char confidence)."""
    best = probs.argmax(1)
    chars, alts, confs = [], [], []
    t = 0
    T = len(best)
    while t < T:
        k = best[t]
        if k == 0:
            t += 1
            continue
        t0 = t
        while t < T and best[t] == k:
            t += 1
        seg = probs[t0:t, 1:].mean(axis=0)
        order = np.argsort(-seg)[:3]
        chars.append(MRZ_ALPHABET[k - 1])
        alts.append([(MRZ_ALPHABET[i], float(seg[i])) for i in order])
        confs.append(float(probs[t0:t, k].max()))
    return ''.join(chars), alts, confs


def ctc_fixed_length(probs: np.ndarray, L: int):
    """Viterbi decode of the best CTC path emitting exactly ``L`` labels."""
    T, C = probs.shape
    if L > T:
        return None
    lp = np.log(np.maximum(probs, 1e-12))
    NEG = -1e18
    vb = np.full(L + 1, NEG); vb[0] = lp[0, 0]
    vl = np.full((L + 1, C), NEG); vl[1, 1:] = lp[0, 1:]
    back_b = np.zeros((T, L + 1), np.int16)        # -1 from blank, >=1 from label k
    back_l = np.zeros((T, L + 1, C), np.int16)     # 0 continue, -1 from blank(n-1), k>=1 from label k (n-1)
    for t in range(1, T):
        # blank state
        best_lab = vl.argmax(axis=1)
        best_lab_v = vl[np.arange(L + 1), best_lab]
        from_blank = vb >= best_lab_v
        nb = np.where(from_blank, vb, best_lab_v) + lp[t, 0]
        back_b[t] = np.where(from_blank, -1, best_lab)
        # label states
        cont = vl
        prev_b = np.concatenate([[NEG], vb[:-1]])[:, None] * np.ones((1, C))
        prev_l = np.vstack([np.full((1, C), NEG), vl[:-1]])
        top1 = prev_l.argmax(axis=1)
        v1 = prev_l[np.arange(L + 1), top1]
        tmp = prev_l.copy(); tmp[np.arange(L + 1), top1] = NEG
        top2 = tmp.argmax(axis=1)
        v2 = tmp[np.arange(L + 1), top2]
        cls = np.arange(C)[None, :]
        other_v = np.where(cls == top1[:, None], v2[:, None], v1[:, None])
        other_k = np.where(cls == top1[:, None], top2[:, None], top1[:, None])
        choice = np.stack([cont, prev_b, other_v])
        arg = choice.argmax(axis=0)
        nl = choice.max(axis=0) + lp[t][None, :]
        nl[:, 0] = NEG
        back_l[t] = np.where(arg == 0, 0, np.where(arg == 1, -1, other_k)).astype(np.int16)
        vb, vl = nb, nl
    # Backtrack.
    end_l = vl[L].argmax()
    state = ('b', None) if vb[L] >= vl[L, end_l] else ('l', int(end_l))
    n = L
    labels_rev, frames = [], []
    seg_end = None
    for t in range(T - 1, -1, -1):
        if state[0] == 'b':
            if t == 0:
                break
            pb = back_b[t, n]
            state = ('b', None) if pb == -1 else ('l', int(pb))
        else:
            k = state[1]
            if seg_end is None:
                seg_end = t
            pk = back_l[t, n, k] if t > 0 else -2
            if pk == 0:
                continue
            labels_rev.append(k); frames.append((t, seg_end)); seg_end = None
            n -= 1
            if t == 0:
                break
            state = ('b', None) if pk == -1 else ('l', int(pk))
    labels = labels_rev[::-1]
    frames = frames[::-1]
    if len(labels) != L:
        return None
    alts, confs = [], []
    for k, (a, b) in zip(labels, frames):
        seg = probs[a:b + 1, 1:].mean(axis=0)
        order = np.argsort(-seg)[:3]
        alts.append([(MRZ_ALPHABET[i], float(seg[i])) for i in order])
        confs.append(float(probs[a:b + 1, k].max()))
    return ''.join(MRZ_ALPHABET[k - 1] for k in labels), alts, confs


@lru_cache(maxsize=1)
def _predictor():
    from .nets import Predictor
    return Predictor('mrz_reader')


def model_available() -> bool:
    return model_error() is None


def model_error() -> str | None:
    """None when the MRZ reader loads, otherwise a human-readable reason."""
    try:
        _predictor()
        return None
    except Exception as e:
        return str(e)


def recognise(line_imgs: list[np.ndarray]) -> list[np.ndarray]:
    """Grey 32x512 line crops -> per-line CTC probability matrices (T, C)."""
    if not line_imgs:
        return []
    x = np.stack([im.astype(np.float32) for im in line_imgs])[:, None]
    m = x.mean(axis=(1, 2, 3), keepdims=True)
    s = x.std(axis=(1, 2, 3), keepdims=True)
    x = ((x - m) / (s + 1e-3)).astype(np.float32)
    logits = _predictor()(x)  # (N, C, 1, T)
    return [_softmax(l[:, 0, :].T, axis=1) for l in logits]


# ---------------------------------------------------------------------------
# Full MRZ reading
# ---------------------------------------------------------------------------

@dataclass
class MRZRead:
    mrz: MRZResult
    confidence: float
    raw_lines: list[str] = field(default_factory=list)
    char_confidence: list[list[float]] = field(default_factory=list)
    line_quads: list[list[list[float]]] = field(default_factory=list)
    attempts: int = 0
    rotation: int = 0

    def to_dict(self):
        return dict(mrz=self.mrz.to_dict(), confidence=round(self.confidence, 2), raw_lines=self.raw_lines,
                    min_char_confidence=round(min((min(c) for c in self.char_confidence if c), default=0.0), 3),
                    line_quads=self.line_quads, attempts=self.attempts, rotation=self.rotation)


def _score(m: MRZResult, confs: list[list[float]]) -> float:
    if not m.valid_format:
        return 0.0
    checks = m.check_digits or {}
    passed = sum(bool(v) for k, v in checks.items() if k != 'passport_number')
    total = max(1, sum(1 for k in checks if k != 'passport_number'))
    mean_conf = float(np.mean([c for line in confs for c in line])) if confs else 0.0
    return 40.0 * passed / total + 35.0 * float(m.overall_check_digit_valid) + 25.0 * mean_conf




def _harmonise(quads: list[np.ndarray]) -> list[np.ndarray]:
    """MRZ lines in one block are equally long and aligned: extend every line to the block's extent
    so a line partly hidden by a stamp or glare is still read end to end."""
    ref = max(quads, key=lambda q: np.linalg.norm(q[1] - q[0]))
    u = (ref[1] - ref[0]) / max(float(np.linalg.norm(ref[1] - ref[0])), 1e-6)
    lo = min(float(np.dot(q, u).min()) for q in quads)
    hi = max(float(np.dot(q, u).max()) for q in quads)
    out = []
    for q in quads:
        tl, tr, br, bl = q
        a0, a1 = float(np.dot(tl, u)), float(np.dot(tr, u))
        b0, b1 = float(np.dot(bl, u)), float(np.dot(br, u))
        out.append(np.float32([tl + u * (lo - a0), tr + u * (hi - a1), br + u * (hi - b1), bl + u * (lo - b0)]))
    return out


def _decode_tta(crop_sets: list[list[np.ndarray]]):
    """Average CTC posteriors over crop variants that share horizontal alignment, then decode."""
    n = len(crop_sets[0])
    flat = [c for cs in crop_sets for c in cs]
    probs = recognise(flat)
    avg = [np.mean([probs[v * n + i] for v in range(len(crop_sets))], axis=0) for i in range(n)]
    texts, alts, confs = [], [], []
    for p in avg:
        t, a, c = ctc_greedy(p)
        texts.append(t); alts.append(a); confs.append(c)
    widths = [30] if n == 3 else [44, 36]
    target = min(widths, key=lambda w: sum(abs(len(t) - w) for t in texts))
    for i, p in enumerate(avg):
        if len(texts[i]) != target:
            fixed = ctc_fixed_length(p, target)
            if fixed:
                texts[i], alts[i], confs[i] = fixed
    return texts, alts, confs, target


def read_mrz_from_quads(img: np.ndarray, quads: list[np.ndarray], heights: list[float]) -> tuple[MRZResult, list, list, list]:
    quads = _harmonise(quads)
    variants = [(0.3, 0.3), (0.18, 0.42), (0.42, 0.18)]  # vertical slack only: frames stay aligned
    crop_sets = []
    for top, bot in variants:
        crop_sets.append([crop_quad(img, q, h * top, h * bot, h * 0.45, h * 0.45) for q, h in zip(quads, heights)])
    best = None
    for flip in (False, True):
        sets = [[cv2.rotate(c, cv2.ROTATE_180) for c in cs[::-1]] for cs in crop_sets] if flip else crop_sets
        texts, alts, confs, target = _decode_tta(sets)
        fmt_alts = alts if all(len(t) == target for t in texts) else None
        m = parse_mrz(texts, alternatives=fmt_alts)
        s = _score(m, confs)
        if best is None or s > best[0]:
            best = (s, m, texts, confs, flip)
    return best[1], best[2], best[3], [best[4]]


def read_mrz(img: np.ndarray, max_groups: int = 6) -> MRZRead:
    """Locate and read an MRZ anywhere in ``img`` (any of the four orientations)."""
    best: MRZRead | None = None
    attempts = 0
    for rot in (0, 90, 270):
        view = img if rot == 0 else cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE if rot == 90 else cv2.ROTATE_90_COUNTERCLOCKWISE)
        lines = find_text_lines(view)
        for group in group_lines(lines)[:max_groups]:
            attempts += 1
            m, texts, confs, flip = read_mrz_from_quads(view, [l.quad for l in group], [l.height for l in group])
            s = _score(m, confs)
            if best is None or s > _score(best.mrz, best.char_confidence):
                best = MRZRead(m, s, texts, confs, [l.quad.tolist() for l in group], attempts, rot + (180 if flip[0] else 0))
            if m.valid_format and m.overall_check_digit_valid and s > 90:
                best.attempts = attempts
                return best
        if best is not None and best.mrz.valid_format and best.mrz.overall_check_digit_valid:
            break
    if best is None:
        return MRZRead(parse_mrz([]), 0.0, attempts=attempts)
    best.attempts = attempts
    return best
