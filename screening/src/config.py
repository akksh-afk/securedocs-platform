from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get('SCREENING_DATA_DIR', ROOT / 'data'))
MODEL_DIR = ROOT / 'models'


@lru_cache(maxsize=4)
def load_policy(path: str | None = None) -> dict:
    p = Path(path or os.environ.get('SCREENING_POLICY', ROOT / 'config' / 'policy.json'))
    return json.loads(p.read_text(encoding='utf-8'))
