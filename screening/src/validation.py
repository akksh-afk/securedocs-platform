"""Module 2: document validation against official document standards and screening policy."""
from __future__ import annotations

import re
from datetime import date, timedelta

from rapidfuzz import fuzz

from .config import load_policy
from .dates import mrz_date, parse_date
from .icao import is_valid_code

MRZ_TYPES = {'passport': 'TD3', 'visa': 'MRVA', 'national_id': 'TD1', 'residence_permit': 'TD1'}
MRZ_CODE_OK = {'passport': ('P',), 'visa': ('V',), 'national_id': ('I', 'A', 'C'), 'residence_permit': ('I', 'A', 'C')}
# Glyphs a reader cannot reliably tell apart in printed alphanumerics.
_CONFUSABLE = str.maketrans({'O': '0', 'Q': '0', 'D': '0', 'I': '1', 'L': '1', 'S': '5', 'B': '8', 'Z': '2', 'G': '6'})


def _check(results, cid, category, status, severity, message, **evidence):
    results.append(dict(id=cid, category=category, status=status, severity=severity if status in ('FAIL', 'WARN') else 'info',
                        message=message, evidence=evidence))


def _canon_docno(s: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper()).translate(_CONFUSABLE)


def _name_letters(s: str) -> str:
    return ' '.join(re.sub(r'[^A-Z ]', ' ', str(s or '').upper()).split())


def _age(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def mrz_fields(mrz: dict | None, today: date) -> dict:
    """Normalise MRZ values to the canonical field vocabulary (ISO dates)."""
    if not mrz or not mrz.get('valid_format'):
        return {}
    dob = mrz_date(mrz.get('date_of_birth', ''), 'birth', today)
    exp = mrz_date(mrz.get('date_of_expiry', ''), 'expiry', today)
    out = dict(document_number=mrz.get('document_number') or mrz.get('passport_number'),
               surname=mrz.get('surname'), given_names=mrz.get('given_names'),
               nationality=(mrz.get('nationality') or '').replace('<', ''),
               issuing_state=(mrz.get('issuing_state') or '').replace('<', ''),
               sex=(mrz.get('gender') or '').replace('<', 'X'),
               date_of_birth=dob.isoformat() if dob else None,
               date_of_expiry=exp.isoformat() if exp else None)
    if mrz.get('format') in ('MRVA', 'MRVB'):
        linked = (mrz.get('optional_data') or '').replace('<', '')
        if linked:
            out['passport_number'] = linked
    return {k: v for k, v in out.items() if v}


def merge_fields(document_type: str, mrz: dict | None, viz: dict | None, today: date) -> tuple[dict, dict]:
    """MRZ values are authoritative for the fields they carry (check digits protect them);
    visual-zone values fill the rest. Returns (fields, source-per-field)."""
    viz = viz or {}
    m = mrz_fields(mrz, today) if (mrz and mrz.get('overall_check_digit_valid')) else {}
    fields, source = {}, {}
    for k, v in viz.items():
        if v not in (None, ''):
            fields[k], source[k] = v, 'viz'
    for k, v in m.items():
        fields[k], source[k] = v, 'mrz'
    fields['document_type'] = document_type
    return fields, source


def validate_document(record: dict, document_type: str | None = None, *, today: date | None = None,
                      policy: dict | None = None, companion: dict | None = None,
                      context: dict | None = None) -> dict:
    """Validate one screened document.

    ``record`` is ``{'document_type', 'fields', 'mrz', 'viz_fields', 'viz_confidences'}``. For
    backwards compatibility a plain fields dict is also accepted. ``companion`` is the record of a
    second document presented together (e.g. the passport a visa is linked to). ``context`` may
    carry ``entry_date``, ``intended_stay_days`` and ``prior_entries`` for visa entry rules."""
    if 'fields' not in record:
        record = dict(document_type=document_type or 'passport', fields=dict(record), mrz=None, viz_fields={})
    policy = policy or load_policy()
    vp = policy['validation']
    today = today or date.today()
    context = context or {}
    entry_date = parse_date(context.get('entry_date')) or today
    dtype = record.get('document_type') or document_type or 'unknown'
    f = record.get('fields') or {}
    mrz = record.get('mrz') or None
    viz = record.get('viz_fields') or {}
    vconf = record.get('viz_confidences') or {}
    results: list[dict] = []

    # ---- format ---------------------------------------------------------------------------
    pattern = vp['document_number_patterns'].get(dtype)
    dn = str(f.get('document_number') or '')
    if pattern and dn:
        ok = bool(re.fullmatch(pattern, dn))
        _check(results, 'document_number_format', 'format', 'PASS' if ok else 'FAIL', 'medium',
               'Document number matches the expected format.' if ok else f'Document number {dn!r} does not match the {dtype} format.',
               value=dn, pattern=pattern)
    elif not dn:
        _check(results, 'document_number_present', 'format', 'WARN', 'medium', 'Document number could not be read.')
    for key in ('nationality', 'issuing_state'):
        code = f.get(key)
        if code:
            ok = is_valid_code(code)
            _check(results, f'{key}_code', 'format', 'PASS' if ok else 'FAIL', 'medium',
                   f'{key.replace("_", " ").capitalize()} code {code} is a valid ICAO code.' if ok
                   else f'{key.replace("_", " ").capitalize()} {code!r} is not a valid ICAO code.', value=code)
    sex = f.get('sex')
    if sex:
        ok = sex in ('M', 'F', 'X')
        _check(results, 'sex_format', 'format', 'PASS' if ok else 'FAIL', 'low',
               'Sex field is valid.' if ok else f'Sex field {sex!r} is not M, F or X.', value=sex)
    for key in ('surname', 'given_names'):
        v = f.get(key)
        if v and not re.fullmatch(r"[A-Z][A-Z '\-]*", str(v)):
            _check(results, f'{key}_characters', 'format', 'WARN', 'low', f'{key} contains unexpected characters.', value=v)

    # ---- dates ----------------------------------------------------------------------------
    dob = parse_date(f.get('date_of_birth'))
    doe = parse_date(f.get('date_of_expiry'))
    doi = parse_date(f.get('date_of_issue'))
    if dob:
        if dob > today:
            _check(results, 'dob_not_future', 'validity', 'FAIL', 'high', 'Date of birth is in the future.', dob=dob.isoformat())
        elif _age(dob, today) > vp['max_age_years']:
            _check(results, 'dob_plausible', 'validity', 'FAIL', 'high', f'Holder age exceeds {vp["max_age_years"]} years.', dob=dob.isoformat())
        else:
            age = _age(dob, today)
            _check(results, 'dob_plausible', 'validity', 'PASS', 'info', f'Holder age {age}.', age=age)
            if age < 18 and dtype != 'driving_licence':
                _check(results, 'minor_traveller', 'validity', 'WARN', 'low',
                       'Holder is a minor: confirm accompanying guardian / consent documentation.', age=age)
    elif f.get('date_of_birth'):
        _check(results, 'dob_parse', 'format', 'FAIL', 'medium', 'Date of birth is not a valid date.', raw=f.get('date_of_birth'))

    if doe:
        if doe < today:
            _check(results, 'not_expired', 'validity', 'FAIL', 'high', f'Document expired on {doe.isoformat()}.',
                   date_of_expiry=doe.isoformat(), days_expired=(today - doe).days)
        else:
            days_left = (doe - today).days
            _check(results, 'not_expired', 'validity', 'PASS', 'info', f'Valid until {doe.isoformat()} ({days_left} days).',
                   days_left=days_left)
            if dtype == 'passport' and (doe - entry_date).days < vp['passport_min_validity_days_on_entry']:
                _check(results, 'passport_remaining_validity', 'validity', 'WARN', 'medium',
                       f'Passport has less than {vp["passport_min_validity_days_on_entry"]} days validity remaining.',
                       days_left=days_left)
    elif f.get('date_of_expiry'):
        _check(results, 'expiry_parse', 'format', 'FAIL', 'medium', 'Expiry date is not a valid date.', raw=f.get('date_of_expiry'))
    else:
        _check(results, 'expiry_present', 'validity', 'WARN', 'medium', 'Expiry date could not be read.')

    if doi:
        if doi > today:
            _check(results, 'issue_not_future', 'validity', 'FAIL', 'high', 'Date of issue is in the future.', date_of_issue=doi.isoformat())
        if doe and doi >= doe:
            _check(results, 'issue_before_expiry', 'validity', 'FAIL', 'high', 'Date of issue is not before the expiry date.')
        if dob and doi < dob:
            _check(results, 'issue_after_birth', 'validity', 'FAIL', 'high', 'Document was issued before the holder was born.')
        max_years = vp['max_validity_years'].get(dtype)
        if doe and max_years and doe > _add_years(doi, max_years) + timedelta(days=45):
            _check(results, 'validity_period', 'validity', 'FAIL', 'high',
                   f'Validity period exceeds the {max_years}-year maximum for a {dtype}.',
                   date_of_issue=doi.isoformat(), date_of_expiry=doe.isoformat())
        elif doe and max_years:
            _check(results, 'validity_period', 'validity', 'PASS', 'info', 'Validity period is within limits.')
        if dtype == 'driving_licence' and dob and _age(dob, doi) < vp['min_driving_age_years']:
            _check(results, 'licence_minimum_age', 'validity', 'FAIL', 'high',
                   f'Licence issued when the holder was {_age(dob, doi)}, below the minimum driving age.')

    # ---- printed dates must lie inside the document's life span -----------------------------
    if mrz and mrz.get('overall_check_digit_valid') and record.get('printed_dates'):
        mf = mrz_fields(mrz, today)
        lo, hi = parse_date(mf.get('date_of_birth')), parse_date(mf.get('date_of_expiry'))
        if lo and hi:
            outside = sorted({d for d in record['printed_dates'] if not lo <= parse_date(d) <= hi})
            if outside:
                _check(results, 'printed_dates_within_mrz_range', 'consistency', 'FAIL', 'high',
                       f'Printed date(s) {", ".join(outside)} fall outside the birth-to-expiry range certified by the MRZ '
                       f'({lo.isoformat()} to {hi.isoformat()}): possible altered date.', dates=outside)

    # ---- MRZ integrity --------------------------------------------------------------------
    if dtype in MRZ_TYPES:
        if not mrz or not mrz.get('valid_format'):
            _check(results, 'mrz_present', 'integrity', 'WARN', 'medium',
                   'Machine readable zone could not be read; integrity checks unavailable (recapture or inspect manually).')
        else:
            checks = mrz.get('check_digits') or {}
            reliable = context.get('mrz_read_reliable', True)
            for k, v in checks.items():
                if k == 'passport_number':
                    continue
                if v:
                    _check(results, f'mrz_check_{k}', 'integrity', 'PASS', 'info', f'MRZ {k.replace("_", " ")} check digit is valid.')
                elif reliable:
                    _check(results, f'mrz_check_{k}', 'integrity', 'FAIL', 'critical' if k == 'composite' else 'high',
                           f'MRZ {k.replace("_", " ")} check digit FAILED on a clear read: the MRZ may have been altered.')
                else:
                    # A blurred or low-confidence read cannot distinguish forgery from misreading.
                    _check(results, f'mrz_check_{k}', 'capture', 'WARN', 'medium',
                           f'MRZ {k.replace("_", " ")} check digit failed on a poor-quality read: recapture the document.')
            code = (mrz.get('document_code') or mrz.get('document_type') or '')[:1]
            if code and code not in MRZ_CODE_OK[dtype]:
                _check(results, 'mrz_document_code', 'integrity', 'FAIL', 'high',
                       f'MRZ document code {code!r} does not match a {dtype}.', code=code)
            if mrz.get('repairs'):
                _check(results, 'mrz_repairs', 'integrity', 'PASS', 'info',
                       'MRZ needed check-digit-confirmed glyph repairs (retained for audit).', repairs=mrz['repairs'])

    # ---- MRZ <-> visual zone consistency ---------------------------------------------------
    if mrz and mrz.get('overall_check_digit_valid') and viz:
        mf = mrz_fields(mrz, today)
        for key in ('document_number', 'date_of_birth', 'date_of_expiry', 'sex', 'nationality', 'surname', 'given_names',
                    'passport_number'):
            if key not in mf or key not in viz or viz.get(key) in (None, ''):
                continue
            conf = vconf.get(key, 80)
            if conf < 55:
                continue  # value was inferred without a readable label; the MRZ is authoritative
            a, b = mf[key], viz[key]
            if key in ('document_number', 'passport_number'):
                same = _canon_docno(a) == _canon_docno(b)
                score = 100 if same else fuzz.ratio(_canon_docno(a), _canon_docno(b))
            elif key in ('surname', 'given_names'):
                na, nb = _name_letters(a), _name_letters(b)
                score = 100 if nb.startswith(na) or na.startswith(nb) else fuzz.ratio(na, nb)
                same = score >= vp['name_match_pass']
            else:
                same = str(a) == str(b)
                score = 100 if same else 0
            if same:
                _check(results, f'viz_mrz_{key}', 'consistency', 'PASS', 'info', f'{key} matches between MRZ and printed zone.')
            elif key in ('surname', 'given_names') and score >= vp['name_match_fail']:
                _check(results, f'viz_mrz_{key}', 'consistency', 'WARN', 'low',
                       f'{key} differs slightly between MRZ and printed zone (possible OCR noise).', mrz=a, printed=b, score=score)
            else:
                _check(results, f'viz_mrz_{key}', 'consistency', 'FAIL', 'high',
                       f'{key} printed on the document ({b}) differs from the MRZ ({a}): possible text manipulation.',
                       mrz=a, printed=b, ocr_confidence=conf)

    # ---- visa entry rules ------------------------------------------------------------------
    if dtype == 'visa':
        vf = parse_date(f.get('valid_from'))
        vu = doe
        if vf and entry_date < vf:
            _check(results, 'visa_entry_window', 'validity', 'FAIL', 'high', f'Visa is not valid for entry until {vf.isoformat()}.',
                   valid_from=vf.isoformat(), entry_date=entry_date.isoformat())
        elif vu and entry_date > vu:
            _check(results, 'visa_entry_window', 'validity', 'FAIL', 'high', f'Visa validity ended on {vu.isoformat()}.')
        elif vu:
            _check(results, 'visa_entry_window', 'validity', 'PASS', 'info', 'Entry date is inside the visa validity window.',
                   valid_from=vf.isoformat() if vf else None, valid_until=vu.isoformat())
        entries = str(f.get('number_of_entries') or '').upper()
        prior = context.get('prior_entries')
        allowed = {'SINGLE': 1, 'DOUBLE': 2}.get(entries, None if entries in ('MULTIPLE', '') else
                                                 (int(entries) if entries.isdigit() else None))
        if prior is not None and allowed is not None:
            ok = prior < allowed
            _check(results, 'visa_entries_remaining', 'validity', 'PASS' if ok else 'FAIL', 'high',
                   f'{allowed - prior} of {allowed} entries remaining.' if ok else
                   f'All {allowed} permitted entries on this visa have been used.', prior_entries=prior, allowed=allowed)
        elif entries:
            _check(results, 'visa_entries', 'validity', 'PASS', 'info', f'Visa allows {entries.lower()} entry.', entries=entries)
        stay = f.get('duration_of_stay_days')
        try:
            stay = int(stay) if stay not in (None, '') else None
        except (TypeError, ValueError):
            stay = None
        if stay:
            permitted_until = entry_date + timedelta(days=stay)
            if vu and permitted_until > vu:
                permitted_until = vu
            intended = context.get('intended_stay_days')
            if intended is not None:
                ok = entry_date + timedelta(days=int(intended)) <= permitted_until
                _check(results, 'visa_stay_duration', 'validity', 'PASS' if ok else 'FAIL', 'medium',
                       f'Intended stay of {intended} days fits the permitted stay (until {permitted_until.isoformat()}).' if ok else
                       f'Intended stay of {intended} days exceeds the permitted stay (until {permitted_until.isoformat()}).',
                       permitted_stay_days=stay, permitted_until=permitted_until.isoformat())
            else:
                _check(results, 'visa_stay_duration', 'validity', 'PASS', 'info',
                       f'Permitted stay {stay} days, i.e. until {permitted_until.isoformat()}.',
                       permitted_stay_days=stay, permitted_until=permitted_until.isoformat())
        if companion:
            cf = companion.get('fields') or {}
            linked = f.get('passport_number')
            pno = cf.get('document_number')
            if linked and pno:
                ok = _canon_docno(linked) == _canon_docno(pno)
                _check(results, 'visa_passport_link', 'consistency', 'PASS' if ok else 'FAIL', 'critical',
                       'Visa is issued to the presented passport.' if ok else
                       f'Visa is linked to passport {linked}, but passport {pno} was presented.', visa_passport=linked, passport=pno)
            for key in ('surname', 'date_of_birth', 'nationality'):
                a, b = f.get(key), cf.get(key)
                if a and b:
                    ok = (fuzz.ratio(_name_letters(a), _name_letters(b)) >= vp['name_match_pass']) if key == 'surname' else a == b
                    _check(results, f'visa_passport_{key}', 'consistency', 'PASS' if ok else 'FAIL', 'high',
                           f'Visa and passport {key} agree.' if ok else f'Visa {key} ({a}) differs from passport ({b}).')
            cdoe = parse_date(cf.get('date_of_expiry'))
            if cdoe and vu and cdoe < min(vu, entry_date + timedelta(days=stay or 0)):
                _check(results, 'passport_covers_stay', 'validity', 'WARN', 'medium',
                       'Passport expires before the end of the permitted stay.', passport_expiry=cdoe.isoformat())

    fails = [r for r in results if r['status'] == 'FAIL']
    warns = [r for r in results if r['status'] == 'WARN']
    return dict(valid=not fails, checks=results,
                summary=dict(passed=sum(r['status'] == 'PASS' for r in results), failed=len(fails), warnings=len(warns)),
                entry_date=entry_date.isoformat(), policy_version=policy.get('policy_version'))
