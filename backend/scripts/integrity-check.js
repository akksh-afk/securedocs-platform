// Run the scheduled integrity check once, now.
//
//   npm run integrity:check
//
// The server already runs this every 20 minutes on cases under
// investigation. This is for not waiting: after `npm run tamper`, run it
// and the officers on that case have their alert straight away.
require("dotenv").config({ quiet: true });

const db = require("../db");
const ledger = require("../services/ledger");
const integrity = require("../services/integrity");

(async () => {
    // The stub ledger lives in memory; load its anchors first or every
    // anchored version would look like it had lost its ledger record.
    await ledger.rehydrate();

    const r = await integrity.sweep();
    console.log(
        `${r.checked} version(s) verified, ${r.failing} failing, ${r.alerts} new alert(s)` +
        (r.skipped ? `, ${r.skipped} unreadable` : "")
    );

    await ledger.close();
    await db.pool.end();
})().catch((err) => {
    console.error("integrity check failed:", err.message);
    process.exit(1);
});
