from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_tesseract(explicit: str | None = None) -> str | None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get('TESSERACT_CMD')
    if env:
        candidates.append(env)
    which = shutil.which('tesseract')
    if which:
        candidates.append(which)
    if os.name == 'nt':
        candidates.extend([
            r'C:\\Program Files\\Tesseract-OCR\\tesseract.exe',
            r'C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe',
        ])
        local = Path(os.environ.get('LOCALAPPDATA', ''))
        if local:
            candidates.extend([
                str(local / 'Tesseract-OCR' / 'tesseract.exe'),
                str(local / 'Programs' / 'Tesseract-OCR' / 'tesseract.exe'),
            ])
    else:
        candidates.extend([
            '/usr/bin/tesseract', '/usr/local/bin/tesseract',
            '/opt/homebrew/bin/tesseract',
        ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return None
