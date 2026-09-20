require("dotenv").config({ quiet: true });

const fs = require("fs");
const path = require("path");
const db = require("../db");
const storage = require("../services/storage");

// ---------------------------------------------------------------
// Damage a stored file on purpose, for the tamper-detection demo.
//
//   npm run tamper                 the most recently uploaded document
//   npm run tamper -- <document_id>   a specific one
//
// This exists because the obvious manual instruction - "open the newest
// .enc file and change a character" - is wrong more often than it is
// right. The newest file on disk belongs to whichever document was
// uploaded last, which during a demo is usually NOT the one on screen.
// Follow that instruction and you corrupt the wrong document, click
// Verify, and it still says AUTHENTIC in front of an audience.
//
// This picks the file by document instead of by timestamp, and prints
// exactly what it hit so there is no ambiguity.
//
// It writes one byte into the encrypted blob. That is the same thing an
// insider with server access would do, and it is what the integrity
// check is designed to catch.
// ---------------------------------------------------------------

(async () => {
    if (storage.BACKEND !== "disk") {
        console.error(
            `Storage backend is "${storage.BACKEND}", not disk - there is no local file to edit.`
        );
        process.exit(1);
    }

    const documentId = process.argv[2];

    const { rows } = documentId
        ? await db.query(
            `SELECT v.storage_path, v.version, d.id, d.title, c.case_number
               FROM document_versions v
               JOIN documents d ON d.id = v.document_id
               JOIN cases c ON c.id = d.case_id
              WHERE d.id = $1
              ORDER BY v.version DESC LIMIT 1`,
            [documentId]
        )
        : await db.query(
            `SELECT v.storage_path, v.version, d.id, d.title, c.case_number
               FROM document_versions v
               JOIN documents d ON d.id = v.document_id
               JOIN cases c ON c.id = d.case_id
              ORDER BY v.uploaded_at DESC LIMIT 1`
        );

    if (!rows[0]) {
        console.error("No documents found. Upload one first.");
        process.exit(1);
    }

    const row = rows[0];
    const file = path.join(storage.BLOB_DIR, row.storage_path);

    if (!fs.existsSync(file)) {
        console.error(`The stored file is already missing: ${row.storage_path}`);
        console.error("Verify will report TAMPERED for this document as it stands.");
        process.exit(1);
    }

    // Flip one byte near the start. Enough to break the integrity check,
    // small enough that the file is obviously still "there".
    const fd = fs.openSync(file, "r+");
    const original = Buffer.alloc(1);
    fs.readSync(fd, original, 0, 1, 0);
    fs.writeSync(fd, Buffer.from([original[0] ^ 0xff]), 0, 1, 0);
    fs.closeSync(fd);

    console.log("");
    console.log("  Damaged the stored file for:");
    console.log(`    Case      : ${row.case_number}`);
    console.log(`    Document  : ${row.title}  (version ${row.version})`);
    console.log(`    File      : ${row.storage_path}`);
    console.log("");
    console.log("  Now click Verify on this document. It must say TAMPERED.");
    console.log(`    http://localhost:${process.env.PORT || 5000}/documents/${row.id}/verify`);
    console.log("");
    console.log("  If the case is under investigation, the officers on it are alerted");
    console.log("  at the next 20-minute check - or now, with:  npm run integrity:check");
    console.log("");
    console.log("  To undo: npm run seed  then  npm run seed:scan");
    console.log("");

    await db.pool.end();
})().catch((err) => {
    console.error("could not damage the file:", err.message);
    process.exit(1);
});
