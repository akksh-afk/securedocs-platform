const express = require("express");

const db = require("../db");
const { requireAuth } = require("../middleware/auth");
const { can } = require("../middleware/policy");

const router = express.Router();

// ---------------------------------------------------------------
// GET /users
//
// The officer directory. It exists because somebody has to be able to
// see who can be put on a case, and that is a real need rather than a
// convenience.
//
// It is gated on case.assign - the ranks that actually assign cases -
// rather than being open to everyone with a login. A directory of every
// officer, their rank and their station is not sensitive in the way a
// case file is, but it is not nothing either, and there is no reason a
// constable needs it.
//
// Returns identity and posting only. No password hash, no MFA secret,
// no session. Those columns are not selected at all rather than being
// selected and stripped, so a later edit cannot leak them by accident.
// ---------------------------------------------------------------
router.get("/", requireAuth, async (req, res, next) => {
    try {
        const decision = await can(req.user, "case.assign", { caseId: null });

        // can() also refuses for want of a case context, which does not
        // apply to a directory - only the rank part matters here.
        if (decision.reason === "rank") {
            return res.status(403).json({
                error: "forbidden",
                message: "Your rank does not permit viewing the officer directory.",
            });
        }

        const { rows } = await db.query(
            `SELECT u.id, u.service_number, u.name, u.rank, u.station, u.is_active,
                    count(a.case_id)::int AS assigned_cases
               FROM users u
               LEFT JOIN case_assignments a ON a.user_id = u.id
              GROUP BY u.id
              ORDER BY u.station, u.service_number`
        );

        return res.json({ items: rows, total: rows.length });
    } catch (err) {
        next(err);
    }
});

module.exports = router;
