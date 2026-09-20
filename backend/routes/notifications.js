const express = require("express");

const db = require("../db");
const { requireAuth } = require("../middleware/auth");

const router = express.Router();

// ---------------------------------------------------------------
// The caller's own notifications - today, integrity alerts raised by
// the scheduled sweep in services/integrity.js.
//
// Joined through case_assignments like every other case-scoped read:
// an officer taken off a case stops seeing its alerts, rather than
// keeping a list of evidence numbers from a case they are no longer on.
// ---------------------------------------------------------------

router.get("/", requireAuth, async (req, res, next) => {
    try {
        const { rows } = await db.query(
            `SELECT n.id, n.kind, n.case_id, n.document_id, n.version, n.message,
                    n.created_at, n.read_at
               FROM notifications n
               JOIN case_assignments a
                 ON a.case_id = n.case_id AND a.user_id = n.user_id
              WHERE n.user_id = $1
              ORDER BY n.id DESC
              LIMIT 50`,
            [req.user.id]
        );

        // Counted in the database, not from the 50 rows above, or an
        // officer with more than 50 alerts is told they have 50.
        const { rows: count } = await db.query(
            `SELECT count(*)::int AS unread
               FROM notifications n
               JOIN case_assignments a
                 ON a.case_id = n.case_id AND a.user_id = n.user_id
              WHERE n.user_id = $1 AND n.read_at IS NULL`,
            [req.user.id]
        );

        return res.json({ unread: count[0].unread, items: rows });
    } catch (err) {
        next(err);
    }
});

router.post("/:id/read", requireAuth, async (req, res, next) => {
    try {
        // user_id in the WHERE: nobody marks another officer's alert read.
        const { rowCount } = await db.query(
            `UPDATE notifications SET read_at = coalesce(read_at, now())
              WHERE id = $1 AND user_id = $2`,
            [parseInt(req.params.id, 10) || 0, req.user.id]
        );
        return rowCount
            ? res.status(204).end()
            : res.status(404).json({ error: "not_found", message: "Not found." });
    } catch (err) {
        next(err);
    }
});

module.exports = router;
