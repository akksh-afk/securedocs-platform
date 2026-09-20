import json

import numpy as np

from src.audit import AuditLog
from src.registry import Registry
from src.risk import assess_risk, evidence


def test_watchlist_matches_document_and_person(tmp_path):
    reg = Registry(tmp_path / 'r.sqlite')
    reg.add_watch('LOST_STOLEN', 'reported stolen', document_number='K71M4P829', issuing_state='UTO')
    reg.add_watch('BLACKLIST', 'deportation order', surname='SINGH', given_names='RHEA', date_of_birth='1999-07-08')
    hits = reg.check_watchlist(dict(document_number='K71M4P829', issuing_state='UTO'))
    assert hits and hits[0]['list_type'] == 'LOST_STOLEN'
    hits = reg.check_watchlist(dict(document_number='NEW12345', surname='SINGH', given_names='RHEA ANIKA', date_of_birth='1999-07-08'))
    assert any(h['match'] == 'name_and_dob' for h in hits)
    assert not reg.check_watchlist(dict(document_number='CLEAN123', surname='SINGH', date_of_birth='1980-01-01'))


def test_multiple_identity_patterns(tmp_path):
    reg = Registry(tmp_path / 'r.sqlite')
    face = np.random.default_rng(0).normal(size=128).astype(np.float32)
    face /= np.linalg.norm(face)
    reg.enroll('s1', dict(document_type='passport', document_number='P1111111', surname='GUPTA', given_names='AKSHIT',
                          date_of_birth='2002-02-14', nationality='IND'), face)
    # Same face, different name and birth date.
    other = dict(document_type='passport', document_number='P2222222', surname='SHARMA', given_names='DEV',
                 date_of_birth='1990-01-01', nationality='NPL')
    patterns = {c['pattern'] for c in reg.find_conflicts(other, face + 0.01)}
    assert 'same_face_different_identity' in patterns
    # Same document number presented by a different person.
    patterns = {c['pattern'] for c in reg.find_conflicts(dict(other, document_number='P1111111'), None)}
    assert 'document_shared_by_different_identities' in patterns
    # Genuine repeat traveller: no conflict.
    same = dict(document_type='passport', document_number='P1111111', surname='GUPTA', given_names='AKSHIT',
                date_of_birth='2002-02-14', nationality='IND')
    assert not reg.find_conflicts(same, face)


def test_audit_chain_detects_edits(tmp_path):
    log = AuditLog(tmp_path / 'a.jsonl')
    for i in range(3):
        log.append(dict(event='screening', screening_id=f's{i}', disposition='CLEAR'))
    assert log.verify() == dict(valid=True, records=3, head=log.read()[-1]['hash'])
    lines = (tmp_path / 'a.jsonl').read_text().splitlines()
    rec = json.loads(lines[1])
    rec['disposition'] = 'REFER_TO_SUPERVISOR'
    lines[1] = json.dumps(rec, sort_keys=True)
    (tmp_path / 'a.jsonl').write_text('\n'.join(lines) + '\n')
    v = log.verify()
    assert not v['valid'] and v['broken_at_seq'] == 2


def test_risk_dispositions():
    clean = assess_risk([], capture_quality=90, mrz_expected=True, mrz_verified=True, face_checked=True)
    assert clean['disposition'] == 'CLEAR' and clean['risk_band'] == 'LOW'
    hit = assess_risk([evidence('watchlist', 'critical', 'watchlist_lost_stolen', 'stolen', 'registry')],
                      capture_quality=90, mrz_expected=True, mrz_verified=True)
    assert hit['risk_band'] == 'HIGH' and hit['disposition'] == 'REFER_TO_SUPERVISOR'
    moderate = assess_risk([evidence('validity', 'medium', 'passport_remaining_validity', 'x', 'validation', 'WARN'),
                            evidence('consistency', 'high', 'viz_mrz_surname', 'y', 'validation')],
                           capture_quality=80, mrz_expected=True, mrz_verified=True)
    assert moderate['risk_band'] == 'MEDIUM' and moderate['disposition'] == 'SECONDARY_INSPECTION'
    unreadable = assess_risk([], capture_quality=20, mrz_expected=True, mrz_verified=False)
    assert unreadable['disposition'] == 'RECAPTURE'
