"""Tamper-evident audit trail: append-only JSON lines chained with SHA-256.

Each record stores the hash of the previous record, so editing, deleting or reordering any
past decision breaks verification from that point on. Images are referenced by SHA-256
digest, not stored."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR

GENESIS = '0' * 64


def _digest(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != 'hash'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DATA_DIR / 'audit_log.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, GENESIS
        with self.path.open('rb') as fh:
            fh.seek(0, 2)
            end = fh.tell()
            size = min(end, 1 << 20)
            fh.seek(end - size)
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        last = json.loads(lines[-1])
        return int(last['seq']), last['hash']

    def append(self, event: dict) -> dict:
        with self._lock:
            seq, prev = self._tail()
            record = dict(seq=seq + 1, logged_at=datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                          prev_hash=prev, **event)
            record['hash'] = _digest(record)
            with self.path.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(record, sort_keys=True, default=str) + '\n')
            return record

    def verify(self) -> dict:
        if not self.path.exists():
            return dict(valid=True, records=0)
        prev, n = GENESIS, 0
        with self.path.open(encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():
                    continue
                n += 1
                rec = json.loads(line)
                if rec.get('prev_hash') != prev or _digest(rec) != rec.get('hash') or rec.get('seq') != n:
                    return dict(valid=False, records=n, broken_at_seq=rec.get('seq'),
                                reason='hash chain mismatch: a record was modified, removed or reordered')
                prev = rec['hash']
        return dict(valid=True, records=n, head=prev)

    def read(self, limit: int = 50, screening_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding='utf-8') as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if screening_id is None or rec.get('screening_id') == screening_id:
                        out.append(rec)
        return out[-limit:]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
