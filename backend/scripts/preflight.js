require("dotenv").config({ quiet: true });

const db = require("../db");
const storage = require("../services/storage");
const ledger = require("../services/ledger");

// ---------------------------------------------------------------
// Demo preflight.
//
//   npm run preflight
//
// Answers one question: will the demo work if I start it right now.
// Every check says what to do about a failure, because finding out at
// 9am that the OCR worker was never started is not the moment to go
// reading documentation.
//
// FAIL blocks the demo. WARN still runs but changes what you can
// honestly claim while standing in front of people.
// ---------------------------------------------------------------

const results = [];
const ok = (name, detail) => results.push(["OK  ", name, detail, null]);
const warn = (name, detail, fix) => results.push(["WARN", name, detail, fix]);
const fail = (name, detail, fix) => results.push(["FAIL", name, detail, fix]);

(async () => {
    // ---- database ----
    try {
        await db.query("SELECT 1");
        ok("database", "reachable");
    } catch (err) {
        fail("database", err.message, "check DATABASE_URL in backend/.env");
        return;
    }

    // ---- migrations ----
    const enums = await db.query(
        `SELECT t.typname, string_agg(e.enumlabel, ',') AS v
           FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
          WHERE t.typname IN ('audit_action_t','ocr_status_t')
          GROUP BY 1`
    );
    const byName = Object.fromEntries(enums.rows.map((r) => [r.typname, r.v]));

    const missing = [];
    if (!(byName.audit_action_t || "").includes("search")) missing.push("001");
    if (!(byName.ocr_status_t || "").includes("processing")) missing.push("003");

    const cols = await db.query(
        `SELECT column_name FROM information_schema.columns
          WHERE table_name='document_versions' AND column_name='entities'`
    );
    const idTable = await db.query(
        "SELECT to_regclass('public.case_protected_identities') AS t"
    );
    if (cols.rows.length === 0 || !idTable.rows[0].t) missing.push("002");

    if (missing.length) {
        fail("migrations", `missing ${missing.join(", ")}`, "npm run db:setup");
    } else {
        ok("migrations", "001, 002, 003 applied");
    }

    // ---- master key ----
    const key = process.env.MASTER_KEY || "";
    if (key.length !== 64 || !/^[0-9a-f]+$/i.test(key)) {
        fail(
            "MASTER_KEY",
            key.length === 0 ? "not set" : `${key.length} chars, need 64 hex`,
            "openssl rand -hex 32  -> backend/.env"
        );
    } else {
        ok("MASTER_KEY", "64 hex chars");
    }

    // ---- seed data ----
    const users = await db.query("SELECT count(*)::int n FROM users");
    const cases = await db.query(
        "SELECT count(*)::int n, count(*) FILTER (WHERE sensitivity='protected')::int p FROM cases"
    );

    if (users.rows[0].n === 0) {
        fail("seed data", "no users", "node scripts/seed.js");
    } else if (cases.rows[0].p === 0) {
        warn(
            "seed data",
            `${users.rows[0].n} users, no protected case`,
            "the redaction demo needs one"
        );
    } else {
        ok(
            "seed data",
            `${users.rows[0].n} users, ${cases.rows[0].n} cases (${cases.rows[0].p} protected)`
        );
    }

    // ---- an unassigned officer, for the closing beat of the demo ----
    const unassigned = await db.query(
        `SELECT u.service_number FROM users u
          WHERE NOT EXISTS (SELECT 1 FROM case_assignments a WHERE a.user_id = u.id)
          LIMIT 1`
    );
    if (unassigned.rows[0]) {
        ok("unassigned officer", `${unassigned.rows[0].service_number} - for step 8`);
    } else {
        warn(
            "unassigned officer",
            "every user is assigned to a case",
            "step 8 (rank is not access) needs someone with no assignment"
        );
    }

    // ---- OCR queue ----
    const queue = await db.query(
        `SELECT ocr_status, count(*)::int n FROM document_versions GROUP BY 1`
    );
    const q = Object.fromEntries(queue.rows.map((r) => [r.ocr_status, r.n]));
    const waiting = (q.pending || 0) + (q.processing || 0);

    if (waiting > 0) {
        warn(
            "OCR queue",
            `${waiting} document(s) still waiting`,
            "npm run worker  (search finds nothing until this runs)"
        );
    } else if ((q.done || 0) > 0) {
        ok("OCR queue", `${q.done} document(s) recognised, nothing waiting`);
    } else {
        warn("OCR queue", "no documents at all", "node scripts/seed-scan.js");
    }

    // ---- searchable text ----
    const searchable = await db.query(
        `SELECT count(*)::int n FROM document_versions
          WHERE coalesce(extracted_text,'') <> ''`
    );
    if (searchable.rows[0].n === 0) {
        warn("search", "nothing has text yet", "run the worker, then search will return hits");
    } else {
        ok("search", `${searchable.rows[0].n} document(s) have text`);
    }

    // ---- the two claims that must be stated honestly ----
    if (ledger.backend === "fabric") {
        ok("ledger", "fabric - anchoring is independent");
    } else {
        warn(
            "ledger",
            "stub (in memory)",
            "say so out loud, or set LEDGER_BACKEND=fabric (see fabric/README.md)"
        );
    }

    if (process.env.REDACTION_ENABLED === "true") {
        ok("redaction", "enabled - protected exports are redacted");
    } else {
        warn(
            "redaction",
            "disabled - protected cases refuse every export with 503",
            "REDACTION_ENABLED=true in backend/.env once you have seen real output"
        );
    }

    ok("storage", `${storage.BACKEND}${storage.BACKEND === "disk" ? " (local, not WORM)" : ""}`);

    // ---- report ----
    console.log("");
    for (const [status, name, detail, fix] of results) {
        console.log(`  [${status}] ${name.padEnd(20)} ${detail}`);
        if (fix) console.log(`         ${" ".repeat(20)} -> ${fix}`);
    }

    const failures = results.filter((r) => r[0] === "FAIL").length;
    const warnings = results.filter((r) => r[0] === "WARN").length;

    console.log("");
    console.log(
        failures
            ? `${failures} blocker(s) and ${warnings} warning(s). Fix the blockers.`
            : `Ready. ${warnings} warning(s) - each one changes what you can claim, not whether it runs.`
    );

    await db.pool.end();
    process.exit(failures ? 1 : 0);
})().catch((err) => {
    console.error("preflight failed:", err.message);
    process.exit(1);
});
