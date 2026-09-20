"""Acceptance check on the three reference captures (clean, low-light, tampered + low-light).

    python run_tests.py                     # acceptance samples
    FULL_STRESS_TEST=1 python run_tests.py  # every sample in samples/generated
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from src.mrz_reader import model_available
from src.ocr_pipeline import OCRPipeline
from src.runtime_utils import find_tesseract

ROOT = Path(__file__).parent
OUT = ROOT / 'outputs'
OUT.mkdir(exist_ok=True)
ACCEPT = ['identity_01_00_good.jpg', 'identity_01_01_low_light.jpg', 'identity_01_text_patch_low_light.jpg']
EXPECTED = {'passport_number': 'K71M4P829', 'date_of_birth': '020214', 'date_of_expiry': '350918'}


def main():
    tess = find_tesseract()
    if not model_available():
        print('ERROR: trained MRZ reader not found in models/. Run: python -m training.train_mrz_reader')
        return 2
    full = os.getenv('FULL_STRESS_TEST', '').lower() in {'1', 'true', 'yes'}
    files = sorted((ROOT / 'samples' / 'generated').glob('identity_*')) if full else [ROOT / 'samples' / 'generated' / n for n in ACCEPT]
    pipe = OCRPipeline(fast_mode=True)
    report = dict(tesseract=tess, mode='full' if full else 'acceptance', tests=[])
    ok = True
    for p in files:
        r = pipe.process(p)
        item = dict(file=str(p.relative_to(ROOT)).replace('\\', '/'), confidence=r.confidence, mrz_valid=r.mrz['valid_format'],
                    mrz_check=r.mrz['overall_check_digit_valid'], mrz_raw_check=r.mrz['raw_overall_check_digit_valid'],
                    repairs=r.mrz['repairs'], quality=r.capture_quality, warnings=r.warnings, elapsed_ms=r.elapsed_ms,
                    fields={k: r.fields.get(k) for k in EXPECTED})
        report['tests'].append(item)
        passed = r.mrz['overall_check_digit_valid'] and all(r.fields.get(k) == v for k, v in EXPECTED.items())
        print(f"{item['file']}: {'PASS' if passed else 'FAIL'} conf={r.confidence:.1f} quality={r.capture_quality['quality_score']:.1f} "
              f"mrz_check={r.mrz['overall_check_digit_valid']} {r.elapsed_ms} ms")
        if not full and not passed:
            ok = False
    (OUT / 'test_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Acceptance result:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
