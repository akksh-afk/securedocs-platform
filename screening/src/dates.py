"""Date parsing shared by OCR field extraction, validation and the synthetic generator."""
from __future__ import annotations

import re
from datetime import date, datetime

_MONTHS = {m: i for i, m in enumerate(['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'], 1)}
_MONTH_OCR = str.maketrans({'0': 'O', '1': 'I', '5': 'S', '8': 'B', '6': 'G', '4': 'A'})
_DIGIT_OCR = str.maketrans({'O': '0', 'o': '0', 'D': '0', 'Q': '0', 'I': '1', 'l': '1', 'L': '1', '|': '1', 'S': '5',
                            'B': '8', 'Z': '2', 'G': '6'})


def _mk(y: int, m: int, d: int) -> date | None:
    if not 1900 <= y <= 2100:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text: str | date | None) -> date | None:
    """Parse printed dates such as '14 FEB 2002', '14/02/2002', '2002-02-14', '14.02.2002'."""
    if text is None:
        return None
    if isinstance(text, date):
        return text
    s = re.sub(r'\s+', ' ', str(text).upper().strip())
    if not s:
        return None
    m = re.search(r'(\d{1,2}|[0-9OIDQSLBZG]{2})\s*[ \-/.]?\s*([A-Z0-9]{3})[A-Z]*\s*[ \-/.]?\s*(\d{4}|[0-9OIDQSLBZG]{4})', s)
    if m:
        mon = _MONTHS.get(m.group(2).translate(_MONTH_OCR))
        if mon:
            dd = m.group(1).translate(_DIGIT_OCR); yy = m.group(3).translate(_DIGIT_OCR)
            if dd.isdigit() and yy.isdigit():
                r = _mk(int(yy), mon, int(dd))
                if r:
                    return r
    t = s.translate(_DIGIT_OCR)
    m = re.search(r'(?<!\d)(\d{4})[\-/. ](\d{1,2})[\-/. ](\d{1,2})(?!\d)', t)
    if m:
        r = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if r:
            return r
    m = re.search(r'(?<!\d)(\d{1,2})[\-/. ](\d{1,2})[\-/. ](\d{4})(?!\d)', t)
    if m:
        r = _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if r:
            return r
    digits = re.sub(r'\D', '', t)
    if len(digits) == 8:
        for fmt in ('%Y%m%d', '%d%m%Y'):
            try:
                r = datetime.strptime(digits, fmt).date()
                if 1900 <= r.year <= 2100:
                    return r
            except ValueError:
                pass
    return None


def mrz_date(yymmdd: str, kind: str, today: date | None = None) -> date | None:
    """Expand a YYMMDD MRZ date. Birth dates cannot be in the future; expiry dates are
    assumed to lie within 50 years before or after ``today``."""
    if not yymmdd or not re.fullmatch(r'\d{6}', yymmdd):
        return None
    today = today or date.today()
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    if kind == 'birth':
        year = 2000 + yy if 2000 + yy <= today.year else 1900 + yy
        r = _mk(year, mm, dd)
        if r and r > today:
            r = _mk(year - 100, mm, dd)
        return r
    century = (today.year // 100) * 100
    candidates = [c for c in (_mk(century - 100 + yy, mm, dd), _mk(century + yy, mm, dd), _mk(century + 100 + yy, mm, dd)) if c]
    return min(candidates, key=lambda c: abs((c - today).days)) if candidates else None
