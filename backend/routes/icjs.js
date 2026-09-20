const express = require("express");

const db = require("../db");
const icjs = require("../services/icjs");
const { requireAuth } = require("../middleware/auth");
const { requirePermission, caseIdForDocument } = require("../middleware/policy");

const router = express.Router();

// ---------------------------------------------------------------
// ICJS exchange endpoints - all backed by the MOCK adapter.
//
// Every response carries "mock": true. Do not remove that flag to make
// a demo look better; the whole value of this module is being honest
// about where the real boundary is.
// ---------------------------------------------------------------

// ---------------------------------------------------------------
// GET /icjs/fir?fir_number=FIR/0142/2026        FIR reference lookup
//
// A query parameter rather than a path segment on purpose: FIR numbers
// contain slashes, and an encoded %2F in a path is normalised or
// rejected outright by most reverse proxies. Do not "tidy" this into
// /fir/:fir_number - it breaks the moment nginx is in front of it.
//
// Fixture data about an external FIR. Nothing here is scoped to a case
// in our system, so a valid session is the only gate.
// ---------------------------------------------------------------
router.get("/fir", requireAuth, async (req, res, next) => {
    try {
        const firNumber = (req.query.fir_number || "").trim();

        if (!firNumber) {
            return res
                .status(400)
                .json({ error: "bad_request", message: "fir_number is required." });
        }

        const fir = await icjs.fetchFir(firNumber);

        if (!fir) {
            return res
                .status(404)
                .json({ error: "not_found", message: "No such FIR in ICJS." });
        }

        return res.json(fir);
    } catch (err) {
        next(err);
    }
});

// ---------------------------------------------------------------
// POST /icjs/cases/:case_id/link        case linkage
//
// Returns the linkage acknowledgement ICJS would return. Persists
// nothing - there is no correlation id column to write to yet, and
// inventing one before the real connection exists would be worse than
// leaving the seam visible.
// ---------------------------------------------------------------
router.post(
    "/cases/:case_id/link",
    requireAuth,
    requirePermission("case.view", (req) => req.params.case_id),
    async (req, res, next) => {
        try {
            const { fir_number } = req.body || {};

            if (!fir_number) {
                return res
                    .status(400)
                    .json({ error: "bad_request", message: "fir_number is required." });
            }

            const { rows } = await db.query(
                "SELECT case_number FROM cases WHERE id = $1",
                [req.params.case_id]
            );
            if (!rows[0]) {
                return res
                    .status(404)
                    .json({ error: "not_found", message: "Not found." });
            }

            const result = await icjs.linkCase({
                caseNumber: rows[0].case_number,
                firNumber: fir_number,
            });

            return res.status(result.linked ? 200 : 404).json(result);
        } catch (err) {
            next(err);
        }
    }
);

// ---------------------------------------------------------------
// GET /icjs/documents/:document_id/payload
//
// The exact metadata payload we would push to ICJS for this document.
// Read-only and sends nothing, so the wire format can be reviewed by
// someone who knows ICJS without reading transport code.
//
// Assignment-gated like every other document route. The payload names
// a real case, so it must not be readable by someone off the case.
// ---------------------------------------------------------------
router.get(
    "/documents/:document_id/payload",
    requireAuth,
    requirePermission("document.view", caseIdForDocument),
    async (req, res, next) => {
        try {
            const { rows } = await db.query(
                `SELECT d.id, d.title, d.doc_type,
                        c.case_number, c.sensitivity,
                        v.version, v.sha256, v.ledger_tx_id, v.anchored_at
                   FROM documents d
                   JOIN cases c ON c.id = d.case_id
                   JOIN document_versions v
                     ON v.document_id = d.id AND v.version = d.current_version
                  WHERE d.id = $1`,
                [req.params.document_id]
            );

            const row = rows[0];
            if (!row) {
                return res
                    .status(404)
                    .json({ error: "not_found", message: "Not found." });
            }

            return res.json(
                icjs.documentMetadataPayload({
                    document: { id: row.id, title: row.title, doc_type: row.doc_type },
                    caseRecord: {
                        case_number: row.case_number,
                        sensitivity: row.sensitivity,
                    },
                    version: {
                        version: row.version,
                        sha256: row.sha256,
                        ledger_tx_id: row.ledger_tx_id,
                        anchored_at: row.anchored_at,
                    },
                })
            );
        } catch (err) {
            next(err);
        }
    }
);

module.exports = router;
