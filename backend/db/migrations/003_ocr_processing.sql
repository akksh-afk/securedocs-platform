-- Adds the 'processing' OCR state.
--
-- Run with:  psql -U postgres -d sih_dms -f db/migrations/003_ocr_processing.sql
--
-- Without a third state the worker cannot claim a row atomically: it
-- would have to SELECT pending rows and then update them, and two
-- workers polling at once would both pick up the same document. With
-- it, the claim is a single UPDATE ... RETURNING that moves rows out of
-- 'pending' in the same statement that reads them.
--
-- It is also worth showing a user - "queued" and "being read" are
-- different answers to "why can I not search this yet".
--
-- No BEGIN/COMMIT: a new enum value cannot be used by other statements
-- in the transaction that added it.

ALTER TYPE ocr_status_t ADD VALUE IF NOT EXISTS 'processing';
