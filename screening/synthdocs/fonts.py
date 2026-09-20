from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_DIRS = [Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts', Path('/usr/share/fonts/truetype/dejavu'),
         Path('/usr/share/fonts/truetype/liberation'), Path('/usr/share/fonts/truetype/freefont')]

# Visual-zone (printed field) fonts.
VIZ_FONTS = ['arial.ttf', 'arialbd.ttf', 'ARIALN.TTF', 'calibri.ttf', 'calibrib.ttf', 'segoeui.ttf', 'segoeuib.ttf',
             'verdana.ttf', 'tahoma.ttf', 'times.ttf', 'timesbd.ttf', 'georgia.ttf', 'trebuc.ttf', 'bahnschrift.ttf',
             'corbel.ttf', 'Candara.ttf', 'GOTHIC.TTF', 'FRADM.TTF', 'micross.ttf',
             'DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'LiberationSans-Regular.ttf', 'LiberationSerif-Regular.ttf']
# Genuinely monospaced MRZ-style fonts used for training.
MRZ_TRAIN_MONO = ['OCRAEXT.TTF', 'cour.ttf', 'courbd.ttf', 'lucon.ttf', 'LTYPE.TTF', 'LTYPEB.TTF',
                  'DejaVuSansMono.ttf', 'LiberationMono-Regular.ttf']
# Proportional sans fonts rendered on a fixed pitch; adds glyph-shape diversity so the
# reader generalises towards OCR-B, which is not available locally. Fonts with old-style
# (lowercase-height) numerals such as Candara/Constantia are excluded: MRZs never use them.
MRZ_TRAIN_PITCHED = ['arial.ttf', 'arialbd.ttf', 'verdana.ttf', 'tahoma.ttf', 'segoeui.ttf', 'bahnschrift.ttf',
                     'calibri.ttf', 'trebuc.ttf', 'DejaVuSans.ttf', 'calibrib.ttf', 'segoeuib.ttf', 'verdanab.ttf',
                     'tahomabd.ttf', 'trebucbd.ttf', 'GOTHIC.TTF', 'FRADM.TTF',
                     'ARIALN.TTF', 'LSANS.TTF', 'micross.ttf', 'framd.ttf', 'GIL_____.TTF', 'TCM_____.TTF',
                     'ERASMD.TTF', 'BRLNSR.TTF', 'LEELAWAD.TTF', 'ebrima.ttf', 'gadugi.ttf', 'malgun.ttf',
                     'seguisb.ttf', 'sylfaen.ttf']
# Held out from MRZ training so evaluation measures unseen-font generalisation.
# The legacy sample set (samples/generated) renders its MRZ with Consolas.
MRZ_HOLDOUT = ['consola.ttf', 'consolab.ttf']


@lru_cache(maxsize=None)
def font_path(name: str) -> str | None:
    for d in _DIRS:
        p = d / name
        if p.exists():
            return str(p)
    return None


def available(names: list[str]) -> list[str]:
    return [n for n in names if font_path(n)]


@lru_cache(maxsize=512)
def load(name: str, size: int) -> ImageFont.FreeTypeFont:
    p = font_path(name)
    if p is None:
        fallback = available(VIZ_FONTS)
        if not fallback:
            return ImageFont.load_default()
        p = font_path(fallback[0])
    return ImageFont.truetype(p, size)
