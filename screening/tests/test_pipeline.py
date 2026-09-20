from pathlib import Path

import cv2
import pytest

from src.capture_robustness import quality_metrics
from src.mrz_reader import model_available
from src.ocr_pipeline import OCRPipeline
from src.runtime_utils import find_tesseract

ROOT = Path(__file__).parent.parent

requires_models = pytest.mark.skipif(not model_available(), reason='trained MRZ reader not present (run training)')


def test_imports_and_tesseract_available():
    assert find_tesseract()


@requires_models
def test_hard_acceptance_samples():
    pipe = OCRPipeline(fast_mode=True)
    expected = {"passport_number": "K71M4P829", "date_of_birth": "020214", "date_of_expiry": "350918"}
    for name in ("identity_01_00_good.jpg", "identity_01_01_low_light.jpg", "identity_01_text_patch_low_light.jpg"):
        r = pipe.process(ROOT / 'samples' / 'generated' / name)
        assert r.mrz['valid_format'], name
        assert r.mrz['overall_check_digit_valid'], name
        for k, v in expected.items():
            assert r.fields.get(k) == v, (name, k, r.fields.get(k))
        assert r.confidence >= 75, (name, r.confidence)
    worst = ROOT / 'samples' / 'generated' / 'identity_01_13_combo_worst.jpg'
    q = quality_metrics(cv2.imread(str(worst)))
    assert 0 <= q.quality_score <= 100


def test_clean_capture_has_no_noise_warning():
    q = quality_metrics(cv2.imread(str(ROOT / 'samples' / 'generated' / 'identity_01_00_good.jpg')))
    assert not any('noise' in w.lower() for w in q.warnings), q.warnings


def test_cctns_style_capture_quality():
    q = quality_metrics(cv2.imread(str(ROOT / 'samples' / 'generated' / 'cctns_style_01_07_combo_worst.jpg')))
    assert 0 <= q.quality_score <= 100
    assert q.warnings


@requires_models
def test_synthetic_documents_of_every_mrz_type():
    import random
    from datetime import date
    from synthdocs import render_document
    pipe = OCRPipeline(fast_mode=True)
    for i, dtype in enumerate(['passport', 'visa', 'national_id', 'residence_permit']):
        doc = render_document(dtype, seed=31337 + i, today=date(2026, 9, 16))
        r = pipe.process(doc.image, document_type=dtype, today=date(2026, 9, 16))
        assert r.mrz['overall_check_digit_valid'], (dtype, r.mrz['normalized_lines'])
        # Check-digit-protected fields must be exact. Names carry no check digit, so allow one
        # recognition slip (they are also cross-checked against the printed zone).
        from src.mrz_utils import parse_mrz
        from rapidfuzz.distance import Levenshtein
        expected = parse_mrz(doc.mrz_lines)
        for key in ('document_number', 'date_of_birth', 'date_of_expiry', 'nationality', 'gender'):
            assert r.mrz[key] == getattr(expected, key), (dtype, key)
        assert Levenshtein.distance(r.mrz['surname'] + r.mrz['given_names'], expected.surname + expected.given_names) <= 1
        assert r.document_type == dtype
