-- Identity documents: the type the screening service examines.
--
-- Run with:  npm run db:setup
--
-- No BEGIN/COMMIT: a new enum value cannot be used by other statements
-- in the transaction that added it, so the prefix that goes with it
-- lives in 011.

ALTER TYPE doc_type_t ADD VALUE IF NOT EXISTS 'identity_document';

-- What the screening service reported, recorded against the case.
ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'screening';
