-- Results from the fake-identity screening service, against the version
-- that was screened.
--
-- Run with:  npm run db:setup

BEGIN;

-- pending  - an identity document waiting to be screened
-- done     - screened, see screening_result
-- failed   - the service could not be reached or refused the file
-- skipped  - not an identity document, or screening is switched off
ALTER TABLE document_versions
  ADD COLUMN IF NOT EXISTS screening_status TEXT NOT NULL DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS screening_result JSONB,
  ADD COLUMN IF NOT EXISTS screened_at TIMESTAMPTZ;

ALTER TABLE document_versions DROP CONSTRAINT IF EXISTS document_versions_screening_status;
ALTER TABLE document_versions ADD CONSTRAINT document_versions_screening_status
  CHECK (screening_status IN ('pending', 'done', 'failed', 'skipped'));

-- The prefix for the type added in 010.
--
-- Without this, identity_document fell to the ELSE branch and was
-- numbered OTH-001 - the same string 'other' already uses in that case,
-- which the unique index on (case_id, evidence_number) rejects. Every
-- new document type needs its own prefix here.
CREATE OR REPLACE FUNCTION format_evidence_number(t doc_type_t, seq INT)
  RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE t
           WHEN 'fir'               THEN 'FIR'
           WHEN 'statement'         THEN 'STM'
           WHEN 'forensic_report'   THEN 'FSR'
           WHEN 'charge_sheet'      THEN 'CHS'
           WHEN 'court_filing'      THEN 'CRT'
           WHEN 'notice'            THEN 'NTC'
           WHEN 'identity_document' THEN 'IDN'
           ELSE 'OTH'
         END
         -- greatest() so number 1000 becomes 1000, not a truncated 100.
         || '-' || lpad(seq::text, greatest(3, length(seq::text)), '0')
$$;

COMMIT;
