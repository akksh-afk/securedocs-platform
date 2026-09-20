require("dotenv").config({ quiet: true });

const fs = require("fs");
const path = require("path");
const db = require("../db");

// ---------------------------------------------------------------
// Create the schema and apply every migration, over the same
// connection the app uses.
//
//   npm run db:setup
//
// Deliberately not psql. psql authenticates as the postgres OS user and
// on Windows it stops and asks for a password, which means a setup step
// that hangs forever with no output - and it hangs inside demo.sh too,
// which is a bad thing to discover in front of an audience. This reads
// DATABASE_URL from .env, which is already known to work.
//
// Safe to re-run. The schema is skipped once the tables exist and every
// migration is written to be idempotent.
// ---------------------------------------------------------------

const MIGRATIONS = path.join(__dirname, "..", "db", "migrations");

(async () => {
    const { rows } = await db.query(
        "SELECT to_regclass('public.document_versions') AS present"
    );

    if (rows[0].present) {
        console.log("schema: already present, skipping schema.sql");
    } else {
        console.log("schema: creating from db/schema.sql");
        await db.query(
            fs.readFileSync(path.join(__dirname, "..", "db", "schema.sql"), "utf8")
        );
        console.log("schema: created");
    }

    for (const file of fs.readdirSync(MIGRATIONS).sort()) {
        if (!file.endsWith(".sql")) continue;
        try {
            await db.query(fs.readFileSync(path.join(MIGRATIONS, file), "utf8"));
            console.log(`migration: ${file} ok`);
        } catch (err) {
            console.error(`migration: ${file} FAILED - ${err.message}`);
            process.exitCode = 1;
        }
    }

    await db.pool.end();
})().catch((err) => {
    console.error("db setup failed:", err.message);
    console.error("Is DATABASE_URL correct in backend/.env?");
    process.exit(1);
});
