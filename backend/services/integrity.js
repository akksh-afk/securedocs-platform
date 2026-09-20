const crypto = require("crypto");

const db = require("../db");
const storage = require("./storage");
const ledger = require("./ledger");
const audit = require("./audit");
const sms = require("./sms");

// ---------------------------------------------------------------
// Recompute the hash of what is actually in storage and compare it to
// both the stored value and the ledger.
//
// One function, three callers: /verify reports the result, the
// certificate refuses to issue without it, and the scheduled sweep
// below alerts on it. All of them must reach the same answer the same
// way.
//
// Storage that does not answer throws rather than reporting tampering.
// An outage that reads as TAMPERED would send every officer on every
// case an alarm about evidence nobody touched.
// ---------------------------------------------------------------
async function checkIntegrity(row) {
    const chainRecord = await ledger.lookup(row.id);

    let storedHash = null;
    let failure = null;

    try {
        const plaintext = await storage.retrieve(row);
        storedHash = crypto.createHash("sha256").update(plaintext).digest("hex");
    } catch (e) {
        if (e.integrity) {
            // Decryption failed its authentication tag: the ciphertext
            // or its key was modified.
            failure = "ciphertext_modified";
        } else if (e.code === "ENOENT" || e.name === "NoSuchKey") {
            failure = "file_missing";
        } else {
            throw e;
        }
    }

    const verified =
        !failure &&
        !!chainRecord &&
        storedHash === row.sha256 &&
        storedHash === chainRecord.sha256;

    return { verified, failure, storedHash, chainRecord };
}

// Why a version counts as tampered, or null if it does not.
//
// Narrower than !verified on purpose: a version whose ledger anchor is
// still pending has no ledger record yet, which fails verification but
// is not tampering. The file itself is still checked against the hash
// recorded at upload.
function tamperReason(row, { failure, storedHash, chainRecord }) {
    if (failure) return failure;
    if (storedHash !== row.sha256) return "hash_mismatch";
    if (row.anchor_status === "anchored" && (!chainRecord || chainRecord.sha256 !== row.sha256)) {
        return "ledger_mismatch";
    }
    return null;
}

const REASON_TEXT = {
    ciphertext_modified: "the stored file was modified",
    file_missing: "the stored file is missing",
    hash_mismatch: "the file no longer matches the fingerprint taken at upload",
    ledger_mismatch: "the recorded fingerprint no longer matches the ledger",
};

// Only a change of state is news: intact -> tampered, or tampered ->
// verifying again. Each one writes a system entry to the case's audit
// trail and a notification to every officer on the case, in one
// transaction. A clean pass that changes nothing just records the time.
//
// Both changes are also texted to every active officer on the case with
// a registered mobile number - after the commit, so a slow or failing
// SMS gateway can neither hold the transaction open nor undo the
// record. The in-app alert is the one that is guaranteed.
//
// The row lock means two sweeps (two server processes) cannot both
// announce the same change - or text it twice.
async function record(row, reason) {
    const failed = Boolean(reason);

    if (failed === Boolean(row.integrity_failed_at)) {
        await db.query(
            "UPDATE document_versions SET integrity_checked_at = now() WHERE id = $1",
            [row.id]
        );
        return false;
    }

    const mobiles = await db.transaction(async (client) => {
        const cur = await client.query(
            "SELECT integrity_failed_at FROM document_versions WHERE id = $1 FOR NO KEY UPDATE",
            [row.id]
        );
        if (Boolean(cur.rows[0].integrity_failed_at) === failed) return null;

        await client.query(
            `UPDATE document_versions
                SET integrity_checked_at = now(),
                    integrity_failed_at  = CASE WHEN $2::boolean THEN now() END
              WHERE id = $1`,
            [row.id, failed]
        );

        await audit.append(
            {
                userId: null,
                action: "verify",
                documentId: row.document_id,
                caseId: row.case_id,
                version: row.version,
                detail: {
                    automated: true,
                    verified: !failed,
                    failure: reason,
                    evidence_number: row.evidence_number,
                },
            },
            client
        );

        const label = `Evidence ${row.evidence_number} v${row.version} in case ${row.case_number}`;
        const message = failed
            ? `${label} failed its automatic integrity check: ${REASON_TEXT[reason]}. ` +
              "Do not rely on it until this has been investigated."
            : `${label} verifies again after an earlier integrity failure.`;

        await client.query(
            `INSERT INTO notifications (user_id, kind, case_id, document_id, version, message)
             SELECT user_id, $2, $1, $3, $4, $5
               FROM case_assignments
              WHERE case_id = $1`,
            [row.case_id, failed ? "tamper" : "integrity_restored", row.document_id, row.version, message]
        );

        const { rows: officers } = await client.query(
            `SELECT u.mobile_number
               FROM case_assignments a
               JOIN users u ON u.id = a.user_id
              WHERE a.case_id = $1 AND u.is_active AND u.mobile_number IS NOT NULL`,
            [row.case_id]
        );
        return officers.map((o) => o.mobile_number);
    });

    if (mobiles === null) return false;

    // Evidence and case numbers only - an SMS leaves our systems, so it
    // carries nothing an officer would not read aloud.
    const subject = `evidence ${row.evidence_number} v${row.version} in case ${row.case_number}`;
    const text = failed
        ? `SecureDocs ALERT: ${subject} failed its integrity check. Do not rely on it. Sign in for details.`
        : `SecureDocs: ${subject} verifies again after an earlier integrity failure. Sign in for details.`;

    // ponytail: one attempt, failures logged. Queue and retry if an
    // undelivered alarm ever needs chasing beyond the in-app alert.
    const results = await Promise.allSettled(mobiles.map((m) => sms.send(m, text)));
    results.forEach((r, i) => {
        if (r.status === "rejected") {
            console.error(`tamper sms to ${sms.maskMobile(mobiles[i])} failed: ${r.reason.message}`);
        }
    });

    return true;
}

// ---------------------------------------------------------------
// Re-verify every version of every document in every case that is
// under investigation.
//
// ponytail: sequential, and each check decrypts the whole file. Fine
// for hundreds of versions every 20 minutes; past that, check a
// rotating slice per run or only versions not checked recently.
// ---------------------------------------------------------------
async function sweep() {
    const { rows } = await db.query(
        `SELECT v.id, v.document_id, v.version, v.sha256, v.storage_path,
                v.wrapped_key, v.iv, v.auth_tag, v.anchor_status, v.integrity_failed_at,
                d.case_id, d.evidence_number, c.case_number
           FROM document_versions v
           JOIN documents d ON d.id = v.document_id
           JOIN cases c ON c.id = d.case_id
          WHERE c.status = 'under_investigation'
          ORDER BY c.case_number, d.evidence_number, v.version`
    );

    const result = { checked: 0, failing: 0, alerts: 0, skipped: 0 };

    for (const row of rows) {
        let reason;
        try {
            reason = tamperReason(row, await checkIntegrity(row));
        } catch (err) {
            // Could not read it this time. Not an alarm - try next run.
            result.skipped++;
            console.error(`integrity check skipped ${row.evidence_number} v${row.version}: ${err.message}`);
            continue;
        }

        result.checked++;
        if (reason) result.failing++;
        if (await record(row, reason)) result.alerts++;
    }

    return result;
}

const SWEEP_MINUTES = 20;

// Runs once now, then every SWEEP_MINUTES. A run that is still going
// when the next is due is left to finish rather than doubled up.
function start() {
    let running = false;

    const run = async () => {
        if (running) return;
        running = true;
        try {
            const r = await sweep();
            console.log(
                `Integrity check: ${r.checked} version(s) verified, ${r.failing} failing, ` +
                `${r.alerts} new alert(s)` + (r.skipped ? `, ${r.skipped} unreadable (retrying)` : "")
            );
        } catch (err) {
            console.error("integrity check failed:", err.message);
        } finally {
            running = false;
        }
    };

    run();
    return setInterval(run, SWEEP_MINUTES * 60 * 1000);
}

module.exports = { checkIntegrity, tamperReason, sweep, start, SWEEP_MINUTES };
