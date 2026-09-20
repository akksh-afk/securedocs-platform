const fs = require("fs/promises");
const path = require("path");
const crypto = require("crypto");

// ---------------------------------------------------------------
// Envelope encryption.
//
// Every document gets its own random AES-256 key (the DEK). That key
// is itself encrypted with the master key and stored alongside the
// metadata. The master key never touches a file.
//
// Two consequences worth knowing:
//   - Deleting the wrapped key makes the blob permanently unreadable.
//     That is crypto-shredding, and it is how you satisfy a DPDP
//     erasure request without altering the ledger.
//   - GCM authenticates as well as encrypts. If anyone edits the blob
//     on disk, decryption fails outright rather than returning
//     garbage.
// ---------------------------------------------------------------

const BLOB_DIR = path.join(__dirname, "..", "uploads");

function masterKey() {
    const hex = process.env.MASTER_KEY;
    if (!hex || hex.length !== 64) {
        throw new Error(
            "MASTER_KEY must be 64 hex characters. Generate one with: openssl rand -hex 32"
        );
    }
    return Buffer.from(hex, "hex");
}

// Wrapped key layout:  [12-byte iv][16-byte tag][32-byte encrypted DEK]
function wrapKey(dek) {
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv("aes-256-gcm", masterKey(), iv);
    const enc = Buffer.concat([cipher.update(dek), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), enc]);
}

function unwrapKey(wrapped) {
    const iv = wrapped.subarray(0, 12);
    const tag = wrapped.subarray(12, 28);
    const enc = wrapped.subarray(28);
    const decipher = crypto.createDecipheriv("aes-256-gcm", masterKey(), iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(enc), decipher.final()]);
}

// ---------------------------------------------------------------
// Object store. Two functions, because disk was only ever touched in
// two places - one write in store(), one read in retrieve().
//
//   STORAGE_BACKEND=disk    local filesystem (default)
//   STORAGE_BACKEND=minio   S3-compatible WORM object store
//
// Disk stays the default so the system runs with nothing else
// installed. Nothing above these two functions changes between them,
// and the ciphertext written is byte-identical either way - the
// encryption happens before we get here.
// ---------------------------------------------------------------

const BACKEND = (process.env.STORAGE_BACKEND || "disk").toLowerCase();
const BUCKET = process.env.MINIO_BUCKET || "securedocs";

let s3 = null;

function client() {
    if (s3) return s3;

    const { S3Client } = require("@aws-sdk/client-s3");

    s3 = new S3Client({
        endpoint: process.env.MINIO_ENDPOINT || "http://localhost:9000",
        region: process.env.MINIO_REGION || "us-east-1",
        // MinIO serves buckets on a path, not a subdomain.
        forcePathStyle: true,
        credentials: {
            accessKeyId: process.env.MINIO_ACCESS_KEY || "minioadmin",
            secretAccessKey: process.env.MINIO_SECRET_KEY || "minioadmin",
        },
    });
    return s3;
}

async function putObject(name, bytes) {
    if (BACKEND !== "minio") {
        await fs.mkdir(BLOB_DIR, { recursive: true });
        return fs.writeFile(path.join(BLOB_DIR, name), bytes);
    }

    const { PutObjectCommand } = require("@aws-sdk/client-s3");
    await client().send(
        new PutObjectCommand({
            Bucket: BUCKET,
            Key: name,
            Body: bytes,
            ContentType: "application/octet-stream",
        })
    );
}

async function getObject(name) {
    if (BACKEND !== "minio") {
        return fs.readFile(path.join(BLOB_DIR, name));
    }

    const { GetObjectCommand } = require("@aws-sdk/client-s3");
    const res = await client().send(
        new GetObjectCommand({ Bucket: BUCKET, Key: name })
    );

    const chunks = [];
    for await (const chunk of res.Body) chunks.push(chunk);
    return Buffer.concat(chunks);
}

/**
 * Encrypt a buffer and write it to the object store.
 * Returns everything the database row needs.
 */
async function store(plaintext) {
    // Hash the ORIGINAL bytes, before encryption. This is what gets
    // anchored and what verification recomputes.
    const sha256 = crypto.createHash("sha256").update(plaintext).digest("hex");

    const dek = crypto.randomBytes(32);
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv("aes-256-gcm", dek, iv);
    const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
    const authTag = cipher.getAuthTag();

    const name = `${crypto.randomUUID()}.enc`;
    await putObject(name, ciphertext);

    return {
        sha256,
        storage_path: name,
        size_bytes: plaintext.length,
        wrapped_key: wrapKey(dek),
        iv,
        auth_tag: authTag,
    };
}

/**
 * Read and decrypt.
 * Throws if the blob has been altered in storage - GCM will not return
 * data that fails its authentication tag.
 */
//
// A decryption failure is marked err.integrity, so a caller can tell
// "the bytes are not what was stored" from "storage did not answer".
// Only the first is evidence of tampering.
async function retrieve(row) {
    const ciphertext = await getObject(row.storage_path);

    // Outside the try: a missing or malformed MASTER_KEY is a
    // configuration fault, and tagging it as an integrity failure would
    // have the scheduled check report every document in the system as
    // tampered with.
    masterKey();

    try {
        const dek = unwrapKey(row.wrapped_key);
        const decipher = crypto.createDecipheriv("aes-256-gcm", dek, row.iv);
        decipher.setAuthTag(row.auth_tag);

        return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
    } catch (err) {
        err.integrity = true;
        throw err;
    }
}

// Destroy the key, not the file. The blob becomes unreadable noise
// while the ledger record stays intact.
function shredPayload() {
    return crypto.randomBytes(60);
}

module.exports = { store, retrieve, shredPayload, BLOB_DIR, BACKEND, BUCKET };