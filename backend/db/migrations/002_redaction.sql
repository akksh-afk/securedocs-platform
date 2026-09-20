-- Redaction support (F5), plus the column F4 will write into.
--
-- Run with:  psql -U postgres -d sih_dms -f db/migrations/002_redaction.sql

BEGIN;

-- ---------------------------------------------------------------
-- F4's output lands here. F5 reads it.
--
-- Expected shape, so that whoever builds F4 writes what redaction
-- already knows how to consume:
--
--   {
--     "persons":   ["..."],   -- NER, feeds redaction
--     "addresses": ["..."],   -- NER, feeds redaction
--     "phones":    ["..."],   -- rules
--     "fir_numbers": ["..."], -- rules, KEPT not redacted
--     "sections":  ["..."],   -- rules, KEPT not redacted
--     "dates":     ["..."]    -- rules, KEPT not redacted
--   }
--
-- Redaction tolerates this being null. It does not depend on F4 having
-- landed; F4 only makes it better at finding names in free text.
-- ---------------------------------------------------------------
ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS entities JSONB;

-- ---------------------------------------------------------------
-- Identities the investigating officer has flagged as protected on a
-- case - the victim, their parent or spouse, their address, their
-- phone number.
--
-- This is how redaction knows a name is a victim's name without a
-- model. In an S.72 case the IO already knows who the victim is;
-- asking them to say so is more reliable than inferring it, and it
-- works today rather than after F4 ships.
--
-- WARNING: this table holds exactly the identities the system exists
-- to protect. It is readable only by officers assigned to the case,
-- it is never exported, and it must never be logged. Treat any change
-- to who can read it as a change to the victim's safety.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS case_protected_identities (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id     UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,

  value       TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'name',   -- name | address | phone | relationship

  added_by    UUID NOT NULL REFERENCES users(id),
  added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (case_id, value)
);

CREATE INDEX IF NOT EXISTS case_protected_identities_case
  ON case_protected_identities (case_id);

COMMIT;
