"use strict";

const { Contract } = require("fabric-contract-api");

// ---------------------------------------------------------------
// Anchor chaincode.
//
// What goes on the ledger: a hash, who signed it, when, and what
// happened to it. NEVER the document, and never any personal data.
// If you are ever tempted to add a field here, ask whether you would
// be comfortable with every organisation on the channel reading it
// forever, because that is what you are proposing.
//
// The asset is keyed by versionId. Anchoring creates it; custody
// events update it. Because Fabric keeps every write, GetHistoryForKey
// on that key returns the complete chain of custody.
//
// DETERMINISM: chaincode runs on every endorsing peer and the results
// must match byte for byte. Never use Date.now(), Math.random() or
// crypto randomness in this file - use the transaction timestamp and
// the transaction id, which are fixed by the proposal.
// ---------------------------------------------------------------

const ANCHOR_ACTION = "anchor";

// Events that may be appended to an asset's history after anchoring.
const CUSTODY_ACTIONS = new Set(["transfer", "share", "export"]);

function txTimestamp(ctx) {
    const ts = ctx.stub.getTxTimestamp();

    // fabric-shim hands back a protobuf Timestamp whose seconds field is
    // a Long in some versions and a plain number in others.
    const seconds =
        ts.seconds && typeof ts.seconds.toNumber === "function"
            ? ts.seconds.toNumber()
            : Number(ts.seconds || 0);

    const millis = seconds * 1000 + Math.floor((ts.nanos || 0) / 1e6);
    return new Date(millis).toISOString();
}

class AnchorContract extends Contract {
    async _get(ctx, versionId) {
        const bytes = await ctx.stub.getState(versionId);
        if (!bytes || bytes.length === 0) return null;
        return JSON.parse(bytes.toString());
    }

    /**
     * Record a document version's hash.
     *
     * Rejects a second anchor for the same version. An anchor that could
     * be overwritten would prove nothing - the immutability is the
     * entire product.
     */
    async Anchor(ctx, versionId, documentId, sha256, signedBy, station) {
        if (!versionId || !documentId || !sha256) {
            throw new Error("versionId, documentId and sha256 are required");
        }
        if (!/^[0-9a-f]{64}$/.test(sha256)) {
            throw new Error("sha256 must be 64 lowercase hex characters");
        }

        const existing = await this._get(ctx, versionId);
        if (existing) {
            throw new Error(`version ${versionId} is already anchored`);
        }

        const anchoredAt = txTimestamp(ctx);

        const asset = {
            versionId,
            documentId,
            sha256,
            signedBy: signedBy || "",
            signedAt: anchoredAt,
            station: station || "",
            action: ANCHOR_ACTION,
            txId: ctx.stub.getTxID(),
        };

        await ctx.stub.putState(versionId, Buffer.from(JSON.stringify(asset)));
        ctx.stub.setEvent("Anchored", Buffer.from(JSON.stringify(asset)));

        return JSON.stringify(asset);
    }

    /**
     * Read back what the ledger holds for a version.
     * Returns the empty string when there is nothing, so the caller can
     * tell "not anchored" apart from an error.
     */
    async Lookup(ctx, versionId) {
        const asset = await this._get(ctx, versionId);
        return asset ? JSON.stringify(asset) : "";
    }

    /**
     * Append a custody event to an already-anchored version.
     *
     * The hash is deliberately carried forward untouched. A custody
     * event records that something happened to the document, never that
     * the document changed - a new file is a new version and gets its
     * own anchor.
     */
    async RecordCustody(ctx, versionId, action, actor, station) {
        if (!CUSTODY_ACTIONS.has(action)) {
            throw new Error(
                `action must be one of ${[...CUSTODY_ACTIONS].join(", ")}`
            );
        }

        const asset = await this._get(ctx, versionId);
        if (!asset) {
            throw new Error(`version ${versionId} is not anchored`);
        }

        const updated = {
            ...asset,
            sha256: asset.sha256, // never changes, stated explicitly
            action,
            signedBy: actor || "",
            station: station || asset.station,
            signedAt: txTimestamp(ctx),
            txId: ctx.stub.getTxID(),
        };

        await ctx.stub.putState(versionId, Buffer.from(JSON.stringify(updated)));
        ctx.stub.setEvent("Custody", Buffer.from(JSON.stringify(updated)));

        return JSON.stringify(updated);
    }

    /**
     * The full chain of custody: every write to this key, oldest first.
     * This is the query to run in front of a judge.
     */
    async History(ctx, versionId) {
        const iterator = await ctx.stub.getHistoryForKey(versionId);
        const entries = [];

        let res = await iterator.next();
        while (!res.done) {
            const v = res.value;
            const seconds =
                v.timestamp && typeof v.timestamp.seconds.toNumber === "function"
                    ? v.timestamp.seconds.toNumber()
                    : Number(v.timestamp ? v.timestamp.seconds : 0);

            entries.push({
                txId: v.txId,
                timestamp: new Date(seconds * 1000).toISOString(),
                isDelete: v.isDelete,
                value: v.value && v.value.length ? JSON.parse(v.value.toString()) : null,
            });

            res = await iterator.next();
        }
        await iterator.close();

        return JSON.stringify(entries);
    }
}

module.exports = AnchorContract;
