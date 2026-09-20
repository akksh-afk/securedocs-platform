import itertools

import numpy as np
import pytest

from src.mrz_utils import check_digit, generate_mrz, generate_td3_mrz, parse_mrz

COMMON = dict(document_number='K71M4P829', nationality='UTO', date_of_birth='020214', sex='M',
              date_of_expiry='350918', surname='GUPTA', given_names='AKSHIT')


def test_td3_roundtrip():
    lines = generate_td3_mrz(passport_number='K71M4P829', nationality='UTO', date_of_birth='020214', sex='M',
                             date_of_expiry='350918', surname='GUPTA', given_names='AKSHIT')
    assert len(lines) == 2 and all(len(x) == 44 for x in lines)
    parsed = parse_mrz(lines)
    assert parsed.valid_format and parsed.overall_check_digit_valid
    assert parsed.passport_number == 'K71M4P829'
    assert parsed.nationality == 'UTO'
    assert parsed.surname == 'GUPTA' and parsed.given_names == 'AKSHIT'


@pytest.mark.parametrize('fmt,code,shape', [('TD3', 'P', (2, 44)), ('TD2', 'I', (2, 36)), ('MRVA', 'V', (2, 44)),
                                             ('MRVB', 'V', (2, 36)), ('TD1', 'ID', (3, 30))])
def test_all_icao_formats_roundtrip(fmt, code, shape):
    lines = generate_mrz(fmt, document_code=code, issuing_state='UTO', optional_data='A1234567', **COMMON)
    assert (len(lines), len(lines[0])) == shape
    m = parse_mrz(lines)
    assert m.format == fmt and m.valid_format and m.overall_check_digit_valid
    assert m.document_number == 'K71M4P829' and m.date_of_birth == '020214' and m.surname == 'GUPTA'


def test_td3_ocr_checksum_repair():
    lines = generate_td3_mrz(passport_number='K71M4P829', nationality='UTO', date_of_birth='020214', sex='M',
                             date_of_expiry='350918', surname='GUPTA', given_names='AKSHIT')
    noisy = [lines[0], lines[1][:42] + 'Q8']
    parsed = parse_mrz(noisy)
    assert parsed.valid_format and parsed.overall_check_digit_valid
    assert not parsed.raw_overall_check_digit_valid
    assert parsed.repair_applied and parsed.repairs


def test_forged_field_with_recomputed_digit_is_rejected():
    """Changing the DOB and its own check digit but not the composite digit must fail."""
    lines = generate_td3_mrz(passport_number='K71M4P829', nationality='UTO', date_of_birth='020214', sex='M',
                             date_of_expiry='350918', surname='GUPTA', given_names='AKSHIT')
    l2 = lines[1]
    forged = l2[:13] + '990214' + check_digit('990214') + l2[20:]
    m = parse_mrz([lines[0], forged])
    assert m.valid_format
    assert m.check_digits['date_of_birth'] is True
    assert m.composite_check_valid is False
    assert m.overall_check_digit_valid is False


def test_letter_digit_confusions_repaired_only_when_confirmed():
    lines = generate_td3_mrz(passport_number='K71M4P829', nationality='UTO', date_of_birth='020214', sex='M',
                             date_of_expiry='350918', surname='GUPTA', given_names='AKSHIT')
    ocr = [lines[0].replace('GUPTA', 'GUP7A'), lines[1].replace('020214', 'O2O214')]
    m = parse_mrz(ocr)
    assert m.overall_check_digit_valid and m.date_of_birth == '020214' and m.surname == 'GUPTA'


def test_ambiguous_repair_is_not_guessed():
    lines = generate_td3_mrz(passport_number='K71M4P829', nationality='UTO', date_of_birth='020214', sex='M',
                             date_of_expiry='350918', surname='GUPTA', given_names='AKSHIT')
    # B->8 and 2->Z both satisfy the mod-10 digit: without recogniser probabilities, do not guess.
    m = parse_mrz([lines[0], lines[1].replace('K71M4P829', 'K71M4PB29')])
    assert not m.overall_check_digit_valid


def test_ctc_fixed_length_matches_brute_force():
    from src.mrz_reader import MRZ_ALPHABET, ctc_fixed_length
    rng = np.random.default_rng(1)

    def collapse(path):
        out, prev = [], 0
        for k in path:
            if k != prev and k != 0:
                out.append(k)
            prev = k
        return out

    for _ in range(60):
        T, C, L = int(rng.integers(3, 7)), 4, int(rng.integers(1, 4))
        p = rng.dirichlet(np.ones(C) * 0.6, size=T)
        best = None
        for path in itertools.product(range(C), repeat=T):
            lab = collapse(path)
            if len(lab) == L:
                v = float(np.sum(np.log(p[np.arange(T), path])))
                if best is None or v > best[0]:
                    best = (v, lab)
        if best is None or L > T:
            continue
        got = ctc_fixed_length(p, L)
        assert got is not None
        assert [MRZ_ALPHABET.index(c) + 1 for c in got[0]] == best[1]
