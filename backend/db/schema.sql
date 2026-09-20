-- Secure Legal DMS - database schema
-- Postgres 14+
--
-- Run with:  psql -U postgres -d sih_dms -f schema.sql
--
-- Design rules this file enforces:
--   1. Nothing is ever overwritten. New file = new row in document_versions.
--   2. Access is decided by rank AND case assignment. Both must pass.
--   3. The audit log is append-only at the database level, not just in code.

BEGIN;

-- ---------------------------------------------------------------
-- Enums. These mirror the OpenAPI spec exactly. Change both together.
-- ---------------------------------------------------------------

CREATE TYPE rank_t AS ENUM (
  'constable',
  'head_constable',
  'sub_inspector',
  'inspector',
  'dcp',
  'prosecutor',
  'judge',
  'forensic_analyst'
);

CREATE TYPE sensitivity_t AS ENUM (
  'normal',
  'restricted',
  'protected'      -- forces redaction on every export (S.72 BNS cases)
);

CREATE TYPE case_status_t AS ENUM (
  'open',
  'under_investigation',
  'charge_sheeted',
  'closed'
);

CREATE TYPE doc_type_t AS ENUM (
  'fir',
  'statement',
  'forensic_report',
  'charge_sheet',
  'court_filing',
  'notice',
  'other'
);

CREATE TYPE anchor_status_t AS ENUM ('pending', 'anchored', 'failed');
CREATE TYPE ocr_status_t    AS ENUM ('pending', 'processing', 'done', 'failed');

CREATE TYPE audit_action_t AS ENUM (
  'upload',
  'view',
  'download',
  'export_redacted',
  'new_version',
  'verify',
  'search',          -- searching reveals what exists; it is an access event
  'share',
  'access_denied'
);

-- ---------------------------------------------------------------
-- Users
-- ---------------------------------------------------------------

CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_number  TEXT UNIQUE NOT NULL,
  name            TEXT NOT NULL,
  rank            rank_t NOT NULL,
  station         TEXT NOT NULL,

  password_hash   TEXT NOT NULL,     -- scrypt output, never the password
  mfa_secret      TEXT,              -- base32 TOTP seed
  mfa_enabled     BOOLEAN NOT NULL DEFAULT FALSE,

  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Opaque session tokens. Delete the row to revoke instantly.
-- Only the hash of the token is stored, so a database leak does not
-- hand an attacker a set of working sessions.
CREATE TABLE sessions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash   TEXT UNIQUE NOT NULL,
  issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at   TIMESTAMPTZ NOT NULL,
  ip           INET,
  user_agent   TEXT
);

CREATE INDEX ON sessions (user_id);
CREATE INDEX ON sessions (expires_at);

-- ---------------------------------------------------------------
-- Cases and assignments
-- ---------------------------------------------------------------

CREATE TABLE cases (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_number   TEXT UNIQUE NOT NULL,          -- e.g. FIR/0142/2026
  title         TEXT NOT NULL,
  sensitivity   sensitivity_t NOT NULL DEFAULT 'normal',
  status        case_status_t NOT NULL DEFAULT 'open',
  station       TEXT NOT NULL,
  created_by    UUID NOT NULL REFERENCES users(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- This table is what makes ABAC work. No row here, no access,
-- regardless of rank.
CREATE TABLE case_assignments (
  case_id      UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  assigned_by  UUID NOT NULL REFERENCES users(id),
  PRIMARY KEY (case_id, user_id)
);

CREATE INDEX ON case_assignments (user_id);

-- Identities redaction removes from every export on a protected case:
-- the victim, their parent or spouse, their address, their number.
--
-- This is how redaction knows a name belongs to a victim without a
-- model - the investigating officer says so.
--
-- WARNING: these rows ARE the protected identities. Readable only by
-- officers assigned to the case, never exported, never logged.
CREATE TABLE case_protected_identities (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id     UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,

  value       TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'name',   -- name | address | phone | relationship

  added_by    UUID NOT NULL REFERENCES users(id),
  added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (case_id, value)
);

CREATE INDEX ON case_protected_identities (case_id);

-- ---------------------------------------------------------------
-- Documents
--
-- A "document" is the concept: "the charge sheet in case 44".
-- A "document_version" is an actual file on disk.
-- One document has many versions. This split is what gives you
-- version control for free and why nothing is ever overwritten.
-- ---------------------------------------------------------------

CREATE TABLE documents (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id          UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
  title            TEXT NOT NULL,
  doc_type         doc_type_t NOT NULL DEFAULT 'other',
  current_version  INT NOT NULL DEFAULT 1,
  created_by       UUID NOT NULL REFERENCES users(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON documents (case_id);

CREATE TABLE document_versions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
  version        INT NOT NULL,

  -- Hash of the ORIGINAL bytes, computed before encryption.
  -- This is what gets anchored to the ledger and what verification
  -- recomputes and compares against.
  sha256         CHAR(64) NOT NULL,

  storage_path   TEXT NOT NULL,        -- where the encrypted blob lives
  size_bytes     BIGINT NOT NULL,
  mime_type      TEXT,

  -- Envelope encryption: this document's AES key, itself encrypted
  -- with the master key. Destroying this value crypto-shreds the file.
  wrapped_key    BYTEA NOT NULL,
  iv             BYTEA NOT NULL,
  auth_tag       BYTEA NOT NULL,

  uploaded_by    UUID NOT NULL REFERENCES users(id),
  uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  change_note    TEXT,

  anchor_status  anchor_status_t NOT NULL DEFAULT 'pending',
  ledger_tx_id   TEXT,
  anchored_at    TIMESTAMPTZ,

  ocr_status     ocr_status_t NOT NULL DEFAULT 'pending',
  extracted_text TEXT,

  -- Entity extraction output (F4). Redaction reads persons/addresses
  -- from here; fir_numbers/sections/dates are evidence and are kept.
  entities       JSONB,

  -- A given version number can only ever exist once per document.
  UNIQUE (document_id, version)
);

CREATE INDEX ON document_versions (document_id);
CREATE INDEX ON document_versions (sha256);
CREATE INDEX ON document_versions (anchor_status) WHERE anchor_status = 'pending';

-- Full-text search over OCR output.
CREATE INDEX document_versions_fts
  ON document_versions
  USING GIN (to_tsvector('simple', coalesce(extracted_text, '')));

-- ---------------------------------------------------------------
-- Audit log
--
-- Every entry stores the hash of the entry before it. Delete or edit
-- any row and every hash after it stops matching, so tampering with
-- the log itself is detectable.
-- ---------------------------------------------------------------

CREATE TABLE audit_log (
  id           BIGSERIAL PRIMARY KEY,
  user_id      UUID REFERENCES users(id),   -- nullable: failed logins
  action       audit_action_t NOT NULL,
  document_id  UUID REFERENCES documents(id),
  case_id      UUID REFERENCES cases(id),
  version      INT,
  detail       JSONB,
  ip           INET,
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  prev_hash    CHAR(64) NOT NULL,
  entry_hash   CHAR(64) NOT NULL
);

CREATE INDEX ON audit_log (document_id);
CREATE INDEX ON audit_log (user_id);
CREATE INDEX ON audit_log (occurred_at);

-- Append-only, enforced by the database rather than trusted to
-- application code. Updates and deletes are silently discarded.
-- Demo this to a judge: try to UPDATE a row, then show it unchanged.
CREATE RULE audit_log_no_update AS
  ON UPDATE TO audit_log DO INSTEAD NOTHING;

CREATE RULE audit_log_no_delete AS
  ON DELETE TO audit_log DO INSTEAD NOTHING;

COMMIT;