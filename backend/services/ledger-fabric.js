const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const db = require("../db");

// ---------------------------------------------------------------
// Hyperledger Fabric ledger.
//
// Two organisations modelled on ICJS pillars - PoliceOrg and CourtOrg.
// The point of the second org is that no single party can rewrite
// history: an anchor needs endorsement from both, so the police cannot
// quietly restate a hash and neither can the court.
//
// The chaincode stores a hash, a signer and a timestamp. Never the
// document. See chaincode/anchor/lib/anchorContract.js.
//
// The gateway SDK is required lazily so that a deployment running on
// LEDGER_BACKEND=stub never needs these packages installed.
// ---------------------------------------------------------------

const cfg = () => ({
    mspId: process.env.FABRIC_MSP_ID || "Org1MSP",
    peerEndpoint: process.env.FABRIC_PEER_ENDPOINT || "localhost:7051",
    peerHostAlias: process.env.FABRIC_PEER_HOST_ALIAS || "peer0.org1.example.com",
    tlsCertPath: process.env.FABRIC_TLS_CERT_PATH,
    certPath: process.env.FABRIC_CERT_PATH,
    keyPath: process.env.FABRIC_KEY_PATH,
    channel: process.env.FABRIC_CHANNEL || "mychannel",
    chaincode: process.env.FABRIC_CHAINCODE || "anchor",
});

let connection = null;

// Some Fabric crypto material directories hold a single randomly named
// key file, so accept either a file or a directory containing one.
function readFirst(target) {
    const stat = fs.statSync(target);
    if (!stat.isDirectory()) return fs.readFileSync(target);

    const entries = fs.readdirSync(target);
    if (!entries.length) throw new Error(`no files in ${target}`);
    return fs.readFileSync(path.join(target, entries[0]));
}

async function connect() {
    if (connection) return connection;

    const { connect: gatewayConnect, signers } = require("@hyperledger/fabric-gateway");
    const grpc = require("@grpc/grpc-js");

    const c = cfg();

    for (const [name, value] of Object.entries({
        FABRIC_TLS_CERT_PATH: c.tlsCertPath,
        FABRIC_CERT_PATH: c.certPath,
        FABRIC_KEY_PATH: c.keyPath,
    })) {
        if (!value) throw new Error(`${name} is not set`);
    }

    const client = new grpc.Client(
        c.peerEndpoint,
        grpc.credentials.createSsl(readFirst(c.tlsCertPath)),
        { "grpc.ssl_target_name_override": c.peerHostAlias }
    );

    const gateway = gatewayConnect({
        client,
        identity: { mspId: c.mspId, credentials: readFirst(c.certPath) },
        signer: signers.newPrivateKeySigner(
            crypto.createPrivateKey(readFirst(c.keyPath))
        ),
        // A slow ledger must never hang a user's upload.
        evaluateOptions: () => ({ deadline: Date.now() + 5000 }),
        endorseOptions: () => ({ deadline: Date.now() + 15000 }),
        submitOptions: () => ({ deadline: Date.now() + 5000 }),
        commitStatusOptions: () => ({ deadline: Date.now() + 60000 }),
    });

    const contract = gateway
        .getNetwork(c.channel)
        .getContract(c.chaincode);

    connection = { gateway, client, contract };
    return connection;
}

function decode(bytes) {
    const text = Buffer.from(bytes).toString("utf8");
    return text ? JSON.parse(text) : null;
}

/**
 * Submit an anchor transaction and record its id.
 *
 * Signature is identical to the stub's on purpose - routes/documents.js
 * calls this without knowing which backend is behind it.
 */
async function anchor({ versionId, documentId, sha256, signedBy, station }) {
    const { contract } = await connect();

    try {
        const result = await contract.submitTransaction(
            "Anchor",
            versionId,
            documentId,
            sha256,
            signedBy || "",
            station || ""
        );

        const asset = decode(result);
        const txId = asset && asset.txId ? asset.txId : null;

        await db.query(
            `UPDATE document_versions
                SET anchor_status = 'anchored',
                    ledger_tx_id  = $1,
                    anchored_at   = now()
              WHERE id = $2`,
            [txId, versionId]
        );

        return txId;
    } catch (err) {
        // Mark it failed rather than leaving it pending forever. A
        // pending anchor looks like a slow network; a failed one is a
        // thing somebody has to go and look at.
        await db
            .query(
                `UPDATE document_versions SET anchor_status = 'failed' WHERE id = $1`,
                [versionId]
            )
            .catch(() => {});
        throw err;
    }
}

/**
 * Read the ledger's record for a version.
 *
 * Deliberately a separate store from the row being checked. Reading the
 * hash out of the same row we are verifying would be asking the suspect
 * to vouch for themselves.
 */
async function lookup(versionId) {
    const { contract } = await connect();

    const asset = decode(await contract.evaluateTransaction("Lookup", versionId));
    if (!asset) return null;

    return {
        txId: asset.txId,
        documentId: asset.documentId,
        sha256: asset.sha256,
        signedBy: asset.signedBy,
        anchoredAt: asset.signedAt,
    };
}

/**
 * Append a custody event to the version's ledger history.
 * action is one of: transfer, share, export.
 */
async function recordCustody({ versionId, action, actor, station }) {
    const { contract } = await connect();

    const asset = decode(
        await contract.submitTransaction(
            "RecordCustody",
            versionId,
            action,
            actor || "",
            station || ""
        )
    );

    return asset ? asset.txId : null;
}

/** Full chain of custody for a version, oldest first. */
async function history(versionId) {
    const { contract } = await connect();
    return decode(await contract.evaluateTransaction("History", versionId)) || [];
}

// Fabric is durable on its own. Nothing to rebuild at startup - this
// exists only so server.js can call it regardless of backend.
async function rehydrate() {
    return 0;
}

function close() {
    if (!connection) return;
    connection.gateway.close();
    connection.client.close();
    connection = null;
}

module.exports = { anchor, lookup, recordCustody, history, rehydrate, close };
