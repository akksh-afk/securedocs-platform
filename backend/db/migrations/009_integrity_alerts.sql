-- Automatic integrity checking and the alerts it raises.
--
-- Run with:  npm run db:setup

BEGIN;

-- Written by the sweep in services/integrity.js. integrity_failed_at is
-- set when a version first fails and cleared if it verifies again; the
-- audit log keeps every change of state for good.
ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS integrity_checked_at TIMESTAMPTZ;
ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS integrity_failed_at  TIMESTAMPTZ;

-- An officer's inbox. Not evidence and not the record - the audit log
-- is - so rows cascade away with the case they point at.
CREATE TABLE IF NOT EXISTS notifications (
  id           BIGSERIAL PRIMARY KEY,
  user_id      UUID NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
  kind         TEXT NOT NULL,                  -- tamper | integrity_restored
  case_id      UUID REFERENCES cases(id)       ON DELETE CASCADE,
  document_id  UUID REFERENCES documents(id)   ON DELETE CASCADE,
  version      INT,
  message      TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS notifications_user ON notifications (user_id, id DESC);

COMMIT;
