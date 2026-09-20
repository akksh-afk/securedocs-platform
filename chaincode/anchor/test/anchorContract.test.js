"use strict";

const assert = require("assert");
const AnchorContract = require("../lib/anchorContract");

// ---------------------------------------------------------------
// Chaincode self-check.
//
//   node test/anchorContract.test.js
//
// Runs against a fake stub, so no Docker and no network. What it is
// actually protecting:
//
//   - anchors are immutable (the whole product)
//   - the hash never changes on a custody event
//   - nothing non-deterministic leaks in, because a Date.now() here
//     would give every endorsing peer a different answer and the
//     transaction would fail to commit with a confusing error
// ---------------------------------------------------------------

const HASH = "a".repeat(64);
const OTHER = "b".repeat(64);

// Fixed timestamp - this is what a real proposal carries.
function fakeCtx(seconds = 1_700_000_000) {
    const state = new Map();
    const history = new Map();
    let txCounter = 0;
    const txId = () => `tx-${txCounter}`;

    return {
        _state: state,
        _history: history,
        stub: {
            getState: async (k) => state.get(k) || Buffer.alloc(0),
            putState: async (k, v) => {
                state.set(k, v);
                if (!history.has(k)) history.set(k, []);
                history.get(k).push({
                    txId: txId(),
                    timestamp: { seconds, nanos: 0 },
                    isDelete: false,
                    value: v,
                });
            },
            getTxTimestamp: () => ({ seconds, nanos: 0 }),
            getTxID: () => {
                txCounter++;
                return txId();
            },
            setEvent: () => {},
            getHistoryForKey: async (k) => {
                const entries = (history.get(k) || []).slice();
                let i = 0;
                return {
                    next: async () =>
                        i < entries.length
                            ? { done: false, value: entries[i++] }
                            : { done: true },
                    close: async () => {},
                };
            },
        },
    };
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("anchor writes the hash and returns a tx id", async () => {
    const c = new AnchorContract();
    const ctx = fakeCtx();

    const asset = JSON.parse(await c.Anchor(ctx, "v1", "d1", HASH, "u1", "Sadar"));

    assert.strictEqual(asset.sha256, HASH);
    assert.strictEqual(asset.documentId, "d1");
    assert.strictEqual(asset.action, "anchor");
    assert.ok(asset.txId);
});

test("anchor is immutable - a second anchor for a version is rejected", async () => {
    const c = new AnchorContract();
    const ctx = fakeCtx();

    await c.Anchor(ctx, "v1", "d1", HASH, "u1", "Sadar");

    await assert.rejects(
        () => c.Anchor(ctx, "v1", "d1", OTHER, "u2", "Sadar"),
        /already anchored/,
        "re-anchoring must be refused - an overwritable anchor proves nothing"
    );

    // And the original hash survived the attempt.
    const asset = JSON.parse(await c.Lookup(ctx, "v1"));
    assert.strictEqual(asset.sha256, HASH);
});

test("anchor rejects anything that is not a sha-256 hex digest", async () => {
    const c = new AnchorContract();
    const ctx = fakeCtx();

    await assert.rejects(() => c.Anchor(ctx, "v1", "d1", "nope", "u1", "S"), /64 lowercase hex/);
    await assert.rejects(() => c.Anchor(ctx, "v1", "d1", HASH.toUpperCase(), "u1", "S"), /64 lowercase hex/);
    await assert.rejects(() => c.Anchor(ctx, "", "d1", HASH, "u1", "S"), /required/);
});

test("lookup of an unknown version is empty, not an error", async () => {
    const c = new AnchorContract();
    assert.strictEqual(await c.Lookup(fakeCtx(), "missing"), "");
});

test("custody event never alters the anchored hash", async () => {
    const c = new AnchorContract();
    const ctx = fakeCtx();

    await c.Anchor(ctx, "v1", "d1", HASH, "u1", "Sadar");
    const after = JSON.parse(await c.RecordCustody(ctx, "v1", "export", "u2", "Court"));

    assert.strictEqual(after.sha256, HASH, "custody must not touch the hash");
    assert.strictEqual(after.action, "export");
    assert.strictEqual(after.signedBy, "u2");
});

test("custody rejects an action outside transfer/share/export", async () => {
    const c = new AnchorContract();
    const ctx = fakeCtx();

    await c.Anchor(ctx, "v1", "d1", HASH, "u1", "S");
    await assert.rejects(() => c.RecordCustody(ctx, "v1", "delete", "u2", "S"), /must be one of/);
});

test("custody on an unanchored version is rejected", async () => {
    const c = new AnchorContract();
    await assert.rejects(
        () => c.RecordCustody(fakeCtx(), "ghost", "export", "u1", "S"),
        /not anchored/
    );
});

test("history returns the anchor and every custody event, oldest first", async () => {
    const c = new AnchorContract();
    const ctx = fakeCtx();

    await c.Anchor(ctx, "v1", "d1", HASH, "u1", "Sadar");
    await c.RecordCustody(ctx, "v1", "share", "u2", "Sadar");
    await c.RecordCustody(ctx, "v1", "export", "u3", "Court");

    const entries = JSON.parse(await c.History(ctx, "v1"));

    assert.strictEqual(entries.length, 3);
    assert.deepStrictEqual(
        entries.map((e) => e.value.action),
        ["anchor", "share", "export"]
    );
    // The hash is constant across the whole chain of custody.
    assert.ok(entries.every((e) => e.value.sha256 === HASH));
});

test("determinism: same proposal timestamp gives byte-identical output", async () => {
    // Two endorsing peers running the same proposal must agree exactly.
    // If this ever fails, something in the contract is reading the wall
    // clock or a random source and endorsement will not match.
    const a = new AnchorContract();
    const b = new AnchorContract();

    const one = await a.Anchor(fakeCtx(1_700_000_000), "v1", "d1", HASH, "u1", "S");
    const two = await b.Anchor(fakeCtx(1_700_000_000), "v1", "d1", HASH, "u1", "S");

    assert.strictEqual(one, two);
});

test("timestamp comes from the proposal, not the wall clock", async () => {
    const c = new AnchorContract();
    const asset = JSON.parse(
        await c.Anchor(fakeCtx(1_700_000_000), "v1", "d1", HASH, "u1", "S")
    );
    assert.strictEqual(asset.signedAt, new Date(1_700_000_000 * 1000).toISOString());
});

(async () => {
    let failed = 0;

    for (const [name, fn] of tests) {
        try {
            await fn();
            console.log(`  ok    ${name}`);
        } catch (err) {
            failed++;
            console.error(`  FAIL  ${name}`);
            console.error(`        ${err.message}`);
        }
    }

    console.log(`\n${tests.length - failed}/${tests.length} passed`);
    process.exit(failed ? 1 : 0);
})();
