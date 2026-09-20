// Creates test users so you can actually log in.
//
//   npm run seed
//
// Safe to run more than once - it clears and rebuilds the test data.
//
// Loads .env itself, like every other script here. It used to require
// `node -r dotenv/config scripts/seed.js`, so running it the obvious way
// failed with a SASL "client password must be a string" error that says
// nothing at all about the actual problem.
require("dotenv").config({ quiet: true });

const db = require("../db");
const { hashPassword, generateMfaSecret, totpUri } = require("../services/crypto");

const PASSWORD = "Test@1234";

// Mobile numbers are deliberately not real phones: 00000 0000x is not
// an Indian mobile range, so pointing SMS_PROVIDER at a live gateway
// can never text a stranger. Sign in by typing the ten digits.
const USERS = [
    { service_number: "DL-INS-1001", name: "R. Sharma",  rank: "inspector",        station: "Connaught Place", mobile: "+910000000001" },
    { service_number: "DL-SI-2002",  name: "P. Verma",   rank: "sub_inspector",    station: "Connaught Place", mobile: "+910000000002" },
    { service_number: "DL-CON-3003", name: "A. Kumar",   rank: "constable",        station: "Connaught Place", mobile: "+910000000003" },
    { service_number: "DL-PRO-4004", name: "S. Iyer",    rank: "prosecutor",       station: "Tis Hazari",      mobile: "+910000000004" },
    { service_number: "DL-FSL-5005", name: "N. Das",     rank: "forensic_analyst", station: "Rohini FSL",      mobile: "+910000000005" },
];

async function main() {
    console.log("Seeding...\n");

    // The audit log points at documents and cases, and it is protected
    // by rules that silently discard DELETE - which is the whole point
    // of it. That protection also makes this reset impossible: deleting
    // a document trips the foreign key from audit_log, and the audit
    // rows cannot be removed to clear the way.
    //
    // So the rule is switched off for exactly as long as it takes to
    // wipe, and switched back on in a finally block so an error cannot
    // leave the log unprotected. This is the one place in the entire
    // project that is allowed to do this, it is a development seeding
    // tool, and it must never be reachable from the running service.
    await db.query("ALTER TABLE audit_log DISABLE RULE audit_log_no_delete");
    try {
        await db.query("DELETE FROM audit_log");

        // Order matters because of foreign keys.
        await db.query("DELETE FROM case_assignments");
        await db.query("DELETE FROM sessions");
        await db.query("DELETE FROM document_versions");
        await db.query("DELETE FROM documents");
        await db.query("DELETE FROM cases");
        await db.query("DELETE FROM users");
    } finally {
        await db.query("ALTER TABLE audit_log ENABLE RULE audit_log_no_delete");
    }

    const created = [];

    for (const u of USERS) {
        const secret = generateMfaSecret();
        const { rows } = await db.query(
            `INSERT INTO users
         (service_number, name, rank, station, password_hash, mfa_secret, mfa_enabled, mobile_number)
       VALUES ($1,$2,$3,$4,$5,$6,TRUE,$7)
       RETURNING id`,
            [
                u.service_number,
                u.name,
                u.rank,
                u.station,
                await hashPassword(PASSWORD),
                secret,
                u.mobile,
            ]
        );
        created.push({ ...u, id: rows[0].id, secret });
    }

    const inspector = created.find((u) => u.rank === "inspector");

    // One ordinary case and one protected case, so you can demonstrate
    // that sensitivity actually changes behaviour.
    const normalCase = await db.query(
        `INSERT INTO cases (case_number, title, sensitivity, station, created_by)
     VALUES ($1,$2,'normal',$3,$4) RETURNING id`,
        ["FIR/0142/2026", "Theft - Connaught Place", "Connaught Place", inspector.id]
    );

    const protectedCase = await db.query(
        `INSERT INTO cases (case_number, title, sensitivity, station, created_by)
     VALUES ($1,$2,'protected',$3,$4) RETURNING id`,
        ["FIR/0198/2026", "Protected case - identity restricted", "Connaught Place", inspector.id]
    );

    // Deliberately NOT assigning the constable to anything, so you have
    // a user who fails the ABAC check even though their rank permits
    // viewing. That is the demo.
    for (const u of created.filter((c) => c.rank !== "constable")) {
        await db.query(
            `INSERT INTO case_assignments (case_id, user_id, assigned_by)
       VALUES ($1,$2,$3)`,
            [normalCase.rows[0].id, u.id, inspector.id]
        );
    }

    await db.query(
        `INSERT INTO case_assignments (case_id, user_id, assigned_by)
     VALUES ($1,$2,$3)`,
        [protectedCase.rows[0].id, inspector.id, inspector.id]
    );

    console.log(`Password for every user:  ${PASSWORD}\n`);
    console.log("Users:");
    for (const u of created) {
        console.log(`  ${u.service_number}  ${u.rank}  mobile ${u.mobile.slice(3)}`);
        console.log(`    MFA: ${totpUri(u.secret, u.service_number)}`);
    }

    console.log("\nCases:");
    console.log(`  FIR/0142/2026  normal     ${normalCase.rows[0].id}`);
    console.log(`  FIR/0198/2026  protected  ${protectedCase.rows[0].id}`);
    console.log("\nDL-CON-3003 is assigned to nothing. Use it to test denial.");

    await db.pool.end();
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});