from datetime import date

from src.dates import mrz_date, parse_date
from src.mrz_utils import generate_mrz
from src.validation import validate_document

TODAY = date(2026, 9, 16)


def _mrz(doc_code='P', fmt='TD3', number='K71M4P829', dob='020214', expiry='350918', optional=''):
    from src.mrz_utils import parse_mrz
    lines = generate_mrz(fmt, document_code=doc_code, issuing_state='UTO', document_number=number, nationality='IND',
                         date_of_birth=dob, sex='M', date_of_expiry=expiry, surname='GUPTA', given_names='AKSHIT',
                         optional_data=optional)
    return parse_mrz(lines).to_dict()


def _status(result, cid):
    return next((c['status'] for c in result['checks'] if c['id'] == cid), None)


def test_parse_date_formats():
    for s in ('14 FEB 2002', '14/02/2002', '2002-02-14', '14.02.2002', '14-02-2002'):
        assert parse_date(s) == date(2002, 2, 14)
    assert parse_date('-03-2019') is None
    assert mrz_date('020214', 'birth', TODAY) == date(2002, 2, 14)
    assert mrz_date('990708', 'birth', TODAY) == date(1999, 7, 8)
    assert mrz_date('350918', 'expiry', TODAY) == date(2035, 9, 18)


def test_valid_passport_passes():
    rec = dict(document_type='passport', mrz=_mrz(),
               fields=dict(document_number='K71M4P829', nationality='IND', sex='M', surname='GUPTA', given_names='AKSHIT',
                           date_of_birth='2002-02-14', date_of_expiry='2035-09-18', date_of_issue='2025-09-19'),
               viz_fields=dict(document_number='K71M4P829', date_of_birth='2002-02-14', surname='GUPTA'))
    r = validate_document(rec, today=TODAY)
    assert r['valid'], [c for c in r['checks'] if c['status'] == 'FAIL']
    assert _status(r, 'mrz_check_composite') == 'PASS'
    assert _status(r, 'viz_mrz_document_number') == 'PASS'


def test_expired_and_impossible_dates_fail():
    rec = dict(document_type='passport', mrz=_mrz(expiry='240101'),
               fields=dict(document_number='K71M4P829', date_of_birth='2027-01-01', date_of_expiry='2024-01-01',
                           date_of_issue='2010-01-01'))
    r = validate_document(rec, today=TODAY)
    assert not r['valid']
    assert _status(r, 'not_expired') == 'FAIL'
    assert _status(r, 'dob_not_future') == 'FAIL'
    assert _status(r, 'validity_period') == 'FAIL'  # 14-year passport


def test_printed_zone_text_manipulation_detected_but_ocr_lookalikes_tolerated():
    rec = dict(document_type='passport', mrz=_mrz(),
               fields=dict(document_number='K71M4P829'),
               viz_fields=dict(document_number='K71M4PB29', date_of_expiry='2038-12-31'),
               viz_confidences=dict(document_number=90, date_of_expiry=92))
    r = validate_document(rec, today=TODAY)
    assert _status(r, 'viz_mrz_document_number') == 'PASS'   # 8 vs B is an OCR look-alike
    assert _status(r, 'viz_mrz_date_of_expiry') == 'FAIL'    # printed expiry was altered


def test_visa_entry_rules_and_passport_link():
    visa_mrz = _mrz(doc_code='V<', fmt='MRVA', number='VX1234567', expiry='270101', optional='P1234567')
    rec = dict(document_type='visa', mrz=visa_mrz,
               fields=dict(document_number='VX1234567', valid_from='2026-10-01', date_of_expiry='2027-01-01',
                           number_of_entries='SINGLE', duration_of_stay_days=30, passport_number='P1234567',
                           surname='GUPTA', date_of_birth='2002-02-14', nationality='IND'))
    passport = dict(fields=dict(document_number='Z9999999', surname='GUPTA', date_of_birth='2002-02-14', nationality='IND',
                                date_of_expiry='2026-10-20'))
    r = validate_document(rec, today=TODAY, companion=passport,
                          context=dict(entry_date='2026-09-16', prior_entries=1, intended_stay_days=60))
    assert _status(r, 'visa_entry_window') == 'FAIL'       # not valid until October
    assert _status(r, 'visa_entries_remaining') == 'FAIL'  # single entry already used
    assert _status(r, 'visa_stay_duration') == 'FAIL'      # 60 > 30 days
    assert _status(r, 'visa_passport_link') == 'FAIL'      # issued to a different passport


def test_legacy_signature_still_supported():
    r = validate_document({'passport_number': 'K71M4P829', 'document_number': 'K71M4P829', 'nationality': 'UTO',
                           'date_of_birth': '2002-02-14', 'date_of_expiry': '2035-09-18', 'sex': 'M'}, 'passport', today=TODAY)
    assert r['valid']
