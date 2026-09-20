const db = require("../db");
const audit = require("./audit");

// ---------------------------------------------------------------
// The seam to the fake-identity screening service (screening/, a
// Python FastAPI process). SecureDocs holds the evidence; that service
// decides whether an identity document looks forged.
//
// It is called for one document type - identity_document - and only
// after the upload has committed. A screening service that is slow,
// down, or still loading its models must never fail an officer's
// upload, so nothing here is awaited on the request path: the verdict
// arrives later and lands on the case's audit trail.
//
// Set SCREENING_URL to switch it on. Unset, every upload is recorded
// as 'skipped' and nothing is called.
// ---------------------------------------------------------------

const URL = process.env.SCREENING_URL || null;
const TIMEOUT_MS = Number(process.env.SCREENING_TIMEOUT_MS || 60000);

const SCREENED_TYPE = "identity_document";

// CLEAR is the only verdict that needs nobody's attention. RECAPTURE
// means the image was too poor to judge - which on evidence already
// filed is a finding in itself, not a request to photograph it again.
const FLAGGED = {
    SECONDARY_INSPECTION: "needs an officer to examine it",
    REFER_TO_SUPERVISOR: "serious findings - refer to a supervisor",
    RECAPTURE: "the image was too poor to screen reliably",
};

const isFlagged = (disposition) => disposition !== "CLEAR";

// POST the bytes as multipart, the same shape the service's own console
// uses. Returns its JSON verdict.
async function screen(buffer, { filename, mimeType, officerId }) {
    if (!URL) throw new Error("SCREENING_URL is not set");

    const form = new FormData();
    form.append(
        "document",
        new Blob([buffer], { type: mimeType || "application/octet-stream" }),
        filename || "document"
    );
    if (officerId) form.append("officer_id", officerId);

    const res = await fetch(`${URL.replace(/\/$/, "")}/api/v1/screen`, {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(TIMEOUT_MS),
    });

    if (!res.ok) {
        // Status only. The body describes the document that was sent.
        throw new Error(`screening service responded ${res.status}`);
    }
    return res.json();
}

// What an officer reads on the case trail. Deliberately short: the full
// verdict is kept in screening_result for anyone who wants the detail.
function summarise(result) {
    return {
        disposition: result.disposition,
        risk_score: result.risk_score,
        risk_band: result.risk_band,
        document_type: result.document_type || null,
        findings: Array.isArray(result.findings) ? result.findings.length : null,
    };
}

// ---------------------------------------------------------------
// Screen one version and record what came back: the result on the
// version, an entry on the case's audit trail, and - when the verdict
// is anything but CLEAR - an alert for every officer on the case.
//
// The audit entry and the alert are written in one transaction, like
// every other case event. Errors are swallowed on purpose: this runs
// detached from the request, and a screening failure must not take the
// process down. It is recorded as 'failed' so nobody mistakes an
// unscreened document for a clean one.
// ---------------------------------------------------------------
async function screenVersion({ versionId, documentId, caseId, version, evidenceNumber, caseNumber, buffer, filename, mimeType, officerId }) {
    if (!URL) return null;

    let result;
    try {
        result = await screen(buffer, { filename, mimeType, officerId });
    } catch (err) {
        console.error(`screening failed for ${evidenceNumber} v${version}:`, err.message);
        await db
            .query(
                `UPDATE document_versions
                    SET screening_status = 'failed', screened_at = now()
                  WHERE id = $1`,
                [versionId]
            )
            .catch(() => {});
        return null;
    }

    const summary = summarise(result);

    // The upload path knows the case id but not its number; the alert
    // text needs the number an officer would recognise.
    if (!caseNumber) {
        const { rows } = await db.query("SELECT case_number FROM cases WHERE id = $1", [caseId]);
        caseNumber = rows[0] ? rows[0].case_number : caseId;
    }

    await db.transaction(async (client) => {
        await client.query(
            `UPDATE document_versions
                SET screening_status = 'done', screening_result = $2, screened_at = now()
              WHERE id = $1`,
            [versionId, result]
        );

        await audit.append(
            {
                userId: officerId || null,
                action: "screening",
                documentId,
                caseId,
                version,
                detail: { evidence_number: evidenceNumber, ...summary },
                ip: null,
            },
            client
        );

        if (!isFlagged(summary.disposition)) return;

        const message =
            `Evidence ${evidenceNumber} v${version} in case ${caseNumber} was screened as ` +
            `${summary.disposition.replace(/_/g, " ").toLowerCase()} ` +
            `(risk ${summary.risk_score}): ${FLAGGED[summary.disposition] || "see the result"}.`;

        await client.query(
            `INSERT INTO notifications (user_id, kind, case_id, document_id, version, message)
             SELECT user_id, 'screening_alert', $1, $2, $3, $4
               FROM case_assignments
              WHERE case_id = $1`,
            [caseId, documentId, version, message]
        );
    });

    return summary;
}

// Fire and forget, for the upload path. Marks the version pending first
// so a document waiting on a verdict is distinguishable from one that
// was never screened.
function screenInBackground(job) {
    if (!URL || job.docType !== SCREENED_TYPE) return;

    db.query("UPDATE document_versions SET screening_status = 'pending' WHERE id = $1", [job.versionId])
        .then(() => screenVersion(job))
        .catch((err) => console.error("screening job failed:", err.message));
}

module.exports = {
    screen,
    screenVersion,
    screenInBackground,
    summarise,
    isFlagged,
    enabled: Boolean(URL),
    SCREENED_TYPE,
    FLAGGED,
};
