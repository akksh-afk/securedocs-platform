-- Adds the 'search' audit action.
--
-- Searching is an access event: it reveals which documents exist and
-- what they contain. It belongs in the same hash-linked chain as
-- reads and downloads, so the enum has to carry it.
--
-- Run with:  psql -U postgres -d sih_dms -f db/migrations/001_search_audit_action.sql
--
-- No BEGIN/COMMIT here on purpose. A new enum value cannot be used by
-- other statements in the transaction that added it, so this runs on
-- its own.

ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'search';
