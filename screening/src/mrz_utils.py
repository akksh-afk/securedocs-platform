"""ICAO Doc 9303 machine readable zone (MRZ) generation, parsing and integrity checks.

Supported formats:
    TD3   passports                    2 x 44
    TD2   official travel documents    2 x 36
    TD1   ID cards / residence permits 3 x 30
    MRVA  full-size visa stickers      2 x 44
    MRVB  small visa stickers          2 x 36

Integrity policy: check digits are *never* recomputed to make a read pass. The only
repairs allowed are OCR glyph confusions (O/0, I/1, ...) that a check digit confirms,
and every repair is listed in ``MRZResult.repairs`` for the audit trail. A forged MRZ
whose field was edited without updating the composite digit therefore stays invalid.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from itertools import product
import re

MRZ_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<'
FORMAT_SHAPES = {'TD3': (2, 44), 'MRVA': (2, 44), 'TD2': (2, 36), 'MRVB': (2, 36), 'TD1': (3, 30)}

# Glyphs OCR engines confuse, split by the character class a field position requires.
_TO_DIGIT = {'O': '0', 'Q': '0', 'D': '0', 'U': '0', 'I': '1', 'L': '1', 'T': '1', 'Z': '2',
             'S': '5', 'B': '8', 'G': '6', 'A': '4'}
_TO_ALPHA = {'0': 'O', '1': 'I', '2': 'Z', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B'}
# Plausible confusions inside alphanumeric fields (document numbers).
_ALNUM_CONFUSIONS = {'0': 'OD', 'O': '0D', 'D': '0O', '1': 'I', 'I': '1', '2': 'Z', 'Z': '2',
                     '5': 'S', 'S': '5', '8': 'B', 'B': '8', '6': 'G', 'G': '6'}


def _char_value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if 'A' <= c <= 'Z':
        return ord(c) - ord('A') + 10
    return 0


def check_digit(data: str) -> str:
    weights = (7, 3, 1)
    return str(sum(_char_value(ch) * weights[i % 3] for i, ch in enumerate(data)) % 10)


def _pad(value: str, size: int, fill: str = '<') -> str:
    value = re.sub(r'[^A-Z0-9< ]', '', (value or '').upper()).replace(' ', '<')
    return value[:size].ljust(size, fill)


def _name_field(surname: str, given_names: str, size: int) -> str:
    sur = re.sub(r'[^A-Z ]', '', (surname or '').upper()).strip().replace(' ', '<')
    giv = re.sub(r'[^A-Z ]', '', (given_names or '').upper()).strip().replace(' ', '<')
    return (f'{sur}<<{giv}' if giv else sur)[:size].ljust(size, '<')


def normalize_mrz_line(line: str) -> str:
    line = (line or '').upper().replace(' ', '')
    line = line.translate(str.maketrans({'«': '<', '‹': '<', '◂': '<', '·': '<', '(': '<', '{': '<', '[': '<'}))
    return re.sub(r'[^A-Z0-9<]', '', line)




# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_mrz(fmt: str, *, document_code: str, issuing_state: str, document_number: str,
                 nationality: str, date_of_birth: str, sex: str, date_of_expiry: str,
                 surname: str, given_names: str, optional_data: str = '',
                 optional_data_2: str = '') -> list[str]:
    """Build a check-digit-correct MRZ. Dates are YYMMDD strings."""
    fmt = fmt.upper()
    code = _pad(document_code, 2)
    state = _pad(issuing_state, 3)
    docno = _pad(document_number, 9)
    nat = _pad(nationality, 3)
    dob = _pad(date_of_birth, 6, '<')
    exp = _pad(date_of_expiry, 6, '<')
    sex = (sex or '<').upper()[0]
    dcd, bcd, ecd = check_digit(docno), check_digit(dob), check_digit(exp)
    if fmt == 'TD3':
        opt = _pad(optional_data, 14)
        ocd = check_digit(opt)
        comp = check_digit(docno + dcd + dob + bcd + exp + ecd + opt + ocd)
        return [code + state + _name_field(surname, given_names, 39),
                docno + dcd + nat + dob + bcd + sex + exp + ecd + opt + ocd + comp]
    if fmt == 'TD2':
        opt = _pad(optional_data, 7)
        comp = check_digit(docno + dcd + dob + bcd + exp + ecd + opt)
        return [code + state + _name_field(surname, given_names, 31),
                docno + dcd + nat + dob + bcd + sex + exp + ecd + opt + comp]
    if fmt in ('MRVA', 'MRVB'):
        width = 44 if fmt == 'MRVA' else 36
        opt = _pad(optional_data, width - 28)
        return [code + state + _name_field(surname, given_names, width - 5),
                docno + dcd + nat + dob + bcd + sex + exp + ecd + opt]
    if fmt == 'TD1':
        opt1 = _pad(optional_data, 15)
        opt2 = _pad(optional_data_2, 11)
        l1 = code + state + docno + dcd + opt1
        l2_wo = dob + bcd + sex + exp + ecd + nat + opt2
        comp = check_digit(l1[5:30] + l2_wo[0:7] + l2_wo[8:15] + l2_wo[18:29])
        return [l1, l2_wo + comp, _name_field(surname, given_names, 30)]
    raise ValueError(f'Unsupported MRZ format: {fmt}')


def generate_td3_mrz(*, passport_number: str, nationality: str, date_of_birth: str,
                     sex: str, date_of_expiry: str, surname: str, given_names: str,
                     issuing_state: str = 'UTO', optional_data: str = '') -> list[str]:
    return generate_mrz('TD3', document_code='P', issuing_state=issuing_state,
                        document_number=passport_number, nationality=nationality,
                        date_of_birth=date_of_birth, sex=sex, date_of_expiry=date_of_expiry,
                        surname=surname, given_names=given_names, optional_data=optional_data)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class MRZResult:
    valid_format: bool
    document_type: str = ''
    issuing_state: str = ''
    passport_number: str = ''
    nationality: str = ''
    date_of_birth: str = ''
    gender: str = ''
    date_of_expiry: str = ''
    optional_data: str = ''
    surname: str = ''
    given_names: str = ''
    check_digits: dict[str, bool] | None = None
    overall_check_digit_valid: bool = False
    normalized_lines: list[str] | None = None
    raw_overall_check_digit_valid: bool = False
    repair_applied: bool = False
    format: str = ''
    document_code: str = ''
    document_number: str = ''
    optional_data_2: str = ''
    composite_check_valid: bool | None = None
    repairs: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# (name, slice-in-line, check-digit index, character class) per format.
# Line index is encoded as (line, start, end). Classes: 'A' alpha, 'N' numeric, 'X' alphanumeric.
_LAYOUTS = {
    'TD3': dict(width=44, lines=2, fields=[
        ('document_number', (1, 0, 9), (1, 9), 'X'), ('nationality', (1, 10, 13), None, 'A'),
        ('date_of_birth', (1, 13, 19), (1, 19), 'N'), ('sex', (1, 20, 21), None, 'S'),
        ('date_of_expiry', (1, 21, 27), (1, 27), 'N'), ('optional_data', (1, 28, 42), (1, 42), 'X')],
        name=(0, 5, 44), state=(0, 2, 5), composite=((1, 43), [(1, 0, 10), (1, 13, 20), (1, 21, 43)])),
    'TD2': dict(width=36, lines=2, fields=[
        ('document_number', (1, 0, 9), (1, 9), 'X'), ('nationality', (1, 10, 13), None, 'A'),
        ('date_of_birth', (1, 13, 19), (1, 19), 'N'), ('sex', (1, 20, 21), None, 'S'),
        ('date_of_expiry', (1, 21, 27), (1, 27), 'N'), ('optional_data', (1, 28, 35), None, 'X')],
        name=(0, 5, 36), state=(0, 2, 5), composite=((1, 35), [(1, 0, 10), (1, 13, 20), (1, 21, 35)])),
    'MRVA': dict(width=44, lines=2, fields=[
        ('document_number', (1, 0, 9), (1, 9), 'X'), ('nationality', (1, 10, 13), None, 'A'),
        ('date_of_birth', (1, 13, 19), (1, 19), 'N'), ('sex', (1, 20, 21), None, 'S'),
        ('date_of_expiry', (1, 21, 27), (1, 27), 'N'), ('optional_data', (1, 28, 44), None, 'X')],
        name=(0, 5, 44), state=(0, 2, 5), composite=None),
    'MRVB': dict(width=36, lines=2, fields=[
        ('document_number', (1, 0, 9), (1, 9), 'X'), ('nationality', (1, 10, 13), None, 'A'),
        ('date_of_birth', (1, 13, 19), (1, 19), 'N'), ('sex', (1, 20, 21), None, 'S'),
        ('date_of_expiry', (1, 21, 27), (1, 27), 'N'), ('optional_data', (1, 28, 36), None, 'X')],
        name=(0, 5, 36), state=(0, 2, 5), composite=None),
    'TD1': dict(width=30, lines=3, fields=[
        ('document_number', (0, 5, 14), (0, 14), 'X'), ('optional_data', (0, 15, 30), None, 'X'),
        ('date_of_birth', (1, 0, 6), (1, 6), 'N'), ('sex', (1, 7, 8), None, 'S'),
        ('date_of_expiry', (1, 8, 14), (1, 14), 'N'), ('nationality', (1, 15, 18), None, 'A'),
        ('optional_data_2', (1, 18, 29), None, 'X')],
        name=(2, 0, 30), state=(0, 2, 5), composite=((1, 29), [(0, 5, 30), (1, 0, 7), (1, 8, 15), (1, 18, 29)])),
}


def detect_format(lines: list[str]) -> str | None:
    lines = [normalize_mrz_line(x) for x in lines if normalize_mrz_line(x)]
    if len(lines) >= 3 and all(26 <= len(x) <= 34 for x in lines[-3:]):
        return 'TD1'
    if len(lines) >= 2:
        l1, l2 = lines[-2], lines[-1]
        avg = (len(l1) + len(l2)) / 2
        visa = l1[:1] == 'V'
        if 40 <= avg <= 48:
            return 'MRVA' if visa else 'TD3'
        if 32 <= avg < 40:
            return 'MRVB' if visa else 'TD2'
    return None


def _get(lines, span):
    li, a, b = span
    return lines[li][a:b]


def _set(lines, li, idx, ch):
    s = lines[li]
    lines[li] = s[:idx] + ch + s[idx + 1:]


def _class_repair(lines, fmt, repairs):
    """Deterministic glyph-class fixes: digits in alpha slots and letters in digit slots."""
    lay = _LAYOUTS[fmt]

    def fix(span, cls, label):
        li, a, b = span
        for i in range(a, b):
            c = lines[li][i]
            if cls == 'N' and c in _TO_DIGIT:
                _set(lines, li, i, _TO_DIGIT[c]); repairs.append(f'{label}[{i - a}] {c}->{_TO_DIGIT[c]}')
            elif cls in ('A', 'NAME') and c in _TO_ALPHA:
                _set(lines, li, i, _TO_ALPHA[c]); repairs.append(f'{label}[{i - a}] {c}->{_TO_ALPHA[c]}')

    for name, span, cd, cls in lay['fields']:
        if cls in ('N', 'A'):
            fix(span, cls, name)
        if cd is not None:
            fix((cd[0], cd[1], cd[1] + 1), 'N', name + '_check')
    fix(lay['state'], 'A', 'issuing_state')
    fix(lay['name'], 'NAME', 'name')
    if lay['composite']:
        ci = lay['composite'][0]
        fix((ci[0], ci[1], ci[1] + 1), 'N', 'composite_check')


def _composite_ok(lines, fmt):
    comp = _LAYOUTS[fmt]['composite']
    if not comp:
        return None
    (li, idx), spans = comp
    data = ''.join(_get(lines, s) for s in spans)
    return lines[li][idx] == check_digit(data)


def _field_checks(lines, fmt):
    checks = {}
    for name, span, cd, cls in _LAYOUTS[fmt]['fields']:
        if cd is None:
            continue
        value = _get(lines, span)
        digit = lines[cd[0]][cd[1]]
        if name == 'optional_data' and set(value) == {'<'}:
            checks[name] = digit in ('<', '0')
        else:
            checks[name] = digit == check_digit(value)
    return checks


def _confusion_repair(lines, fmt, repairs, alternatives=None):
    """Try single glyph substitutions inside a failing field that its check digit confirms.

    Without model alternatives only letter/digit look-alikes are tried. With per-position
    alternatives from the neural reader, digit/digit swaps are allowed for low-confidence
    glyphs. A repair is accepted only when exactly one candidate satisfies the field digit
    and the composite digit (where the format has one) also agrees afterwards.
    """
    lay = _LAYOUTS[fmt]
    for name, span, cd, cls in lay['fields']:
        if cd is None or _field_checks(lines, fmt).get(name, True):
            continue
        li, a, b = span
        target = lines[cd[0]][cd[1]]
        if not target.isdigit():
            continue
        options = []
        for i in range(a, b):
            c = lines[li][i]
            alts = set()
            if cls == 'X':
                alts.update(_ALNUM_CONFUSIONS.get(c, ''))
            if alternatives is not None:
                for ch, prob in alternatives[li][i]:
                    # Without a composite digit to double-check, only take well-supported alternatives.
                    floor = 0.05 if lay['composite'] else 0.2
                    if ch != c and prob >= floor and (cls != 'N' or ch.isdigit()):
                        alts.add(ch)
            options.append([c] + sorted(alts))
        # Single substitutions first; two substitutions only with model alternatives.
        found = []
        for i, opts in enumerate(options):
            for ch in opts[1:]:
                cand = _get(lines, span)
                cand = cand[:i] + ch + cand[i + 1:]
                if check_digit(cand) == target:
                    found.append(((i, ch),))
        # Two simultaneous substitutions satisfy a mod-10 digit too easily; allow them only when a
        # composite check digit independently confirms the result.
        if not found and alternatives is not None and lay['composite']:
            positions = [i for i, o in enumerate(options) if len(o) > 1]
            for i, j in ((p, q) for p in positions for q in positions if p < q):
                for ci, cj in product(options[i][1:], options[j][1:]):
                    cand = list(_get(lines, span)); cand[i] = ci; cand[j] = cj
                    if check_digit(''.join(cand)) == target:
                        found.append(((i, ci), (j, cj)))
        trials = []
        for subs in found:
            trial = list(lines)
            for i, ch in subs:
                _set(trial, li, a + i, ch)
            # The composite digit is an independent second constraint that breaks
            # mod-10 collisions between look-alike candidates.
            if _composite_ok(trial, fmt) is not False:
                trials.append((subs, trial))
        if len(trials) != 1:
            continue
        subs, trial = trials[0]
        for i, ch in subs:
            repairs.append(f'{name}[{i}] {lines[li][a + i]}->{ch} (check digit confirmed)')
        lines[:] = trial


def _parse_name(raw: str) -> tuple[str, str]:
    raw = raw.rstrip('<')
    parts = raw.split('<<', 1)
    surname = parts[0].replace('<', ' ').strip()
    given = parts[1].replace('<', ' ').strip() if len(parts) > 1 else ''
    return surname, re.sub(r'\s+', ' ', given)


def parse_mrz(lines: list[str] | str, alternatives: list[list[list[tuple[str, float]]]] | None = None,
              fmt: str | None = None) -> MRZResult:
    """Parse any supported MRZ. ``alternatives`` (optional) holds per-line, per-position
    ``(char, probability)`` candidates from a recogniser and enables confidence-aware repair."""
    if isinstance(lines, str):
        lines = [x for x in lines.splitlines() if x.strip()]
    cleaned = [normalize_mrz_line(x) for x in lines if normalize_mrz_line(x)]
    fmt = fmt or detect_format(cleaned)
    if fmt is None:
        return MRZResult(valid_format=False, normalized_lines=cleaned)
    lay = _LAYOUTS[fmt]
    width, nlines = lay['width'], lay['lines']
    work = [x[:width].ljust(width, '<') for x in cleaned[-nlines:]]
    if len(work) != nlines:
        return MRZResult(valid_format=False, normalized_lines=cleaned, format=fmt)
    first = work[0]
    if fmt in ('TD3',) and first[0] != 'P':
        return MRZResult(valid_format=False, normalized_lines=work, format=fmt)
    if fmt in ('MRVA', 'MRVB') and first[0] != 'V':
        return MRZResult(valid_format=False, normalized_lines=work, format=fmt)
    if fmt == 'TD1' and first[0] not in 'ACI':
        return MRZResult(valid_format=False, normalized_lines=work, format=fmt)
    if fmt == 'TD2' and first[0] not in 'ACIPV':
        return MRZResult(valid_format=False, normalized_lines=work, format=fmt)

    raw_checks = _field_checks(work, fmt)
    raw_comp = _composite_ok(work, fmt)
    raw_all = all(raw_checks.values()) and raw_comp is not False

    repairs: list[str] = []
    if not raw_all:
        _class_repair(work, fmt, repairs)
        _confusion_repair(work, fmt, repairs, alternatives)
    checks = _field_checks(work, fmt)
    comp = _composite_ok(work, fmt)
    overall = all(checks.values()) and comp is not False

    values = {name: _get(work, span) for name, span, _, _ in lay['fields']}
    surname, given = _parse_name(_get(work, lay['name']))
    docno = values['document_number'].replace('<', '')
    out_checks = dict(checks)
    if 'document_number' in out_checks:
        out_checks['passport_number'] = out_checks['document_number']
    if comp is not None:
        out_checks['composite'] = comp
    return MRZResult(
        valid_format=True, document_type=work[0][0], issuing_state=_get(work, lay['state']),
        passport_number=docno, nationality=values['nationality'], date_of_birth=values['date_of_birth'],
        gender=values['sex'], date_of_expiry=values['date_of_expiry'], optional_data=values.get('optional_data', ''),
        surname=surname, given_names=given, check_digits=out_checks, overall_check_digit_valid=overall,
        normalized_lines=work, raw_overall_check_digit_valid=raw_all, repair_applied=bool(repairs),
        format=fmt, document_code=work[0][:2], document_number=docno,
        optional_data_2=values.get('optional_data_2', ''), composite_check_valid=comp, repairs=repairs)
