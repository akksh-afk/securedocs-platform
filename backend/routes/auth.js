const express = require("express");
const crypto = require("crypto");

const db = require("../db");
const audit = require("../services/audit");
const sms = require("../services/sms");
const { requireAuth } = require("../middleware/auth");
const {
    verifyPassword,
    generateToken,
    hashToken,
    verifyTotp,
    generateOtp,
    hashOtp,
    otpMatches,
} = require("../services/crypto");

const router = express.Router();

const SESSION_HOURS = 8;
const MFA_WINDOW_MS = 5 * 60 * 1000;

const OTP_TTL_MS = 5 * 60 * 1000;
const OTP_MAX_ATTEMPTS = 5;
const OTP_RESEND_AFTER_S = 60;
const OTP_PER_NUMBER_PER_15_MIN = 3;
const MFA_MAX_ATTEMPTS = 5;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Pending MFA challenges live in memory. They last five minutes and
// losing them on restart is fine - the user just logs in again.
// If you ever run more than one server process, move this to the
// database or Redis.
const pendingMfa = new Map();

// Per-IP request counts for the OTP endpoints.
// ponytail: per-process memory, same ceiling as pendingMfa - move to
// Redis or a table when there is more than one server process. The
// per-number and per-code limits live in the database and do not
// depend on this.
const ipHits = new Map();

function overIpLimit(key, max, windowMs = 15 * 60 * 1000) {
    const now = Date.now();
    const hit = ipHits.get(key);
    if (!hit || hit.resetAt < now) {
        ipHits.set(key, { n: 1, resetAt: now + windowMs });
        return false;
    }
    return ++hit.n > max;
}

setInterval(() => {
    const now = Date.now();
    for (const [key, value] of pendingMfa) {
        if (value.expiresAt < now) pendingMfa.delete(key);
    }
    for (const [key, value] of ipHits) {
        if (value.resetAt < now) ipHits.delete(key);
    }
}, 60 * 1000).unref();

// Both sign-in paths end here: a session row, a login entry in the
// audit log, and the same response shape. Runs on the caller's client
// so the session and its audit entry commit together.
async function issueSession(client, req, user, method) {
    const token = generateToken();
    const expiresAt = new Date(Date.now() + SESSION_HOURS * 3600 * 1000);

    await client.query(
        `INSERT INTO sessions (user_id, token_hash, expires_at, ip, user_agent)
       VALUES ($1, $2, $3, $4, $5)`,
        [user.id, hashToken(token), expiresAt, req.ip, req.get("user-agent") || null]
    );

    await audit.append(
        { userId: user.id, action: "login", detail: { method }, ip: req.ip },
        client
    );

    return {
        session_token: token,
        expires_at: expiresAt.toISOString(),
        user: {
            id: user.id,
            name: user.name,
            service_number: user.service_number,
            rank: user.rank,
            station: user.station,
        },
    };
}

// ---------------------------------------------------------------
// POST /auth/otp/request
// Step 1 of sign-in: service number, password AND registered mobile
// number. Only when all three belong to the same active officer is a
// code texted to that number. The code is the second factor - the
// password alone never yields a session, and neither does the phone.
//
// A wrong password and a right password with someone else's number get
// the same answer, so this cannot be used to learn which phone belongs
// to which officer.
// ---------------------------------------------------------------
router.post("/otp/request", async (req, res, next) => {
    const { service_number, password } = req.body || {};
    const mobile = sms.normalizeMobile((req.body || {}).mobile_number);

    if (!service_number || !password || !mobile) {
        return res.status(400).json({
            error: "bad_request",
            message: "service_number, password and a valid mobile_number are required.",
        });
    }

    if (overIpLimit(`otp-request:${req.ip}`, 10)) {
        return res.status(429).json({
            error: "rate_limited",
            message: "Too many requests. Wait a few minutes and try again.",
        });
    }

    try {
        const { rows } = await db.query(
            `SELECT id, password_hash, mobile_number, is_active
               FROM users WHERE service_number = $1`,
            [service_number]
        );
        const user = rows[0];
        const passwordOk =
            user && user.is_active && (await verifyPassword(password, user.password_hash));

        if (!passwordOk || user.mobile_number !== mobile) {
            await audit
                .append({
                    userId: user ? user.id : null,
                    action: "access_denied",
                    detail: {
                        attempted: "login",
                        service_number,
                        reason: !passwordOk
                            ? "credentials"
                            : user.mobile_number
                              ? "mobile_mismatch"
                              : "no_mobile_registered",
                    },
                    ip: req.ip,
                })
                .catch(() => {});

            // Past a correct password there is nothing to protect by
            // being vague, and "invalid credentials" for an account that
            // simply has no number registered sends the officer hunting
            // for a password that was never wrong.
            if (passwordOk && !user.mobile_number) {
                return res.status(409).json({
                    error: "no_mobile_registered",
                    message:
                        "No mobile number is registered for this account, so no code can be sent. Ask your administrator to register one.",
                });
            }

            return res.status(401).json({
                error: "unauthorized",
                message: "Invalid credentials.",
            });
        }

        const otp = generateOtp();

        // Past the password, there is nothing left to enumerate, so the
        // per-number limits can say plainly why no code was sent.
        const result = await db.transaction(async (client) => {
            // The row lock makes the count-then-insert below atomic per
            // officer, so parallel requests cannot slip past the limit.
            await client.query("SELECT 1 FROM users WHERE id = $1 FOR NO KEY UPDATE", [user.id]);

            const recent = await client.query(
                `SELECT count(*)::int AS n,
                        coalesce(max(created_at) > now() - make_interval(secs => $2), false) AS too_soon
                   FROM otp_challenges
                  WHERE user_id = $1 AND created_at > now() - interval '15 minutes'`,
                [user.id, OTP_RESEND_AFTER_S]
            );
            if (recent.rows[0].too_soon) {
                return { limited: "A code was sent less than a minute ago. Wait before requesting another." };
            }
            if (recent.rows[0].n >= OTP_PER_NUMBER_PER_15_MIN) {
                return { limited: "Too many codes requested. Try again in 15 minutes." };
            }

            // A fresh code retires every earlier one, so only the code
            // just sent can ever be used.
            await client.query(
                `UPDATE otp_challenges SET expires_at = now()
                  WHERE user_id = $1 AND verified_at IS NULL AND expires_at > now()`,
                [user.id]
            );

            const id = crypto.randomUUID();
            await client.query(
                `INSERT INTO otp_challenges (id, user_id, otp_hash, expires_at, max_attempts, ip)
                 VALUES ($1, $2, $3, now() + make_interval(secs => $4), $5, $6)`,
                [id, user.id, hashOtp(id, otp), OTP_TTL_MS / 1000, OTP_MAX_ATTEMPTS, req.ip]
            );
            return { id };
        });

        if (result.limited) {
            return res.status(429).json({ error: "rate_limited", message: result.limited });
        }

        try {
            await sms.send(
                mobile,
                `${otp} is your SecureDocs sign-in code. It expires in 5 minutes. Never share it.`
            );
        } catch (err) {
            // A code that never arrived must not use up the officer's
            // allowance, or a flaky gateway locks them out.
            console.error("otp sms failed:", err.message);
            await db.query("DELETE FROM otp_challenges WHERE id = $1", [result.id]);
            return res.status(502).json({
                error: "sms_failed",
                message: "The code could not be sent. Try again shortly.",
            });
        }

        return res.status(202).json({
            // Identifies the code to verify against. Random per request
            // and handed only to whoever passed the password, so nobody
            // else can spend this officer's attempts on it.
            otp_token: result.id,
            message: `A code has been sent to ${sms.maskMobile(mobile)}.`,
            expires_in: OTP_TTL_MS / 1000,
            resend_after: OTP_RESEND_AFTER_S,
        });
    } catch (err) {
        next(err);
    }
});

// ---------------------------------------------------------------
// POST /auth/otp/verify
// Step 2. Exchanges the code for a session.
//
// Keyed on the otp_token from step 1, not on the mobile number. Keyed
// on the number, anyone who knew an officer's phone could spend that
// officer's five attempts and lock out the code they were waiting for,
// without knowing the password. The token is random per request and is
// only ever handed to whoever passed the password.
//
// Every guess costs an attempt before it is checked, in one atomic
// UPDATE, so parallel guesses cannot share an attempt. After
// OTP_MAX_ATTEMPTS the challenge is dead even for the right code, and
// success stamps verified_at in a statement only one request can win,
// so a code works once.
//
// Unknown token, wrong code, expired code, used code: one answer.
// ---------------------------------------------------------------
router.post("/otp/verify", async (req, res, next) => {
    const { otp_token, otp } = req.body || {};
    const token = String(otp_token || "");
    const code = String(otp || "").trim();

    if (!UUID.test(token) || !/^\d{6}$/.test(code)) {
        return res.status(400).json({
            error: "bad_request",
            message: "otp_token and a 6-digit otp are required.",
        });
    }

    if (overIpLimit(`otp-verify:${req.ip}`, 20)) {
        return res.status(429).json({
            error: "rate_limited",
            message: "Too many attempts. Wait a few minutes and try again.",
        });
    }

    const rejected = () =>
        res.status(401).json({
            error: "invalid_code",
            message: "That code is incorrect or has expired. Request a new one.",
        });

    try {
        const { rows } = await db.query(
            `UPDATE otp_challenges c
                SET attempts = c.attempts + 1
               FROM users u
              WHERE c.id = $1
                AND c.user_id = u.id
                AND u.is_active
                AND c.verified_at IS NULL
                AND c.expires_at > now()
                AND c.attempts < c.max_attempts
          RETURNING c.id, c.otp_hash, u.id AS user_id, u.name,
                    u.service_number, u.rank, u.station`,
            [token]
        );

        const challenge = rows[0];

        if (!challenge || !otpMatches(challenge.id, code, challenge.otp_hash)) {
            await audit
                .append({
                    userId: challenge ? challenge.user_id : null,
                    action: "access_denied",
                    detail: { attempted: "otp_login" },
                    ip: req.ip,
                })
                .catch(() => {});
            return rejected();
        }

        const session = await db.transaction(async (client) => {
            // Lose a race with a parallel request presenting the same
            // code and this finds nothing to stamp.
            const used = await client.query(
                `UPDATE otp_challenges SET verified_at = now()
                  WHERE id = $1 AND verified_at IS NULL
                  RETURNING id`,
                [challenge.id]
            );
            if (!used.rows[0]) return null;

            return issueSession(
                client,
                req,
                {
                    id: challenge.user_id,
                    name: challenge.name,
                    service_number: challenge.service_number,
                    rank: challenge.rank,
                    station: challenge.station,
                },
                "mobile_otp"
            );
        });

        return session ? res.json(session) : rejected();
    } catch (err) {
        next(err);
    }
});

// ---------------------------------------------------------------
// POST /auth/login
// Step 1. Never returns a session, only an MFA challenge.
// ---------------------------------------------------------------
router.post("/login", async (req, res, next) => {
    const { service_number, password } = req.body || {};

    if (!service_number || !password) {
        return res.status(400).json({
            error: "bad_request",
            message: "service_number and password are required.",
        });
    }

    try {
        const { rows } = await db.query(
            `SELECT id, password_hash, mfa_secret, mfa_enabled, is_active
         FROM users WHERE service_number = $1`,
            [service_number]
        );

        const user = rows[0];

        // Same response whether the user does not exist or the password is
        // wrong. Otherwise you have handed an attacker a way to enumerate
        // valid service numbers.
        const ok =
            user && user.is_active && (await verifyPassword(password, user.password_hash));

        if (!ok) {
            await audit
                .append({
                    userId: user ? user.id : null,
                    action: "access_denied",
                    detail: { attempted: "login", service_number },
                    ip: req.ip,
                })
                .catch(() => {});

            return res.status(401).json({
                error: "unauthorized",
                message: "Invalid credentials.",
            });
        }

        const mfaToken = generateToken();
        pendingMfa.set(mfaToken, {
            userId: user.id,
            expiresAt: Date.now() + MFA_WINDOW_MS,
            attempts: 0,
        });

        return res.json({
            mfa_token: mfaToken,
            expires_in: MFA_WINDOW_MS / 1000,
        });
    } catch (err) {
        next(err);
    }
});

// ---------------------------------------------------------------
// POST /auth/mfa/verify
// Step 2. Exchanges a TOTP code for a real session.
// ---------------------------------------------------------------
router.post("/mfa/verify", async (req, res, next) => {
    const { mfa_token, code } = req.body || {};

    if (!mfa_token || !code) {
        return res.status(400).json({
            error: "bad_request",
            message: "mfa_token and code are required.",
        });
    }

    const pending = pendingMfa.get(mfa_token);

    // Distinct codes for two very different situations. A mistyped or
    // expired six-digit code means try again with a fresh one; a dead
    // challenge means start over from the password. Both used to return
    // "unauthorized", so the screen could not tell them apart and sent
    // people back to the password step for a stale code - where the
    // next thing they saw was a credentials error for a password that
    // had never been wrong.
    if (!pending || pending.expiresAt < Date.now()) {
        pendingMfa.delete(mfa_token);
        return res.status(401).json({
            error: "challenge_expired",
            message: "This sign-in attempt timed out. Enter your password again.",
        });
    }

    try {
        const { rows } = await db.query(
            `SELECT id, service_number, name, rank, station, mfa_secret, mfa_enabled, is_active
         FROM users WHERE id = $1`,
            [pending.userId]
        );
        const user = rows[0];

        // Re-checked here, not only at the password step: an account
        // disabled or removed in between must not complete a sign-in.
        if (!user || !user.is_active) {
            pendingMfa.delete(mfa_token);
            return res.status(401).json({
                error: "unauthorized",
                message: "Invalid credentials.",
            });
        }

        // A second factor is not optional. Without this, an account with
        // mfa_enabled = FALSE - the column default - took the password
        // alone and skipped the code entirely, which is the whole point
        // of both sign-in paths.
        if (!user.mfa_enabled) {
            pendingMfa.delete(mfa_token);
            return res.status(409).json({
                error: "mfa_not_enrolled",
                message:
                    "This account has no authenticator app enrolled. Sign in with your mobile number instead.",
            });
        }

        if (!verifyTotp(user.mfa_secret, code)) {
            // The challenge is deliberately left alive so the officer can
            // simply read a fresh code and try again. Codes change every
            // thirty seconds and being typed a moment too late is by far
            // the most common reason to land here.
            //
            // Alive, but not forever: a challenge that accepted guesses
            // for its whole five minutes let anyone holding the password
            // brute-force a six-digit code.
            pending.attempts += 1;
            if (pending.attempts >= MFA_MAX_ATTEMPTS) {
                pendingMfa.delete(mfa_token);
                return res.status(401).json({
                    error: "challenge_expired",
                    message: "Too many incorrect codes. Enter your password again.",
                });
            }

            return res.status(401).json({
                error: "invalid_code",
                message:
                    "That code was not accepted. Codes change every 30 seconds - get a fresh one and enter it straight away.",
            });
        }

        // One code, one use.
        pendingMfa.delete(mfa_token);

        return res.json(
            await db.transaction((client) => issueSession(client, req, user, "password_totp"))
        );
    } catch (err) {
        next(err);
    }
});

// ---------------------------------------------------------------
// POST /auth/logout
// Deleting the row revokes the session instantly. This is the whole
// reason for opaque tokens over JWT.
// ---------------------------------------------------------------
router.post("/logout", requireAuth, async (req, res, next) => {
    try {
        await db.query("DELETE FROM sessions WHERE id = $1", [req.sessionId]);
        return res.status(204).end();
    } catch (err) {
        next(err);
    }
});

module.exports = router;