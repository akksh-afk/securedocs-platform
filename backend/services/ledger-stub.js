const crypto = require("crypto");
const db = require("../db");

// ---------------------------------------------------------------
// In-memory ledger. NOT a blockchain.
//
// This exists so the system runs on a laptop with no Fabric network.
// It proves nothing to a court: the same process that stores the hash
// also vouches for it. Set LEDGER_BACKEND=fabric for the real thing.
//
// Kept rather than deleted because a demo that dies when Docker is not
// running is worse than a demo that is honest about its backend.
// ---------------------------------------------------------------

const anchored = new Map();

async function anchor({ versionId, documentId, sha256, signedBy }) {
    const txId = "stub-" + crypto.randomBytes(16).toString("hex");

    anchored.set(versionId, {
        txId,
        documentId,
        sha256,
        signedBy,
        anchoredAt: new Date().toISOString(),
    });

    await db.query(
        `UPDATE document_versions
        SET anchor_status = 'anchored',
            ledger_tx_id  = $1,
            anchored_at   = now()
      WHERE id = $2`,
        [txId, versionId]
    );

    return txId;
}

async function lookup(versionId) {
    return anchored.get(versionId) || null;
}

// The stub has no history, so custody events are accepted and dropped.
// The Postgres audit log is the real record on this backend.
async function recordCustody() {
    return null;
}

async function history(versionId) {
    const record = anchored.get(versionId);
    return record ? [{ txId: record.txId, timestamp: record.anchoredAt, value: record }] : [];
}

/**
 * Called on startup so anchors survive a restart during development.
 * Exists only because this backend is in memory.
 */
async function rehydrate() {
    const { rows } = await db.query(
        `SELECT id, document_id, sha256, ledger_tx_id, uploaded_by, anchored_at
       FROM document_versions
      WHERE anchor_status = 'anchored'`
    );
    for (const r of rows) {
        anchored.set(r.id, {
            txId: r.ledger_tx_id,
            documentId: r.document_id,
            sha256: r.sha256,
            signedBy: r.uploaded_by,
            anchoredAt: r.anchored_at,
        });
    }
    return rows.length;
}

module.exports = { anchor, lookup, recordCustody, history, rehydrate };
