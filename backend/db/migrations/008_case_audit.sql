-- Tie every case and evidence event in the audit log to its case, at
-- the database level.
--
-- Run with:  npm run db:setup

BEGIN;

-- A case's full trail, oldest first, is one index range scan.
CREATE INDEX IF NOT EXISTS audit_log_case ON audit_log (case_id, id);

-- Only three kinds of event may stand outside a case: signing in,
-- searching across cases, and a denial that never reached a case
-- (a bad login, a rank refusal on the directory). Everything else -
-- uploads, views, versions, case edits, assignments - must name its
-- case, and anything naming a document must name a case too.
--
-- NOT VALID: enforced on every new row. Existing rows cannot be
-- corrected anyway - the log is append-only.
ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_case_scoped;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_case_scoped
  CHECK (
    case_id IS NOT NULL
    OR (document_id IS NULL AND action IN ('login', 'search', 'access_denied'))
  ) NOT VALID;

-- And the case it names must be the case the document is actually in.
-- A composite foreign key says so declaratively; documents.case_id can
-- never change afterwards (see 007), so this holds for good.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'documents_id_case_key') THEN
    ALTER TABLE documents ADD CONSTRAINT documents_id_case_key UNIQUE (id, case_id);
  END IF;
END $$;

ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_document_case_fk;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_document_case_fk
  FOREIGN KEY (document_id, case_id) REFERENCES documents (id, case_id) NOT VALID;

COMMIT;
