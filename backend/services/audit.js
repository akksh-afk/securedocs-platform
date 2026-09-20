const db = require("../db");
const { sha256 } = require("./crypto");

const GENESIS = "0".repeat(64);

// ---------------------------------------------------------------
// Serialise an object to a string that does not depend on key order.
//
// This matters more than it looks. `detail` is stored as JSONB, and
// JSONB does not preserve the order keys were written in - it returns
// them in its own order. So an entry written as {sha256, title} comes
// back as {title, sha256}, JSON.stringify produces a different string,
// and the entry fails its own hash check on verification.
//
// The effect was a false "hash chain BROKEN" on entries where nothing
// whatsoever was wrong - the worst possible failure for the one number
// that is supposed to prove the log has not been touched. Sorting the
// keys makes the hash depend on the content and nothing else.
// ---------------------------------------------------------------
function canonicalJson(value) {
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";

    return (
        "{" +
        Object.keys(value)
            .sort()
            .map((k) => JSON.stringify(k) + ":" + canonicalJson(value[k]))
            .join(",") +
        "}"
    );
}

// Every entry stores the hash of the entry before it. Alter or remove
// any row and every hash after it stops matching, so tampering with
// the log itself becomes detectable.
//
// The advisory lock means two concurrent requests cannot both read the
// same "last hash" and produce a forked chain.
async function write(client, {
                          userId = null,
                          action,
                          documentId = null,
                          caseId = null,
                          version = null,
                          detail = null,
                          ip = null,
                      }) {
    await client.query("SELECT pg_advisory_xact_lock(4815162342)");

    const prev = await client.query(
        "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    );
    const prevHash = prev.rows[0] ? prev.rows[0].entry_hash : GENESIS;

    const occurredAt = new Date().toISOString();
    const payload = [
        prevHash,
        userId,
        action,
        documentId,
        caseId,
        version,
        occurredAt,
        detail ? canonicalJson(detail) : "",
    ].join("|");

    const entryHash = sha256(payload);

    await client.query(
        `INSERT INTO audit_log
         (user_id, action, document_id, case_id, version,
          detail, ip, occurred_at, prev_hash, entry_hash)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
        [
            userId,
            action,
            documentId,
            caseId,
            version,
            detail,
            ip,
            occurredAt,
            prevHash,
            entryHash,
        ]
    );

    return entryHash;
}

// Pass the caller's client to write the entry inside its transaction.
// That is how a change and its audit entry commit together or not at
// all: evidence can never exist without the record of who filed it.
// Append it as the last statement before COMMIT - the chain lock is
// held until the transaction ends.
//
// Without a client the entry commits on its own, which is right for
// reads, where there is no change to tie it to.
async function append(entry, client = null) {
    if (client) return write(client, entry);

    const own = await db.pool.connect();
    try {
        await own.query("BEGIN");
        const entryHash = await write(own, entry);
        await own.query("COMMIT");
        return entryHash;
    } catch (err) {
        await own.query("ROLLBACK");
        // An audit write failing must never silently succeed, but it also
        // must not take down the request that triggered it.
        console.error("AUDIT WRITE FAILED", err);
        throw err;
    } finally {
        own.release();
    }
}

// Walk the chain and confirm every link still matches.
// This is what backs the chain_intact flag in the API.
//
// ALWAYS the whole log, never a filtered subset. The chain is global:
// an entry's prev_hash is the hash of the entry written before it
// anywhere in the system, which is usually about a different document.
// Selecting one document's rows and checking them as if consecutive
// reports BROKEN the moment two documents are worked on in the same
// session - which is simply normal use. That is a false alarm on the
// one number the whole design rests on, so the filter is gone.
//
// The result is cached briefly. Every case screen and every document
// screen asks for it, and each case edit reloads the screen, so without
// this a busy station rehashes the whole log several times a minute.
// The window is short enough that a break still surfaces promptly.
//
// ponytail: still O(n) per check, just not per request. If the log
// grows into millions, verify forward from a stored known-good
// checkpoint instead of from genesis.
const CACHE_MS = 30 * 1000;
let cached = null;

async function verifyChain({ force = false } = {}) {
    if (!force && cached && Date.now() - cached.at < CACHE_MS) return cached.value;

    // Named columns, not *: the log carries a detail blob per row and
    // this reads every row in the table.
    const { rows } = await db.query(
        `SELECT id, user_id, action, document_id, case_id, version,
                detail, occurred_at, prev_hash, entry_hash
           FROM audit_log ORDER BY id ASC`
    );

    const remember = (value) => {
        cached = { at: Date.now(), value };
        return value;
    };

    let prevHash = GENESIS;
    for (const row of rows) {
        if (row.prev_hash !== prevHash) {
            return remember({ intact: false, brokenAt: row.id });
        }
        const payload = [
            row.prev_hash,
            row.user_id,
            row.action,
            row.document_id,
            row.case_id,
            row.version,
            new Date(row.occurred_at).toISOString(),
            row.detail ? canonicalJson(row.detail) : "",
        ].join("|");

        if (sha256(payload) !== row.entry_hash) {
            return remember({ intact: false, brokenAt: row.id });
        }
        prevHash = row.entry_hash;
    }
    return remember({ intact: true, brokenAt: null });
}

module.exports = { append, verifyChain, canonicalJson, GENESIS };