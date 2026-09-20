"""Module 3: tampering and forgery detection.

Four independent evidence sources, reported per problem-statement use case:
  * pixel forensics  - trained U-Net localising altered pixels (SRM noise residuals + RGB);
  * copy-move        - duplicated stamp ink found by keypoint self-matching;
  * image metadata   - editing software, inconsistent timestamps, recompression traces;
  * semantics        - MRZ/printed-zone contradictions, failed check digits, portrait anomalies.
"""
from __future__ import annotations

import io
import json
import re
from functools import lru_cache

import cv2
import numpy as np
from PIL import ExifTags, Image

from .config import MODEL_DIR, load_policy

USE_CASES = ('photo_replacement', 'text_manipulation', 'stamp_forgery', 'metadata')
EDITORS = ['photoshop', 'gimp', 'paint.net', 'pixelmator', 'affinity', 'canva', 'snapseed', 'picsart', 'lightroom',
           'photopea', 'fotor', 'inkscape', 'illustrator', 'corel', 'krita', 'paintshop', 'facetune', 'remini', 'meitu']
WORK_W = 1024


# ---------------------------------------------------------------------------
# Pixel forensics
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _model():
    from .nets import Predictor
    return Predictor('tamper')


@lru_cache(maxsize=1)
def _calibration() -> dict:
    p = MODEL_DIR / 'tamper_calibration.json'
    return json.loads(p.read_text()) if p.exists() else dict(image_threshold=0.5, region_threshold=0.5)


HEATMAP_FEATURES = ['max_blur7', 'max_blur21', 'top100_mean', 'top1000_mean', 'top10000_mean', 'area_gt50', 'area_gt80',
                    'area_gt95', 'largest_comp_area', 'largest_comp_mean', 'n_comps', 'mean_prob']


def heatmap_features(prob: np.ndarray) -> dict:
    """Scale-normalised statistics of a forgery heatmap for the document-level decision."""
    s = 512 / max(prob.shape)
    p = cv2.resize(prob, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else prob
    flat = np.sort(p.ravel())[::-1]
    binary = (p > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    areas = stats[1:, cv2.CC_STAT_AREA] if n > 1 else np.array([0])
    k = int(np.argmax(areas)) + 1 if n > 1 else 0
    return dict(max_blur7=float(cv2.blur(p, (7, 7)).max()), max_blur21=float(cv2.blur(p, (21, 21)).max()),
                top100_mean=float(flat[:100].mean()), top1000_mean=float(flat[:1000].mean()),
                top10000_mean=float(flat[:10000].mean()), area_gt50=float((p > 0.5).mean()),
                area_gt80=float((p > 0.8).mean()), area_gt95=float((p > 0.95).mean()),
                largest_comp_area=float(areas.max()) / p.size, largest_comp_mean=float(p[lab == k].mean()) if k else 0.0,
                n_comps=float(((areas / p.size) > 0.0004).sum()), mean_prob=float(p.mean()))


@lru_cache(maxsize=1)
def _aggregator():
    path = MODEL_DIR / 'tamper_aggregator.joblib'
    try:
        import joblib
        return joblib.load(path) if path.exists() else None
    except ImportError:  # scikit-learn/joblib missing: fall back to the calibrated hottest-region score
        return None


def document_forgery_score(prob: np.ndarray) -> tuple[float, float]:
    """(score, threshold) for the whole document: learned aggregator if trained, else hottest region."""
    agg = _aggregator()
    if agg is not None:
        f = heatmap_features(prob)
        x = np.array([[f[k] for k in agg['features']]], np.float32)
        return float(agg['model'].predict_proba(x)[0, 1]), float(agg['threshold'])
    return float(cv2.blur(prob, (7, 7)).max()), float(_calibration().get('image_threshold', 0.5))


def pixel_model_available() -> bool:
    try:
        _model()
        return True
    except Exception:
        return False


def tamper_heatmap(img: np.ndarray) -> np.ndarray:
    """Per-pixel forgery probability at the input resolution."""
    s = WORK_W / img.shape[1]
    work = cv2.resize(img, (WORK_W, max(32, int(round(img.shape[0] * s)))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    H, W = work.shape[:2]
    padded = cv2.copyMakeBorder(work, 0, (-H) % 32, 0, (-W) % 32, cv2.BORDER_REFLECT)
    x = (padded.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
    logits = _model()(np.ascontiguousarray(x))[0, 0, :H, :W]
    prob = 1.0 / (1.0 + np.exp(-logits))
    return cv2.resize(prob.astype(np.float32), (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)


def _overlap(box, zone) -> float:
    x0, y0, x1, y1 = box
    a0, b0, a1, b1 = zone
    iw, ih = max(0, min(x1, a1) - max(x0, a0)), max(0, min(y1, b1) - max(y0, b0))
    return iw * ih / max(1.0, (x1 - x0) * (y1 - y0))


def _ink_ratio(img: np.ndarray, box) -> float:
    x0, y0, x1, y1 = [int(v) for v in box]
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return float(((hsv[..., 1] > 70) & (hsv[..., 2] < 210)).mean())


def pixel_regions(img: np.ndarray, prob: np.ndarray, zones: dict, policy: dict) -> tuple[float, list[dict]]:
    tp = policy['tampering']
    calib = _calibration()
    score = float(cv2.blur(prob, (7, 7)).max())
    binary = (prob > calib.get('region_threshold', tp['pixel_region_threshold'])).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    min_area = tp['min_region_area_ratio'] * prob.size
    regions = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area:
            continue
        box = [int(x), int(y), int(x + w), int(y + h)]
        peak = float(prob[y:y + h, x:x + w].max())
        label, target = 'unattributed_alteration', None
        if zones.get('portrait') and _overlap(box, zones['portrait']) > 0.3:
            label = 'photo_replacement'
        elif zones.get('mrz') and _overlap(box, zones['mrz']) > 0.3:
            label = 'mrz_alteration'
        else:
            best = max(((_overlap(box, fb), f) for f, fb in (zones.get('fields') or {}).items()), default=(0, None))
            if best[0] > 0.2:
                label, target = 'text_manipulation', best[1]
            elif _ink_ratio(img, box) > 0.12:
                label = 'stamp_forgery'
            elif w > 3 * h:
                label = 'text_manipulation'
        regions.append(dict(box=box, area_ratio=round(float(area) / prob.size, 5), peak=round(peak, 3), label=label, field=target))
    regions.sort(key=lambda r: -r['peak'])
    return score, regions


# ---------------------------------------------------------------------------
# Copy-move of stamp ink
# ---------------------------------------------------------------------------

def stamp_copy_move(img: np.ndarray, exclude: list | None = None, min_matches: int = 12) -> list[dict]:
    scale = min(1.0, 1400 / max(img.shape[:2]))
    work = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    ink = ((hsv[..., 1] > 70) & (hsv[..., 2] < 215)).astype(np.uint8) * 255
    ink = cv2.dilate(ink, np.ones((9, 9), np.uint8))
    for x0, y0, x1, y1 in exclude or []:
        ink[int(y0 * scale):int(y1 * scale), int(x0 * scale):int(x1 * scale)] = 0
    if (ink > 0).mean() < 0.002:
        return []
    sift = cv2.SIFT_create(nfeatures=3000)
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    kps, desc = sift.detectAndCompute(gray, ink)
    if desc is None or len(kps) < min_matches * 2:
        return []
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pts = np.array([k.pt for k in kps], np.float32)
    src_list, dst_list = [], []
    for row in matcher.knnMatch(desc, desc, k=6):
        # Skip the query itself and SIFT duplicates at the same spot, then apply the ratio test.
        others = [m for m in row if np.linalg.norm(pts[m.trainIdx] - pts[m.queryIdx]) > 10]
        if len(others) >= 2 and others[0].distance < 0.65 * others[1].distance:
            p, q = pts[others[0].queryIdx], pts[others[0].trainIdx]
            if np.linalg.norm(p - q) > 40 and (p[0], p[1]) < (q[0], q[1]):
                src_list.append(p); dst_list.append(q)
    if len(src_list) < min_matches:
        return []
    src_all, dst_all = np.array(src_list), np.array(dst_list)
    found = []
    remaining = np.ones(len(src_all), bool)
    box = lambda P: [int(P[:, 0].min() / scale), int(P[:, 1].min() / scale), int(P[:, 0].max() / scale), int(P[:, 1].max() / scale)]
    for _ in range(3):
        idx = np.nonzero(remaining)[0]
        if len(idx) < min_matches:
            break
        # A clone may be rotated or rescaled: fit a similarity transform instead of a pure shift.
        M, inl = cv2.estimateAffinePartial2D(src_all[idx], dst_all[idx], method=cv2.RANSAC, ransacReprojThreshold=5.0,
                                             maxIters=3000, confidence=0.995)
        if M is None or inl is None or inl.sum() < min_matches:
            break
        inl = idx[inl.ravel().astype(bool)]
        remaining[inl] = False
        s_pts, d_pts = src_all[inl], dst_all[inl]
        scale_est = float(np.sqrt(abs(np.linalg.det(M[:, :2]))))
        w_, h_ = float(np.ptp(s_pts[:, 0])), float(np.ptp(s_pts[:, 1]))
        # Stamps are compact 2-D marks; repeated printed words (same font, same text) form thin strips.
        if not 0.7 < scale_est < 1.4 or min(w_, h_) < 50 or max(w_, h_) > 3 * min(w_, h_):
            continue
        texture = _texture_correlation(gray, s_pts, d_pts, ink)
        # The same rubber stamp used twice matches in shape but not in ink texture;
        # a digital clone copies the texture too.
        if texture >= 0.6:
            found.append(dict(source_box=box(s_pts), clone_box=box(d_pts), matches=int(len(inl)),
                              texture_correlation=round(texture, 3),
                              rotation_deg=round(float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), 1), scale=round(scale_est, 3)))
    return found


def _texture_correlation(gray: np.ndarray, src: np.ndarray, dst: np.ndarray, ink: np.ndarray) -> float:
    """Normalised correlation of high-pass ink texture after aligning source onto clone."""
    M, inliers = cv2.estimateAffinePartial2D(src.astype(np.float32), dst.astype(np.float32), method=cv2.RANSAC,
                                             ransacReprojThreshold=4.0)
    if M is None or inliers is None or inliers.sum() < 6:
        return 0.0
    x0, y0 = np.maximum(dst.min(axis=0).astype(int) - 10, 0)
    x1, y1 = dst.max(axis=0).astype(int) + 10
    warped = cv2.warpAffine(gray, M, (gray.shape[1], gray.shape[0]), flags=cv2.INTER_LINEAR)
    a = gray[y0:y1, x0:x1].astype(np.float32)
    b = warped[y0:y1, x0:x1].astype(np.float32)
    m = ink[y0:y1, x0:x1] > 0
    if a.size == 0 or m.sum() < 200:
        return 0.0
    hp = lambda z: z - cv2.GaussianBlur(z, (0, 0), 2.0)
    a, b = hp(a)[m], hp(b)[m]
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _jpeg_grid_offset(gray: np.ndarray) -> tuple[int, int, float]:
    """Blockiness per 8x8 grid phase. Strongest phase != (0,0) means the image was cropped or
    pasted after JPEG compression."""
    g = gray.astype(np.float32)
    dx = np.abs(np.diff(g, axis=1)).mean(axis=0)
    dy = np.abs(np.diff(g, axis=0)).mean(axis=1)
    bx = np.array([dx[k::8].mean() - dx.mean() for k in range(8)])
    by = np.array([dy[k::8].mean() - dy.mean() for k in range(8)])
    ox, oy = int(bx.argmax()), int(by.argmax())
    strength = float(max(bx.max(), by.max()) / (dx.mean() + dy.mean() + 1e-6))
    return (ox + 1) % 8, (oy + 1) % 8, strength


def analyze_metadata(raw: bytes | None) -> dict:
    out = dict(format=None, exif_present=False, software=None, camera=None, findings=[])
    if not raw:
        return out
    try:
        im = Image.open(io.BytesIO(raw))
    except Exception:
        out['findings'].append(dict(code='unreadable_container', severity='low', message='Image container could not be parsed.'))
        return out
    out['format'] = im.format
    exif = {ExifTags.TAGS.get(k, k): v for k, v in (im.getexif() or {}).items()}
    try:
        exif.update({ExifTags.TAGS.get(k, k): v for k, v in im.getexif().get_ifd(0x8769).items()})
    except Exception:
        pass
    out['exif_present'] = bool(exif)
    software = str(exif.get('Software') or '')
    xmp = raw[:200_000].decode('latin-1', errors='ignore')
    tool = re.search(r'(?:CreatorTool|softwareAgent)[=>"\s]+([^"<]{2,80})', xmp)
    tools = ' '.join([software, tool.group(1) if tool else '']).strip()
    out['software'] = tools or None
    out['camera'] = ' '.join(str(exif.get(k) or '') for k in ('Make', 'Model')).strip() or None
    hit = next((e for e in EDITORS if e in tools.lower()), None)
    if hit:
        out['findings'].append(dict(code='editing_software', severity='medium',
                                    message=f'Image was last saved by editing software ({tools}).'))
    if 'photoshop:History' in xmp or 'stEvt:action="saved"' in xmp:
        out['findings'].append(dict(code='edit_history', severity='medium', message='Embedded XMP edit history present.'))
    dt, dto = exif.get('DateTime'), exif.get('DateTimeOriginal')
    if dt and dto and str(dt)[:10] != str(dto)[:10]:
        out['findings'].append(dict(code='timestamp_mismatch', severity='low',
                                    message=f'File modified ({dt}) on a different day from capture ({dto}).'))
    if im.format == 'JPEG':
        q = getattr(im, 'quantization', None) or {}
        if q:
            t0 = np.array(list(q.get(0, [])), float)
            if t0.size:
                out['jpeg_quality_estimate'] = int(np.clip(100 - (t0.mean() - 1) * 1.1, 1, 100))
        gray = np.asarray(im.convert('L'))
        if min(gray.shape) >= 64:
            ox, oy, strength = _jpeg_grid_offset(gray)
            out['jpeg_grid'] = dict(offset=[ox, oy], strength=round(strength, 3))
            if (ox, oy) != (0, 0) and strength > 0.08:
                out['findings'].append(dict(code='jpeg_grid_misaligned', severity='low',
                                            message='JPEG block grid is shifted: image was cropped or composited after compression.'))
    if not out['exif_present'] and im.format in ('JPEG', 'HEIC', 'MPO'):
        out['findings'].append(dict(code='metadata_stripped', severity='info',
                                    message='No EXIF metadata (common after messaging apps or editing).'))
    return out


# ---------------------------------------------------------------------------
# Combined analysis
# ---------------------------------------------------------------------------

def analyze_tampering(img: np.ndarray | str, *, raw_bytes: bytes | None = None, zones: dict | None = None,
                      validation: dict | None = None, portrait: dict | None = None, policy: dict | None = None) -> dict:
    """``img`` should be the rectified document. ``zones`` maps 'portrait', 'mrz' and 'fields'
    (name -> box) in that image's coordinates, so pixel findings can be attributed."""
    if isinstance(img, str):
        path = img
        img = cv2.imread(path)
        if img is None:
            raise ValueError('Image could not be read')
        raw_bytes = raw_bytes or open(path, 'rb').read()
    policy = policy or load_policy()
    zones = zones or {}
    uc = {k: dict(detected=False, evidence=[]) for k in USE_CASES}
    out = dict(use_cases=uc, regions=[], copy_move=[], metadata={}, pixel_model=None, pixel_score=None,
               image_threshold=None)

    if pixel_model_available():
        prob = tamper_heatmap(img)
        _, regions = pixel_regions(img, prob, zones, policy)
        score, thr = document_forgery_score(prob)
        out.update(pixel_model=_model().backend, pixel_score=round(score, 4), image_threshold=round(float(thr), 4), regions=regions)
        out['_heatmap'] = prob
        if score >= thr:
            for r in regions:
                case = {'photo_replacement': 'photo_replacement', 'stamp_forgery': 'stamp_forgery'}.get(r['label'], 'text_manipulation')
                where = f" over field '{r['field']}'" if r.get('field') else ' in the MRZ' if r['label'] == 'mrz_alteration' else ''
                uc[case]['evidence'].append(dict(source='pixel_model', severity='high' if r['peak'] > 0.8 else 'medium',
                                                 message=f'Altered pixels detected{where} (peak {r["peak"]:.2f}).', box=r['box']))

    exclude = [zones['mrz']] if zones.get('mrz') else []
    cm = stamp_copy_move(img, exclude=exclude)
    out['copy_move'] = cm
    for c in cm:
        uc['stamp_forgery']['evidence'].append(dict(source='copy_move', severity='high',
                                                    message=f'Stamp ink duplicated ({c["matches"]} matching keypoints): cloned stamp.',
                                                    box=c['clone_box']))

    meta = analyze_metadata(raw_bytes)
    out['metadata'] = meta
    for f in meta['findings']:
        if f['severity'] != 'info':
            uc['metadata']['evidence'].append(dict(source='metadata', severity=f['severity'], message=f['message']))

    for chk in (validation or {}).get('checks', []):
        if chk['status'] != 'FAIL':
            continue
        if chk['category'] == 'consistency' and (chk['id'].startswith('viz_mrz_') or chk['id'] == 'printed_dates_within_mrz_range'):
            uc['text_manipulation']['evidence'].append(dict(source='mrz_viz_consistency', severity='high', message=chk['message']))
        elif chk['category'] == 'integrity' and chk['id'].startswith('mrz_check_'):
            uc['text_manipulation']['evidence'].append(dict(source='mrz_check_digits', severity=chk['severity'], message=chk['message']))
    if portrait is not None:
        for issue in portrait.get('issues', []):
            if 'ghost image' in issue or 'no face detected' in issue:
                uc['photo_replacement']['evidence'].append(dict(source='portrait_analysis', severity='high' if 'ghost' in issue else 'medium',
                                                                message=issue))

    for case, d in uc.items():
        d['detected'] = any(e['severity'] in ('high', 'critical') for e in d['evidence']) or len(d['evidence']) >= 2
    out['tampering_detected'] = any(d['detected'] for d in uc.values() if d is not uc['metadata']) or uc['metadata']['detected']
    return out
