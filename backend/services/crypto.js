// All of this uses Node's built-in crypto module. No packages.
// Keeping the dependency list short matters for a security project.

const crypto = require("crypto");
const { promisify } = require("util");

const scrypt = promisify(crypto.scrypt);

// ---------------------------------------------------------------
// Passwords
//
// Stored format:  <salt-hex>:<hash-hex>
// scrypt is deliberately slow, which is what makes brute-forcing
// a leaked password table impractical.
// ---------------------------------------------------------------

async function hashPassword(password) {
    const salt = crypto.randomBytes(16);
    const hash = await scrypt(password, salt, 64);
    return `${salt.toString("hex")}:${hash.toString("hex")}`;
}

async function verifyPassword(password, stored) {
    const [saltHex, hashHex] = String(stored).split(":");
    if (!saltHex || !hashHex) return false;

    const hash = await scrypt(password, Buffer.from(saltHex, "hex"), 64);
    const expected = Buffer.from(hashHex, "hex");

    // Constant-time compare. A normal === leaks information through
    // how long it takes to fail.
    if (hash.length !== expected.length) return false;
    return crypto.timingSafeEqual(hash, expected);
}

// ---------------------------------------------------------------
// Session tokens
//
// The raw token goes to the client once and is never stored.
// We keep only its SHA-256, so a database leak does not hand an
// attacker a set of working sessions.
// ---------------------------------------------------------------

function generateToken() {
    return crypto.randomBytes(32).toString("hex");
}

function hashToken(token) {
    return crypto.createHash("sha256").update(token).digest("hex");
}

function sha256(buffer) {
    return crypto.createHash("sha256").update(buffer).digest("hex");
}

// ---------------------------------------------------------------
// SMS one-time codes
//
// randomInt draws from the CSPRNG without modulo bias.
//
// A six-digit code has a million possible values, so a plain SHA-256 of
// one is reversed by trying them all in well under a second. The HMAC
// is keyed with MASTER_KEY, which never touches the database, and bound
// to the challenge id, so a leaked otp_challenges table is useless and
// one challenge's hash says nothing about another's.
// ---------------------------------------------------------------

function generateOtp() {
    return String(crypto.randomInt(0, 1000000)).padStart(6, "0");
}

function hashOtp(challengeId, otp) {
    const hex = process.env.MASTER_KEY || "";
    if (hex.length !== 64) throw new Error("MASTER_KEY must be 64 hex characters.");

    return crypto
        .createHmac("sha256", Buffer.from(hex, "hex"))
        .update(`otp:${challengeId}:${otp}`)
        .digest("hex");
}

function otpMatches(challengeId, otp, storedHash) {
    const a = Buffer.from(hashOtp(challengeId, otp), "hex");
    const b = Buffer.from(String(storedHash), "hex");
    return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// ---------------------------------------------------------------
// TOTP - the six digit codes in Google Authenticator.
// RFC 6238. Roughly forty lines, so no package needed.
// ---------------------------------------------------------------

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Encode(buffer) {
    let bits = 0;
    let value = 0;
    let out = "";
    for (const byte of buffer) {
        value = (value << 8) | byte;
        bits += 8;
        while (bits >= 5) {
            out += B32[(value >>> (bits - 5)) & 31];
            bits -= 5;
        }
    }
    if (bits > 0) out += B32[(value << (5 - bits)) & 31];
    return out;
}

function base32Decode(str) {
    let bits = 0;
    let value = 0;
    const out = [];
    for (const ch of str.toUpperCase().replace(/=+$/, "")) {
        const idx = B32.indexOf(ch);
        if (idx === -1) continue;
        value = (value << 5) | idx;
        bits += 5;
        if (bits >= 8) {
            out.push((value >>> (bits - 8)) & 255);
            bits -= 8;
        }
    }
    return Buffer.from(out);
}

function generateMfaSecret() {
    return base32Encode(crypto.randomBytes(20));
}

function totpAt(secret, counter) {
    const key = base32Decode(secret);

    const buf = Buffer.alloc(8);
    buf.writeUInt32BE(Math.floor(counter / 0x100000000), 0);
    buf.writeUInt32BE(counter >>> 0, 4);

    const hmac = crypto.createHmac("sha1", key).update(buf).digest();

    // Dynamic truncation, straight from the RFC.
    const offset = hmac[hmac.length - 1] & 0x0f;
    const code =
        (((hmac[offset] & 0x7f) << 24) |
            ((hmac[offset + 1] & 0xff) << 16) |
            ((hmac[offset + 2] & 0xff) << 8) |
            (hmac[offset + 3] & 0xff)) %
        1000000;

    return String(code).padStart(6, "0");
}

// Accepts the previous, current and next 30-second window, so a user
// whose clock is slightly off still gets in.
function verifyTotp(secret, code) {
    if (!/^[0-9]{6}$/.test(String(code || ""))) return false;

    const counter = Math.floor(Date.now() / 1000 / 30);
    const given = Buffer.from(String(code));

    for (const drift of [-1, 0, 1]) {
        const expected = Buffer.from(totpAt(secret, counter + drift));
        if (crypto.timingSafeEqual(given, expected)) return true;
    }
    return false;
}

// URI you can turn into a QR code for Google Authenticator.
function totpUri(secret, serviceNumber) {
    const label = encodeURIComponent(`SIH-DMS:${serviceNumber}`);
    return `otpauth://totp/${label}?secret=${secret}&issuer=SIH-DMS`;
}

module.exports = {
    hashPassword,
    verifyPassword,
    generateToken,
    hashToken,
    sha256,
    generateOtp,
    hashOtp,
    otpMatches,
    generateMfaSecret,
    verifyTotp,
    totpUri,
};