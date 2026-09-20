from pathlib import Path

import cv2
import pytest

from src import face_verification as fv

try:
    SAMPLE = Path(__import__('matplotlib').get_data_path()) / 'sample_data' / 'grace_hopper.jpg'
except ImportError:
    SAMPLE = Path('missing-matplotlib-sample.jpg')

pytestmark = pytest.mark.skipif(not (fv.models_available() and SAMPLE.exists()), reason='face models or sample photo missing')


def test_same_person_matches_across_capture_changes():
    from datetime import date
    from synthdocs import render_document
    photo = cv2.imread(str(SAMPLE))
    doc = render_document('passport', seed=42, today=date(2026, 9, 16))
    x0, y0, x1, y1 = doc.portrait_box
    doc.image[y0:y1, x0:x1] = cv2.resize(photo[40:560, 30:480], (x1 - x0, y1 - y0))
    live = cv2.copyMakeBorder(cv2.GaussianBlur(cv2.convertScaleAbs(cv2.flip(photo, 1), alpha=0.7, beta=10), (3, 3), 1),
                              100, 100, 200, 200, cv2.BORDER_CONSTANT, value=(60, 70, 80))
    result = fv.verify_faces(doc.image, live)
    assert result['decision'] == 'MATCH' and result['similarity'] > 0.6


def test_missing_live_face_is_inconclusive():
    photo = cv2.imread(str(SAMPLE))
    blank = photo.copy()
    blank[:] = 128
    result = fv.verify_faces(photo, blank)
    assert result['decision'] == 'INCONCLUSIVE'
    assert any('no face' in i for i in result['issues'])
