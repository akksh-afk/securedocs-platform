require("dotenv").config({ quiet: true });

const crypto = require("crypto");
const { Jimp, loadFont } = require("jimp");
const { SANS_32_BLACK } = require("jimp/fonts");

const db = require("../db");

// ---------------------------------------------------------------
// Upload a rendered "scanned FIR" through the real HTTP API.
//
//   node scripts/seed-scan.js [case_number]
//
// Goes through login, MFA, multer, the policy engine and anchoring -
// the same path an officer's upload takes - so the document that lands
// is a real one the OCR worker will pick up. A dev tool for putting
// something demonstrable in front of the UI.
// ---------------------------------------------------------------

const BASE = `http://localhost:${process.env.PORT || 5000}/api/v1`;
const SERVICE_NUMBER = "DL-INS-1001";
const PASSWORD = "Test@1234";
const CASE_NUMBER = process.argv[2] || "FIR/0142/2026";

const LINES = [
    "FIR No. 142 of 2026",
    "Under BNS Section 74 and Section 72",
    "Complainant Sunita Sharma",
    "Daughter of Ramesh Sharma",
    "Resident of 14 Nehru Road",
    "Phone 9876543210",
    "Recorded by Inspector A Deshmukh",
];

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

async function render() {
    const font = await loadFont(SANS_32_BLACK);
    const img = new Jimp({ width: 950, height: 90 + LINES.length * 58, color: 0xffffffff });
    LINES.forEach((t, i) => img.print({ font, x: 30, y: 40 + i * 58, text: t }));
    img.rotate(2.5);
    return img.getBuffer("image/png");
}

(async () => {
    const { rows } = await db.query(
        "SELECT mfa_secret FROM users WHERE service_number = $1",
        [SERVICE_NUMBER]
    );
    const { rows: cases } = await db.query(
        "SELECT id FROM cases WHERE case_number = $1",
        [CASE_NUMBER]
    );
    if (!rows[0] || !cases[0]) throw new Error("seed data missing");

    const login = await fetch(`${BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service_number: SERVICE_NUMBER, password: PASSWORD }),
    }).then((r) => r.json());

    const session = await fetch(`${BASE}/auth/mfa/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            mfa_token: login.mfa_token,
            code: totp(rows[0].mfa_secret),
        }),
    }).then((r) => r.json());

    if (!session.session_token) throw new Error("MFA failed: " + JSON.stringify(session));
    console.log("authenticated");

    const png = await render();
    const form = new FormData();
    form.append("case_id", cases[0].id);
    form.append("title", "Scanned FIR (seeded)");
    form.append("doc_type", "fir");
    form.append("file", new Blob([png], { type: "image/png" }), "fir-scan.png");

    const res = await fetch(`${BASE}/documents`, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.session_token}` },
        body: form,
    });
    const doc = await res.json();

    if (!res.ok) throw new Error("upload failed: " + JSON.stringify(doc));

    console.log(`uploaded into ${CASE_NUMBER}`);
    console.log(`  document_id : ${doc.document_id}`);
    console.log(`  sha256      : ${doc.sha256}`);
    console.log(`  ocr_status  : ${doc.ocr_status}`);
    console.log(`  open at     : http://localhost:5173/documents/${doc.document_id}`);
    console.log("\nNow run:  npm run worker");

    await db.pool.end();
})().catch((err) => {
    console.error(err.message);
    process.exit(1);
});
