-- Audit actions for case-level events and mobile OTP sign-in.
--
-- Run with:  npm run db:setup
--
-- No BEGIN/COMMIT: a new enum value cannot be used by other statements
-- in the transaction that added it, so these live apart from the
-- constraint in 008 that names them.

ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'login';
ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'case_create';
ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'case_update';
ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'assign';
ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'unassign';
ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'identity_add';
ALTER TYPE audit_action_t ADD VALUE IF NOT EXISTS 'delete_attempt';
