"""Create demo inputs for the console/API in samples/demo/ (all synthetic, watermarked).

    python generate_demo_documents.py
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import cv2

from synthdocs import apply_tamper, render_document, simulate_capture

ROOT = Path(__file__).parent
OUT = ROOT / 'samples' / 'demo'
TODAY = date.today()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list = []

    def save(name, img, notes):
        cv2.imwrite(str(OUT / name), img, [cv2.IMWRITE_JPEG_QUALITY, 93])
        manifest.append(dict(file=f'samples/demo/{name}', notes=notes))

    valid = dict(date_of_expiry=TODAY + timedelta(days=2000), date_of_issue=TODAY - timedelta(days=900))
    for i, dtype in enumerate(('passport', 'visa', 'national_id', 'driving_licence', 'residence_permit')):
        over = dict(valid)
        if dtype == 'visa':
            over.update(valid_from=TODAY - timedelta(days=5), date_of_expiry=TODAY + timedelta(days=170),
                        date_of_issue=TODAY - timedelta(days=12))
        if dtype == 'driving_licence':
            over.update(date_of_birth=date(1990, 5, 17))
        doc = render_document(dtype, seed=100 + i, today=TODAY, overrides=over)
        save(f'{dtype}_genuine_scan.jpg', doc.image, f'Genuine {dtype}, flat scan.')
        cap, _, _, _ = simulate_capture(doc.image, random.Random(1), conditions=['glare', 'jpeg'], perspective=0.04)
        save(f'{dtype}_genuine_phone.jpg', cap, f'Genuine {dtype}, phone capture with glare.')

    for kind in ('photo_replace', 'date_replace', 'stamp_clone', 'stamp_forge', 'mrz_edit', 'legacy_patch'):
        doc = render_document('visa' if kind == 'stamp_clone' else 'passport', seed=500, today=TODAY, overrides=dict(valid))
        apply_tamper(doc, kind, random.Random(11))
        save(f'forged_{kind}.jpg', doc.image, f'Forgery: {doc.tamper_ops[-1]}')

    passport = render_document('passport', seed=900, today=TODAY, overrides=dict(valid))
    p = passport.fields
    visa = render_document('visa', seed=901, today=TODAY, overrides=dict(
        surname=p['surname'], given_names=p['given_names'], nationality=p['nationality'], date_of_birth=p['date_of_birth'],
        sex=p['sex'], passport_number=p['document_number'], valid_from=TODAY - timedelta(days=3),
        date_of_expiry=TODAY + timedelta(days=90), date_of_issue=TODAY - timedelta(days=10)))
    save('pair_passport.jpg', passport.image, 'Passport presented with pair_visa.jpg.')
    save('pair_visa.jpg', visa.image, 'Visa linked to pair_passport.jpg.')
    save('pair_visa_other_person.jpg', render_document('visa', seed=902, today=TODAY).image,
         'Visa belonging to someone else: the link check fails with pair_passport.jpg.')
    save('passport_expired.jpg', render_document('passport', seed=903, today=TODAY, overrides=dict(
        date_of_expiry=TODAY - timedelta(days=40), date_of_issue=TODAY - timedelta(days=3690))).image, 'Expired passport.')

    face = Path(__import__('matplotlib').get_data_path()) / 'sample_data' / 'grace_hopper.jpg'
    if face.exists():
        photo = cv2.imread(str(face))
        doc = render_document('passport', seed=950, today=TODAY, overrides=dict(valid))
        x0, y0, x1, y1 = doc.portrait_box
        doc.image[y0:y1, x0:x1] = cv2.resize(photo[40:560, 30:480], (x1 - x0, y1 - y0))
        save('face_passport.jpg', doc.image, 'Passport with a real face photo (public matplotlib sample image) pasted over the '
             'synthetic portrait, for face matching. The pixel forgery model may flag the pasted photo.')
        live = cv2.copyMakeBorder(cv2.flip(cv2.convertScaleAbs(photo, alpha=0.8, beta=15), 1), 80, 80, 160, 160,
                                  cv2.BORDER_CONSTANT, value=(70, 60, 50))
        save('face_live_same_person.jpg', live, 'Live capture matching face_passport.jpg.')
    (OUT / 'manifest.json').write_text(json.dumps(dict(disclaimer='Synthetic, watermarked test documents only.', samples=manifest), indent=1))
    print(f'wrote {len(manifest)} demo files to {OUT}')


if __name__ == '__main__':
    main()
