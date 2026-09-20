-- Evidence numbers: every document gets a type-specific number within
-- its case - FIR-001, STM-001, STM-002, FSR-001 - assigned by the
-- database, never typed by an officer.
--
-- Run with:  npm run db:setup
--
-- case_number + evidence_number identifies an exhibit to a person;
-- documents.id stays the key every foreign key points at.

BEGIN;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS evidence_seq INT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS evidence_number TEXT;

-- The one mapping from type to prefix. Change it here and nowhere else.
CREATE OR REPLACE FUNCTION format_evidence_number(t doc_type_t, seq INT)
  RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE t
           WHEN 'fir'             THEN 'FIR'
           WHEN 'statement'       THEN 'STM'
           WHEN 'forensic_report' THEN 'FSR'
           WHEN 'charge_sheet'    THEN 'CHS'
           WHEN 'court_filing'    THEN 'CRT'
           WHEN 'notice'          THEN 'NTC'
           ELSE 'OTH'
         END
         -- greatest() so number 1000 becomes 1000, not a truncated 100.
         || '-' || lpad(seq::text, greatest(3, length(seq::text)), '0')
$$;

-- Number what already exists, oldest first within each case and type.
WITH numbered AS (
  SELECT id,
         row_number() OVER (PARTITION BY case_id, doc_type
                            ORDER BY created_at, id)::int AS seq
    FROM documents
   WHERE evidence_seq IS NULL
)
UPDATE documents d
   SET evidence_seq    = n.seq,
       evidence_number = format_evidence_number(d.doc_type, n.seq)
  FROM numbered n
 WHERE n.id = d.id;

ALTER TABLE documents ALTER COLUMN evidence_seq    SET NOT NULL;
ALTER TABLE documents ALTER COLUMN evidence_number SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS documents_case_evidence_number
  ON documents (case_id, evidence_number);

-- ---------------------------------------------------------------
-- Assign the number on insert. Whatever the application sends is
-- overwritten, so no code path can file a document without one or
-- choose its own.
--
-- The case row lock serialises numbering within one case, so two
-- simultaneous uploads cannot both become STM-004. NO KEY UPDATE rather
-- than UPDATE: rows that merely reference the case (audit entries,
-- assignments) are not blocked by it.
--
-- With no description given, the title falls back to the evidence
-- number, so every existing reader of documents.title keeps working.
-- ---------------------------------------------------------------
CREATE OR REPLACE FUNCTION documents_assign_evidence_number()
  RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM 1 FROM cases WHERE id = NEW.case_id FOR NO KEY UPDATE;

  SELECT coalesce(max(evidence_seq), 0) + 1
    INTO NEW.evidence_seq
    FROM documents
   WHERE case_id = NEW.case_id AND doc_type = NEW.doc_type;

  NEW.evidence_number := format_evidence_number(NEW.doc_type, NEW.evidence_seq);
  NEW.title := coalesce(nullif(btrim(NEW.title), ''), NEW.evidence_number);
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS documents_evidence_number ON documents;
CREATE TRIGGER documents_evidence_number
  BEFORE INSERT ON documents
  FOR EACH ROW EXECUTE FUNCTION documents_assign_evidence_number();

-- ---------------------------------------------------------------
-- An exhibit's identity is fixed at creation. It cannot move to
-- another case, change type (its number is type-specific), be
-- renumbered, or be re-attributed to a different officer. Only the
-- description and the current version pointer ever change.
-- ---------------------------------------------------------------
CREATE OR REPLACE FUNCTION documents_identity_immutable()
  RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.case_id         IS DISTINCT FROM OLD.case_id
  OR NEW.doc_type        IS DISTINCT FROM OLD.doc_type
  OR NEW.evidence_seq    IS DISTINCT FROM OLD.evidence_seq
  OR NEW.evidence_number IS DISTINCT FROM OLD.evidence_number
  OR NEW.created_by      IS DISTINCT FROM OLD.created_by
  OR NEW.created_at      IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'evidence % is immutable: case, type, number and creator are fixed',
      OLD.evidence_number
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS documents_identity_immutable ON documents;
CREATE TRIGGER documents_identity_immutable
  BEFORE UPDATE ON documents
  FOR EACH ROW EXECUTE FUNCTION documents_identity_immutable();

COMMIT;
