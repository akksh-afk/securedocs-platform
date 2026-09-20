const express = require("express");

const db = require("../db");
const audit = require("../services/audit");
const { requireAuth } = require("../middleware/auth");
const { requirePermission } = require("../middleware/policy");

const router = express.Router();

// Mirror the enums in schema.sql. Checked here so a bad value is a 400
// with a reason rather than a Postgres cast error surfacing as a 500.
const SENSITIVITIES = new Set(["normal", "restricted", "protected"]);

// Sensitivity is a one-way ratchet through this order. See the refusal
// in PATCH below for why.
const SENSITIVITY_RANK = { normal: 0, restricted: 1, protected: 2 };
const STATUSES = new Set(["open", "under_investigation", "charge_sheeted", "closed"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const CASE_COLUMNS =
    "id, case_number, title, sensitivity, status, station, created_by, created_at";

const badRequest = (res, message) =>
    res.status(400).json({ error: "bad_request", message });

// ---------------------------------------------------------------
// GET /cases
//
// There is deliberately no "list all cases" endpoint. The join to
// case_assignments is the restriction, so it cannot be forgotten the
// way an optional WHERE clause can.
// ---------------------------------------------------------------
router.get("/", requireAuth, async (req, res, next) => {
    try {
        const page = Math.max(1, parseInt(req.query.page, 10) || 1);
        const perPage = Math.min(100, parseInt(req.query.per_page, 10) || 25);

        const { rows } = await db.query(
            `SELECT c.id, c.case_number, c.title, c.sensitivity, c.status, c.created_at
         FROM cases c
         JOIN case_assignments a ON a.case_id = c.id
        WHERE a.user_id = $1
        ORDER BY c.created_at DESC
        LIMIT $2 OFFSET $3`,
            [req.user.id, perPage, (page - 1) * perPage]
        );

        const total = await db.query(
            "SELECT count(*)::int AS n FROM case_assignments WHERE user_id = $1",
            [req.user.id]
        );

        return res.json({ items: rows, total: total.rows[0].n });
    } catch (err) {
        next(err);
    }
});

// ---------------------------------------------------------------
// POST /cases
//
// The station and the creator come from the session, never the body.
// The creator is assigned in the same transaction - a case nobody is
// on could never be opened again, since there is no "all cases" view.
// ---------------------------------------------------------------
router.post(
    "/",
    requireAuth,
    requirePermission("case.create"),
    async (req, res, next) => {
        const body = req.body || {};
        const caseNumber = String(body.case_number || "").trim();
        const title = String(body.title || "").trim();
        const sensitivity = body.sensitivity || "normal";

        if (!caseNumber || !title) {
            return badRequest(res, "case_number and title are required.");
        }
        if (!SENSITIVITIES.has(sensitivity)) {
            return badRequest(res, `sensitivity must be one of ${[...SENSITIVITIES].join(", ")}.`);
        }

        try {
            const created = await db.transaction(async (client) => {
                const { rows } = await client.query(
                    `INSERT INTO cases (case_number, title, sensitivity, station, created_by)
                     VALUES ($1, $2, $3, $4, $5)
                     RETURNING ${CASE_COLUMNS}`,
                    [caseNumber, title, sensitivity, req.user.station, req.user.id]
                );
                const kase = rows[0];

                await client.query(
                    `INSERT INTO case_assignments (case_id, user_id, assigned_by)
                     VALUES ($1, $2, $2)`,
                    [kase.id, req.user.id]
                );

                await audit.append(
                    {
                        userId: req.user.id,
                        action: "case_create",
                        caseId: kase.id,
                        detail: {
                            case_number: kase.case_number,
                            title: kase.title,
                            sensitivity: kase.sensitivity,
                            status: kase.status,
                            station: kase.station,
                        },
                        ip: req.ip,
                    },
                    client
                );

                return kase;
            });

            return res.status(201).json(created);
        } catch (err) {
            if (err.code === "23505") {
                return res.status(409).json({
                    error: "conflict",
                    message: "A case with that number already exists.",
                });
            }
            next(err);
        }
    }
);

// ---------------------------------------------------------------
// GET /cases/:case_id
// ---------------------------------------------------------------
router.get(
    "/:case_id",
    requireAuth,
    requirePermission("case.view", (req) => req.params.case_id),
    async (req, res, next) => {
        try {
            const { rows } = await db.query(
                `SELECT ${CASE_COLUMNS} FROM cases WHERE id = $1`,
                [req.params.case_id]
            );
            if (!rows[0]) {
                return res.status(404).json({ error: "not_found", message: "Not found." });
            }

            const officers = await db.query(
                `SELECT u.id, u.name, u.service_number, u.rank, u.station, a.assigned_at
                   FROM case_assignments a
                   JOIN users u ON u.id = a.user_id
                  WHERE a.case_id = $1
                  ORDER BY a.assigned_at ASC`,
                [req.params.case_id]
            );

            return res.json({ ...rows[0], officers: officers.rows });
        } catch (err) {
            next(err);
        }
    }
);

// ---------------------------------------------------------------
// PATCH /cases/:case_id        title, status, sensitivity
//
// The audit entry carries every changed field as {from, to}, so the
// trail says what the case was as well as what it became. A request
// that changes nothing writes nothing.
// ---------------------------------------------------------------
router.patch(
    "/:case_id",
    requireAuth,
    requirePermission("case.update", (req) => req.params.case_id),
    async (req, res, next) => {
        const body = req.body || {};
        const wanted = {};

        if (body.title !== undefined) {
            wanted.title = String(body.title).trim();
            if (!wanted.title) return badRequest(res, "title cannot be empty.");
        }
        if (body.status !== undefined) {
            if (!STATUSES.has(body.status)) {
                return badRequest(res, `status must be one of ${[...STATUSES].join(", ")}.`);
            }
            wanted.status = body.status;
        }
        if (body.sensitivity !== undefined) {
            if (!SENSITIVITIES.has(body.sensitivity)) {
                return badRequest(res, `sensitivity must be one of ${[...SENSITIVITIES].join(", ")}.`);
            }
            wanted.sensitivity = body.sensitivity;
        }
        if (Object.keys(wanted).length === 0) {
            return badRequest(res, "Send at least one of title, status, sensitivity.");
        }

        try {
            const updated = await db.transaction(async (client) => {
                const cur = await client.query(
                    `SELECT ${CASE_COLUMNS} FROM cases WHERE id = $1 FOR NO KEY UPDATE`,
                    [req.params.case_id]
                );
                const before = cur.rows[0];

                const changes = {};
                for (const [field, to] of Object.entries(wanted)) {
                    if (before[field] !== to) changes[field] = { from: before[field], to };
                }
                if (Object.keys(changes).length === 0) return { record: before };

                // Sensitivity only ever goes up. Lowering it - protected
                // to normal - switches off the redaction that every
                // export on an S.72 case depends on, which is one API
                // call away from releasing the victim's identity. Raising
                // it is always safe; lowering one needs a deliberate act
                // outside the application, not a dropdown.
                const s = changes.sensitivity;
                if (s && SENSITIVITY_RANK[s.to] < SENSITIVITY_RANK[s.from]) {
                    await audit.append(
                        {
                            userId: req.user.id,
                            action: "access_denied",
                            caseId: req.params.case_id,
                            detail: { attempted: "case.sensitivity_downgrade", changes: { sensitivity: s } },
                            ip: req.ip,
                        },
                        client
                    );
                    return { refused: s };
                }

                const { rows } = await client.query(
                    `UPDATE cases
                        SET title       = $2,
                            status      = $3,
                            sensitivity = $4
                      WHERE id = $1
                  RETURNING ${CASE_COLUMNS}`,
                    [
                        req.params.case_id,
                        wanted.title ?? before.title,
                        wanted.status ?? before.status,
                        wanted.sensitivity ?? before.sensitivity,
                    ]
                );

                await audit.append(
                    {
                        userId: req.user.id,
                        action: "case_update",
                        caseId: req.params.case_id,
                        detail: { changes },
                        ip: req.ip,
                    },
                    client
                );

                return { record: rows[0] };
            });

            if (updated.refused) {
                return res.status(403).json({
                    error: "forbidden",
                    message:
                        `A case cannot be moved from ${updated.refused.from} down to ${updated.refused.to}. ` +
                        "Lowering sensitivity would switch off redaction on evidence already released under it.",
                });
            }

            return res.json(updated.record);
        } catch (err) {
            next(err);
        }
    }
);

// ---------------------------------------------------------------
// POST   /cases/:case_id/assignments            { user_id }
// DELETE /cases/:case_id/assignments/:user_id
//
// case_assignments holds only who is on the case now. Who was on it,
// when, and who put them there or took them off is the audit trail.
// The officer's rank and service number are copied into the entry so
// it still reads correctly after a transfer or promotion.
// ---------------------------------------------------------------
router.post(
    "/:case_id/assignments",
    requireAuth,
    requirePermission("case.assign", (req) => req.params.case_id),
    async (req, res, next) => {
        const userId = (req.body || {}).user_id;
        if (!UUID.test(String(userId || ""))) return badRequest(res, "user_id is required.");

        try {
            const result = await db.transaction(async (client) => {
                const { rows } = await client.query(
                    `SELECT id, name, service_number, rank, station
                       FROM users WHERE id = $1 AND is_active`,
                    [userId]
                );
                const officer = rows[0];
                if (!officer) return [404, { error: "not_found", message: "No active officer with that id." }];

                const ins = await client.query(
                    `INSERT INTO case_assignments (case_id, user_id, assigned_by)
                     VALUES ($1, $2, $3)
                     ON CONFLICT DO NOTHING
                     RETURNING assigned_at`,
                    [req.params.case_id, userId, req.user.id]
                );
                if (!ins.rows[0]) {
                    return [409, { error: "conflict", message: "That officer is already on this case." }];
                }

                await audit.append(
                    {
                        userId: req.user.id,
                        action: "assign",
                        caseId: req.params.case_id,
                        detail: { officer },
                        ip: req.ip,
                    },
                    client
                );

                return [201, { ...officer, assigned_at: ins.rows[0].assigned_at }];
            });

            return res.status(result[0]).json(result[1]);
        } catch (err) {
            next(err);
        }
    }
);

router.delete(
    "/:case_id/assignments/:user_id",
    requireAuth,
    requirePermission("case.assign", (req) => req.params.case_id),
    async (req, res, next) => {
        const userId = req.params.user_id;
        if (!UUID.test(userId)) return badRequest(res, "user_id is not valid.");

        // The caller holds case.assign, so as long as they stay on the
        // case, someone who can assign it always remains.
        if (userId === req.user.id) {
            return badRequest(res, "You cannot remove yourself. Ask another officer who can assign this case.");
        }

        try {
            const removed = await db.transaction(async (client) => {
                const { rows } = await client.query(
                    `DELETE FROM case_assignments a
                      USING users u
                      WHERE a.case_id = $1 AND a.user_id = $2 AND u.id = a.user_id
                  RETURNING u.id, u.name, u.service_number, u.rank, u.station`,
                    [req.params.case_id, userId]
                );
                if (!rows[0]) return null;

                await audit.append(
                    {
                        userId: req.user.id,
                        action: "unassign",
                        caseId: req.params.case_id,
                        detail: { officer: rows[0] },
                        ip: req.ip,
                    },
                    client
                );
                return rows[0];
            });

            if (!removed) {
                return res.status(404).json({ error: "not_found", message: "That officer is not on this case." });
            }
            return res.status(204).end();
        } catch (err) {
            next(err);
        }
    }
);

// ---------------------------------------------------------------
// GET /cases/:case_id/audit
//
// Everything that has happened on this case, oldest first: creation,
// edits, assignments, every document filed, viewed, versioned,
// verified or released, and every refused attempt. Each entry names
// the officer the session belonged to - never a name the client sent.
//
// chain_intact covers the whole log, for the reason given at
// GET /documents/:id/audit: the chain is global, so a case's entries
// are never consecutive and cannot be checked on their own.
//
// ponytail: the full trail in one response. Page it (keyset on id)
// if a single case ever grows past a few thousand events.
// ---------------------------------------------------------------
router.get(
    "/:case_id/audit",
    requireAuth,
    requirePermission("case.view", (req) => req.params.case_id),
    async (req, res, next) => {
        try {
            const { rows } = await db.query(
                `SELECT a.id, a.action, a.document_id, d.evidence_number, a.version,
                        a.detail, a.occurred_at, a.prev_hash, a.entry_hash,
                        a.user_id, u.name, u.service_number, u.rank
                   FROM audit_log a
                   LEFT JOIN documents d ON d.id = a.document_id
                   LEFT JOIN users u ON u.id = a.user_id
                  WHERE a.case_id = $1
                  ORDER BY a.id ASC`,
                [req.params.case_id]
            );

            const chain = await audit.verifyChain();

            return res.json({
                chain_intact: chain.intact,
                broken_at: chain.brokenAt,
                entries: rows,
            });
        } catch (err) {
            next(err);
        }
    }
);

// ---------------------------------------------------------------
// GET /cases/:case_id/documents
// ---------------------------------------------------------------
router.get(
    "/:case_id/documents",
    requireAuth,
    requirePermission("document.view", (req) => req.params.case_id),
    async (req, res, next) => {
        try {
            const docType = req.query.doc_type || null;

            const { rows } = await db.query(
                `SELECT d.id, d.case_id, d.evidence_number, d.title, d.doc_type,
                d.current_version, d.created_at,
                u.name AS created_by_name, u.service_number AS created_by_service_number,
                EXISTS (SELECT 1 FROM document_versions v
                         WHERE v.document_id = d.id AND v.integrity_failed_at IS NOT NULL)
                  AS integrity_failed,
                EXISTS (SELECT 1 FROM document_versions v
                         WHERE v.document_id = d.id
                           AND v.screening_result->>'disposition' NOT IN ('CLEAR'))
                  AS screening_flagged
           FROM documents d
           JOIN users u ON u.id = d.created_by
          WHERE d.case_id = $1
            AND ($2::text IS NULL OR d.doc_type::text = $2)
          ORDER BY d.created_at DESC`,
                [req.params.case_id, docType]
            );

            return res.json(rows);
        } catch (err) {
            next(err);
        }
    }
);

// ---------------------------------------------------------------
// Protected identities - the names, addresses and numbers redaction
// removes from every export on a protected case.
//
// This is how the system knows a name belongs to a victim without a
// model: in an S.72 case the investigating officer already knows, and
// asking them is more reliable than inferring it. Once F4 lands, its
// NER output merges with this list rather than replacing it.
//
// These rows ARE the protected identities. They are never exported,
// never watermarked onto a page, and never written to the audit detail.
// ---------------------------------------------------------------

const IDENTITY_KINDS = new Set(["name", "address", "phone", "relationship"]);

router.get(
    "/:case_id/protected-identities",
    requireAuth,
    requirePermission("case.view", (req) => req.params.case_id),
    async (req, res, next) => {
        try {
            const { rows } = await db.query(
                `SELECT i.id, i.value, i.kind, i.added_at,
                        u.name AS added_by_name, u.service_number
                   FROM case_protected_identities i
                   JOIN users u ON u.id = i.added_by
                  WHERE i.case_id = $1
                  ORDER BY i.added_at ASC`,
                [req.params.case_id]
            );
            return res.json(rows);
        } catch (err) {
            next(err);
        }
    }
);

router.post(
    "/:case_id/protected-identities",
    requireAuth,
    // Deciding who counts as a protected identity is an investigative
    // judgement, so it sits with the ranks that can assign a case.
    requirePermission("case.assign", (req) => req.params.case_id),
    async (req, res, next) => {
        try {
            const { value, kind } = req.body || {};

            if (!value || String(value).trim().length < 2) {
                return res.status(400).json({
                    error: "bad_request",
                    message: "value is required and must be at least 2 characters.",
                });
            }
            if (kind && !IDENTITY_KINDS.has(kind)) {
                return res.status(400).json({
                    error: "bad_request",
                    message: `kind must be one of ${[...IDENTITY_KINDS].join(", ")}.`,
                });
            }

            const added = await db.transaction(async (client) => {
                const { rows } = await client.query(
                    `INSERT INTO case_protected_identities (case_id, value, kind, added_by)
                     VALUES ($1, $2, $3, $4)
                     ON CONFLICT (case_id, value) DO UPDATE SET kind = EXCLUDED.kind
                     RETURNING id, value, kind, added_at`,
                    [req.params.case_id, String(value).trim(), kind || "name", req.user.id]
                );

                // That an identity was registered, and by whom - never
                // the identity itself. The audit trail is shown to
                // everyone on the case and is not redacted.
                await audit.append(
                    {
                        userId: req.user.id,
                        action: "identity_add",
                        caseId: req.params.case_id,
                        detail: { identity_id: rows[0].id, kind: rows[0].kind },
                        ip: req.ip,
                    },
                    client
                );

                return rows[0];
            });

            return res.status(201).json(added);
        } catch (err) {
            next(err);
        }
    }
);

module.exports = router;