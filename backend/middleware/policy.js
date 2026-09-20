const db = require("../db");
const audit = require("../services/audit");

// ---------------------------------------------------------------
// This file is the ONLY place that decides whether an action is
// allowed. Do not write permission checks anywhere else. When a judge
// asks how access control works, you point here.
//
// Two independent checks, both must pass:
//   RBAC - does this rank permit this action at all?
//   ABAC - is this person actually assigned to this case?
//
// A DCP who is not on the case gets nothing. Rank is not a bypass.
// ---------------------------------------------------------------

// Explicit lists rather than a rank ladder. A judge is not "senior to"
// a DCP, they are a different role entirely, so ordering them would be
// meaningless.
const PERMISSIONS = {
    constable: ["case.view", "document.view", "document.download"],

    head_constable: ["case.view", "document.view", "document.download"],

    sub_inspector: [
        "case.view",
        "document.view",
        "document.download",
        "document.upload",
        "document.new_version",
        "document.verify",
    ],

    inspector: [
        "case.view",
        "case.create",
        "case.update",
        "case.assign",
        "document.view",
        "document.download",
        "document.upload",
        "document.new_version",
        "document.verify",
    ],

    dcp: [
        "case.view",
        "case.create",
        "case.update",
        "case.assign",
        "document.view",
        "document.download",
        "document.upload",
        "document.new_version",
        "document.verify",
    ],

    // Court-side and lab roles read and verify. They never upload into
    // a police case file.
    prosecutor: [
        "case.view",
        "document.view",
        "document.download",
        "document.verify",
    ],

    judge: ["case.view", "document.view", "document.download", "document.verify"],

    forensic_analyst: [
        "case.view",
        "document.view",
        "document.download",
        "document.upload",
        "document.verify",
    ],
};

// Actions that need nothing beyond a valid rank. Creating a case cannot
// require an assignment to it - the creator is assigned as it is made.
const NO_CASE_REQUIRED = new Set(["case.create"]);

async function isAssigned(userId, caseId) {
    const { rows } = await db.query(
        "SELECT 1 FROM case_assignments WHERE user_id = $1 AND case_id = $2",
        [userId, caseId]
    );
    return rows.length > 0;
}

// Resolve the case a document belongs to, so requirePermission can run
// its assignment check on a route that only knows a document id.
//
// This lives here rather than in a router because more than one router
// needs it and its answer feeds an access decision. A second copy that
// drifts from this one is a hole in the ABAC check.
async function caseIdForDocument(req) {
    const { rows } = await db.query(
        "SELECT case_id FROM documents WHERE id = $1",
        [req.params.document_id]
    );
    return rows[0] ? rows[0].case_id : null;
}

/**
 * The single access decision.
 * Returns { allowed: boolean, reason: string|null }
 */
async function can(user, action, { caseId } = {}) {
    const allowedForRank = PERMISSIONS[user.rank] || [];

    if (!allowedForRank.includes(action)) {
        return { allowed: false, reason: "rank" };
    }

    if (NO_CASE_REQUIRED.has(action)) {
        return { allowed: true, reason: null };
    }

    if (!caseId) {
        return { allowed: false, reason: "no_case_context" };
    }

    if (!(await isAssigned(user.id, caseId))) {
        return { allowed: false, reason: "not_assigned" };
    }

    return { allowed: true, reason: null };
}

/**
 * Express middleware wrapper.
 * getCaseId receives req and returns the case id for this request.
 *
 *   router.get("/:id", requireAuth,
 *     requirePermission("document.view", req => req.caseId), handler)
 */
function requirePermission(action, getCaseId) {
    return async (req, res, next) => {
        try {
            const caseId = getCaseId ? await getCaseId(req) : null;
            const result = await can(req.user, action, { caseId });

            if (result.allowed) return next();

            // Every denial is recorded. A pattern of denials is itself a
            // signal worth having.
            await audit
                .append({
                    userId: req.user.id,
                    action: "access_denied",
                    caseId: caseId || null,
                    detail: { attempted: action, reason: result.reason },
                    ip: req.ip,
                })
                .catch(() => {});

            // Where confirming a protected case exists would itself leak
            // information, deny by pretending it is not there.
            if (result.reason === "not_assigned") {
                return res
                    .status(404)
                    .json({ error: "not_found", message: "Not found." });
            }

            return res.status(403).json({
                error: "forbidden",
                message: "Your rank does not permit this action.",
            });
        } catch (err) {
            next(err);
        }
    };
}

module.exports = { can, requirePermission, caseIdForDocument, PERMISSIONS };