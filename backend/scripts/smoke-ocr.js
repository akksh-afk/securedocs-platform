require("dotenv").config();

const { Jimp, loadFont } = require("jimp");
const { SANS_32_BLACK } = require("jimp/fonts");

const db = require("../db");
const storage = require("../services/storage");
const ocr = require("../services/ocr");
const entities = require("../services/entities");
const redaction = require("../services/redaction");

// ---------------------------------------------------------------
// End-to-end smoke test for F3 -> F4 -> F5, against a real database.
//
//   node scripts/smoke-ocr.js
//
// Renders an FIR page, stores it through the real encrypted storage
// path, runs OCR, extracts entities, and redacts an export. It writes
// a document into the database and deletes it again on the way out.
//
// This is a dev tool. It proves the pipeline works on THIS machine,
// which is the only thing worth knowing the morning of a demo.
// ---------------------------------------------------------------

const LINES = [
    "FIR 0142 of 2026",
    "Under BNS Section 74 and Section 72",
    "Complainant Sunita Sharma",
    "Daughter of Ramesh Sharma",
    "Phone 9876543210",
    "Recorded by Inspector A Deshmukh",
];

async function renderPage() {
    const font = await loadFont(SANS_32_BLACK);
    const img = new Jimp({ width: 950, height: 90 + LINES.length * 60, color: 0xffffffff });

    LINES.forEach((text, i) => img.print({ font, x: 30, y: 40 + i * 60, text }));
    img.rotate(2.5); // scanners are never straight

    return img.getBuffer("image/png");
}

(async () => {
    const { rows: users } = await db.query(
        "SELECT id, station FROM users WHERE service_number = 'DL-INS-1001'"
    );
    const { rows: cases } = await db.query(
        "SELECT id FROM cases WHERE case_number = 'FIR/0142/2026'"
    );

    if (!users[0] || !cases[0]) {
        console.error("Seed data missing. Run: node scripts/seed.js");
        process.exit(1);
    }

    const userId = users[0].id;
    const caseId = cases[0].id;

    console.log("1. rendering and encrypting a scanned FIR...");
    const page = await renderPage();
    const blob = await storage.store(page);
    console.log(`   stored via '${storage.BACKEND}' backend, sha256 ${blob.sha256.slice(0, 16)}...`);

    const doc = await db.query(
        `INSERT INTO documents (case_id, title, doc_type, current_version, created_by)
         VALUES ($1, 'SMOKE TEST - scanned FIR', 'fir', 1, $2) RETURNING id`,
        [caseId, userId]
    );
    const documentId = doc.rows[0].id;

    const ver = await db.query(
        `INSERT INTO document_versions
           (document_id, version, sha256, storage_path, size_bytes, mime_type,
            wrapped_key, iv, auth_tag, uploaded_by)
         VALUES ($1,1,$2,$3,$4,'image/png',$5,$6,$7,$8) RETURNING id, ocr_status`,
        [
            documentId, blob.sha256, blob.storage_path, blob.size_bytes,
            blob.wrapped_key, blob.iv, blob.auth_tag, userId,
        ]
    );
    const versionId = ver.rows[0].id;
    console.log(`   version queued with ocr_status='${ver.rows[0].ocr_status}'`);

    try {
        console.log("2. running OCR on the decrypted bytes (never touching disk)...");
        const row = await db.query(
            `SELECT storage_path, wrapped_key, iv, auth_tag FROM document_versions WHERE id = $1`,
            [versionId]
        );
        const plaintext = await storage.retrieve(row.rows[0]);
        const result = await ocr.recognise(plaintext);

        console.log(`   confidence ${Math.round(result.confidence)}, deskew ${result.skew}`);
        console.log(`   text: ${JSON.stringify(result.text.replace(/\s+/g, " ").slice(0, 110))}`);

        console.log("3. extracting entities...");
        const found = entities.extract(result.text);
        console.log(`   persons:  ${JSON.stringify(found.persons)}`);
        console.log(`   phones:   ${JSON.stringify(found.phones)}`);
        console.log(`   sections: ${JSON.stringify(found.sections)}`);
        console.log(`   fir:      ${JSON.stringify(found.fir_numbers)}`);

        await db.query(
            `UPDATE document_versions
                SET extracted_text=$1, entities=$2, ocr_status='done' WHERE id=$3`,
            [result.text, found, versionId]
        );

        console.log("4. full-text search over what OCR produced...");
        const hits = await db.query(
            `SELECT d.title FROM document_versions v
               JOIN documents d ON d.id = v.document_id
               JOIN cases c ON c.id = d.case_id
               JOIN case_assignments a ON a.case_id = c.id AND a.user_id = $1
              WHERE to_tsvector('simple', coalesce(v.extracted_text,''))
                    @@ plainto_tsquery('simple', $2)`,
            [userId, "Sunita"]
        );
        console.log(`   search for "Sunita" returned ${hits.rows.length} hit(s)`);

        console.log("5. redacting an export driven by those entities...");
        const targets = redaction.targetsFrom({ extracted: found });
        const { buffer, removed } = redaction.redact(
            Buffer.from(result.text),
            "text/plain",
            targets
        );
        const out = buffer.toString().replace(/\s+/g, " ");
        console.log(`   removed ${removed} span(s)`);
        console.log(`   released: ${JSON.stringify(out.slice(0, 110))}`);

        const leaked = /Sunita|9876543210/.test(out);
        console.log(leaked ? "   LEAK: identity survived redaction" : "   identity absent from released copy");

        console.log(
            /0142/.test(out) ? "   FIR number preserved" : "   WARNING: FIR number was eaten"
        );
    } finally {
        console.log("6. cleaning up the smoke-test document...");
        await db.query("DELETE FROM document_versions WHERE id = $1", [versionId]);
        await db.query("DELETE FROM documents WHERE id = $1", [documentId]);
        await ocr.shutdown();
        await db.pool.end();
    }
})().catch((err) => {
    console.error("smoke test failed:", err.message);
    process.exit(1);
});
