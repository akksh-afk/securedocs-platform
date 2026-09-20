-- Sign-in by registered mobile number and a one-time code.
--
-- Run with:  npm run db:setup

BEGIN;

-- E.164 only (+919876543210), so the same phone typed two ways can
-- never be two accounts. NULL means the officer cannot use OTP sign-in.
ALTER TABLE users ADD COLUMN IF NOT EXISTS mobile_number TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS users_mobile_number ON users (mobile_number);

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_mobile_e164;
ALTER TABLE users ADD CONSTRAINT users_mobile_e164
  CHECK (mobile_number ~ '^\+[1-9][0-9]{7,14}$');

-- One row per code sent. The code itself is never stored: otp_hash is
-- an HMAC keyed with MASTER_KEY and bound to this row's id, so a copy
-- of this table cannot be brute-forced offline the way a plain hash of
-- a six-digit number could.
--
-- A challenge is live while verified_at IS NULL, expires_at is in the
-- future, attempts < max_attempts, and it is the newest row for its
-- user. Requesting a fresh code therefore retires every earlier one.
CREATE TABLE IF NOT EXISTS otp_challenges (
  id            UUID PRIMARY KEY,
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  otp_hash      CHAR(64) NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL,
  verified_at   TIMESTAMPTZ,              -- set once, on success: single use
  attempts      INT NOT NULL DEFAULT 0,
  max_attempts  INT NOT NULL DEFAULT 5,
  ip            INET,

  CHECK (attempts <= max_attempts)
);

CREATE INDEX IF NOT EXISTS otp_challenges_user
  ON otp_challenges (user_id, created_at DESC);

COMMIT;
