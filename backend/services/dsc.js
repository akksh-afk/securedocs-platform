const crypto = require("crypto");

// ---------------------------------------------------------------
// Digital Signature Certificate / eSign.
//
// SEAM. The two functions below are the whole interface. The prototype
// signs with an ECDSA P-256 keypair generated at startup; production
// swaps in a CCA-licensed DSC token or an ASP eSign call and nothing
// above this file changes.
//
// Showing the seam is the point. Do not claim this is a legally valid
// digital signature - it is the correct shape with a throwaway key.
// ---------------------------------------------------------------

const ALGORITHM = "ecdsa-p256-sha256";

// ponytail: keypair is per-process, so signatures do not survive a
// restart and cannot be verified across instances. Load a persisted
// key (or the real DSC token) when that starts to matter.
const { privateKey, publicKey } = crypto.generateKeyPairSync("ec", {
    namedCurve: "prime256v1",
});

const CERTIFICATE_PEM = publicKey.export({ type: "spki", format: "pem" });

/**
 * Sign a hash.
 * Returns the signature, the certificate needed to check it, and the
 * algorithm used - everything a verifier needs and nothing more.
 */
function sign(hash, signerId) {
    const signature = crypto.sign("sha256", Buffer.from(hash, "utf8"), privateKey);

    return {
        signature: signature.toString("base64"),
        certificate: CERTIFICATE_PEM,
        algorithm: ALGORITHM,
        signed_by: signerId || null,
        signed_at: new Date().toISOString(),
    };
}

/**
 * Check a signature against the certificate it was issued with.
 * Returns false rather than throwing - a malformed signature is a
 * failed verification, not a server error.
 */
function verify(hash, signature, certificate) {
    try {
        return crypto.verify(
            "sha256",
            Buffer.from(hash, "utf8"),
            crypto.createPublicKey(certificate),
            Buffer.from(signature, "base64")
        );
    } catch {
        return false;
    }
}

module.exports = { sign, verify, ALGORITHM, CERTIFICATE_PEM };
