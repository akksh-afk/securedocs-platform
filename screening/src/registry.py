"""Watchlists, identity registry and travel history (SQLite).

* Watchlist: lost/stolen, revoked or blacklisted documents and persons of interest.
* Identity registry: every screened identity with its face embedding, used to detect the same
  person presenting different identities, or one document number shared by different people.
* Travel events: entries per visa, used for single/double-entry visa rules.

This is a local stand-in for authorised government databases; production deployments replace
it with secure integrations (e.g. INTERPOL SLTD, national watchlists)."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

from .config import DATA_DIR

LIST_TYPES = ('LOST_STOLEN', 'REVOKED', 'BLACKLIST', 'WANTED')
_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id TEXT PRIMARY KEY, list_type TEXT NOT NULL, document_number TEXT, document_type TEXT, issuing_state TEXT,
    surname TEXT, given_names TEXT, date_of_birth TEXT, nationality TEXT, reason TEXT, added_by TEXT,
    added_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_watch_doc ON watchlist(document_number);
CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY, screening_id TEXT, seen_at TEXT NOT NULL, document_type TEXT, document_number TEXT,
    issuing_state TEXT, surname TEXT, given_names TEXT, date_of_birth TEXT, nationality TEXT, sex TEXT,
    face_embedding BLOB, checkpoint_id TEXT);
CREATE INDEX IF NOT EXISTS ix_ident_doc ON identities(document_number);
CREATE INDEX IF NOT EXISTS ix_ident_dob ON identities(date_of_birth);
CREATE TABLE IF NOT EXISTS travel_events (
    id TEXT PRIMARY KEY, screening_id TEXT, document_number TEXT, visa_number TEXT, direction TEXT,
    checkpoint_id TEXT, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_travel_visa ON travel_events(visa_number);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _name(s) -> str:
    return ' '.join(str(s or '').upper().replace('<', ' ').split())


class Registry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DATA_DIR / 'registry.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- watchlist ------------------------------------------------------------------------
    def add_watch(self, list_type: str, reason: str, added_by: str = 'system', **fields) -> str:
        if list_type not in LIST_TYPES:
            raise ValueError(f'list_type must be one of {LIST_TYPES}')
        wid = uuid.uuid4().hex
        cols = ['document_number', 'document_type', 'issuing_state', 'surname', 'given_names', 'date_of_birth', 'nationality']
        vals = [str(fields.get(k)).upper() if fields.get(k) else None for k in cols]
        with self._lock, self._conn() as c:
            c.execute(f'INSERT INTO watchlist (id, list_type, {", ".join(cols)}, reason, added_by, added_at) '
                      f'VALUES (?, ?, {", ".join("?" * len(cols))}, ?, ?, ?)', [wid, list_type, *vals, reason, added_by, _now()])
        return wid

    def list_watch(self, active_only: bool = True) -> list[dict]:
        with self._conn() as c:
            q = 'SELECT * FROM watchlist' + (' WHERE active = 1' if active_only else '') + ' ORDER BY added_at DESC'
            return [dict(r) for r in c.execute(q)]

    def deactivate_watch(self, wid: str) -> bool:
        with self._lock, self._conn() as c:
            return c.execute('UPDATE watchlist SET active = 0 WHERE id = ?', (wid,)).rowcount > 0

    def check_watchlist(self, fields: dict) -> list[dict]:
        hits = []
        dn = str(fields.get('document_number') or '').upper()
        linked = str(fields.get('passport_number') or '').upper()
        with self._conn() as c:
            for r in c.execute('SELECT * FROM watchlist WHERE active = 1'):
                r = dict(r)
                if r['document_number'] and r['document_number'] in (dn, linked):
                    if not r['issuing_state'] or not fields.get('issuing_state') or r['issuing_state'] == fields.get('issuing_state'):
                        hits.append(dict(match='document_number', list_type=r['list_type'], reason=r['reason'], entry_id=r['id'],
                                         document_number=r['document_number']))
                        continue
                if r['surname'] and r['date_of_birth'] and fields.get('surname') and fields.get('date_of_birth'):
                    if r['date_of_birth'] == fields.get('date_of_birth'):
                        full_a = _name(f"{r['given_names'] or ''} {r['surname']}")
                        full_b = _name(f"{fields.get('given_names') or ''} {fields.get('surname')}")
                        # Exact birth date plus matching surname; given names may omit a middle name.
                        score = fuzz.token_set_ratio(full_a, full_b)
                        if fuzz.ratio(_name(r['surname']), _name(fields.get('surname'))) >= 90 and score >= 90:
                            hits.append(dict(match='name_and_dob', score=score, list_type=r['list_type'], reason=r['reason'],
                                             entry_id=r['id'], name=full_a))
        return hits

    # ---- identity registry ----------------------------------------------------------------
    def find_conflicts(self, fields: dict, embedding: np.ndarray | None, face_threshold: float = 0.6,
                       exclude_screening: str | None = None) -> list[dict]:
        """Detect identity fraud patterns against previously screened identities."""
        out = []
        dn = fields.get('document_number')
        full = _name(f"{fields.get('given_names') or ''} {fields.get('surname') or ''}")
        dob = fields.get('date_of_birth')
        with self._conn() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM identities')]
        seen_face = set()
        for r in rows:
            if exclude_screening and r['screening_id'] == exclude_screening:
                continue
            other = _name(f"{r['given_names'] or ''} {r['surname'] or ''}")
            same_person_bio = bool(dob and r['date_of_birth'] == dob and full and fuzz.token_sort_ratio(full, other) >= 90)
            if dn and r['document_number'] == dn and r['document_type'] == fields.get('document_type'):
                if not same_person_bio and (r['date_of_birth'] != dob or fuzz.token_sort_ratio(full, other) < 80):
                    out.append(dict(pattern='document_shared_by_different_identities', severity='critical',
                                    message=f'Document {dn} was previously presented by {other} (DOB {r["date_of_birth"]}).',
                                    previous_seen_at=r['seen_at'], previous_screening=r['screening_id']))
            elif same_person_bio and dn and r['document_number'] and r['document_number'] != dn and \
                    r['document_type'] == fields.get('document_type') and r['nationality'] and fields.get('nationality') and \
                    r['nationality'] != fields.get('nationality'):
                out.append(dict(pattern='same_biographics_different_nationality_documents', severity='high',
                                message=f'{full} (DOB {dob}) previously used {r["document_type"]} {r["document_number"]} '
                                        f'of nationality {r["nationality"]}.', previous_seen_at=r['seen_at'],
                                previous_screening=r['screening_id']))
            if embedding is not None and r['face_embedding'] is not None:
                e = np.frombuffer(r['face_embedding'], dtype=np.float32)
                if e.shape == embedding.shape:
                    sim = float(np.dot(e, embedding) / (np.linalg.norm(e) * np.linalg.norm(embedding) + 1e-9))
                    if sim >= face_threshold and not same_person_bio and r['id'] not in seen_face and \
                            (r['date_of_birth'] != dob or fuzz.token_sort_ratio(full, other) < 80):
                        seen_face.add(r['id'])
                        out.append(dict(pattern='same_face_different_identity', severity='critical', similarity=round(sim, 3),
                                        message=f'Face matches {other} (DOB {r["date_of_birth"]}, {r["document_type"]} '
                                                f'{r["document_number"]}) screened before.', previous_seen_at=r['seen_at'],
                                        previous_screening=r['screening_id']))
        return out

    def enroll(self, screening_id: str, fields: dict, embedding: np.ndarray | None, checkpoint_id: str | None = None) -> str:
        iid = uuid.uuid4().hex
        blob = embedding.astype(np.float32).tobytes() if embedding is not None else None
        with self._lock, self._conn() as c:
            c.execute('INSERT INTO identities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (iid, screening_id, _now(), fields.get('document_type'), fields.get('document_number'),
                       fields.get('issuing_state'), fields.get('surname'), fields.get('given_names'), fields.get('date_of_birth'),
                       fields.get('nationality'), fields.get('sex'), blob, checkpoint_id))
        return iid

    # ---- travel history -------------------------------------------------------------------
    def record_travel(self, screening_id: str, document_number: str | None, visa_number: str | None, direction: str,
                      checkpoint_id: str | None) -> None:
        with self._lock, self._conn() as c:
            c.execute('INSERT INTO travel_events VALUES (?,?,?,?,?,?,?)',
                      (uuid.uuid4().hex, screening_id, document_number, visa_number, direction, checkpoint_id, _now()))

    def entries_on_visa(self, visa_number: str | None) -> int | None:
        if not visa_number:
            return None
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM travel_events WHERE visa_number = ? AND direction = 'ENTRY'",
                             (visa_number,)).fetchone()[0]

    def stats(self) -> dict:
        with self._conn() as c:
            return dict(watchlist=c.execute('SELECT COUNT(*) FROM watchlist WHERE active = 1').fetchone()[0],
                        identities=c.execute('SELECT COUNT(*) FROM identities').fetchone()[0],
                        travel_events=c.execute('SELECT COUNT(*) FROM travel_events').fetchone()[0])

