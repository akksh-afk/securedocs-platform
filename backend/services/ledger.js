// ---------------------------------------------------------------
// Ledger facade.
//
// routes/documents.js calls anchor() and lookup() and does not know or
// care which backend answers. That is the seam the whole design rests
// on - swapping the backend must not touch a single call site.
//
//   LEDGER_BACKEND=fabric   Hyperledger Fabric, two orgs, real anchoring
//   LEDGER_BACKEND=stub     in-memory, single process (default)
//
// The default is the stub on purpose. A demo machine with no Docker
// still runs end to end; it just cannot claim the anchoring is
// independent. Say which backend you are on when demonstrating - the
// startup log prints it.
// ---------------------------------------------------------------

const BACKEND = (process.env.LEDGER_BACKEND || "stub").toLowerCase();

const impl =
    BACKEND === "fabric"
        ? require("./ledger-fabric")
        : require("./ledger-stub");

if (BACKEND !== "fabric" && BACKEND !== "stub") {
    console.warn(`Unknown LEDGER_BACKEND "${BACKEND}", falling back to stub.`);
}

module.exports = {
    backend: BACKEND,
    anchor: impl.anchor,
    lookup: impl.lookup,
    recordCustody: impl.recordCustody,
    history: impl.history,
    rehydrate: impl.rehydrate,
    close: impl.close || (() => {}),
};
