require("dotenv").config({ quiet: true });

const crypto = require("crypto");
const db = require("../db");

// ---------------------------------------------------------------
// Print the current 6-digit sign-in code for the demo accounts.
//
//   npm run code
//
// On a real deployment this number comes from an authenticator app on
// the officer's phone. For a demo on one laptop, setting up phones for
// everyone is friction nobody needs, so this reads the same shared
// secret the server holds and works out the same number.
//
// It proves nothing about security - it is the server telling you what
// it expects. Never ship this to a real deployment.
// ---------------------------------------------------------------

function totp(secret) {
    const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    let bits = 0, value = 0;
    const out = [];
    for (const c of secret) {
        const i = A.indexOf(c);
        if (i < 0) continue;
        value = (value << 5) | i;
        bits += 5;
        if (bits >= 8) { out.push((value >>> (bits - 8)) & 255); bits -= 8; }
    }
    const buf = Buffer.alloc(8);
    buf.writeUInt32BE(Math.floor(Date.now() / 1000 / 30), 4);
    const h = crypto.createHmac("sha1", Buffer.from(out)).update(buf).digest();
    const o = h[19] & 15;
    const n = (((h[o] & 127) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3]) >>> 0;
    return String(n % 1000000).padStart(6, "0");
}

(async () => {
    const { rows } = await db.query(
        `SELECT service_number, name, rank, mfa_secret
           FROM users WHERE mfa_secret IS NOT NULL
          ORDER BY service_number`
    );

    if (rows.length === 0) {
        console.log("No accounts found. Run: node scripts/seed.js");
        await db.pool.end();
        return;
    }

    const secondsLeft = 30 - (Math.floor(Date.now() / 1000) % 30);

    console.log("");
    console.log("  Sign-in codes (all passwords are  Test@1234 )");
    console.log("  " + "-".repeat(58));
    for (const r of rows) {
        console.log(
            `  ${r.service_number.padEnd(14)} ${totp(r.mfa_secret)}   ${r.name} (${r.rank})`
        );
    }
    console.log("  " + "-".repeat(58));
    console.log(`  These change in ${secondsLeft} second(s). Re-run for a fresh set.`);
    console.log("");

    await db.pool.end();
})().catch((err) => {
    console.error("could not read codes:", err.message);
    process.exit(1);
});
