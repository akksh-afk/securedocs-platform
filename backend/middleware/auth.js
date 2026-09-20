const db = require("../db");
const { hashToken } = require("../services/crypto");

// Answers "who is making this request".
// It does NOT answer "are they allowed to" - that is policy.js.
async function requireAuth(req, res, next) {
    const header = req.get("authorization") || "";
    const token = header.startsWith("Bearer ") ? header.slice(7) : null;

    if (!token) {
        return res
            .status(401)
            .json({ error: "unauthorized", message: "No session token provided." });
    }

    try {
        const { rows } = await db.query(
            `SELECT s.id  AS session_id,
              s.expires_at,
              u.id, u.service_number, u.name, u.rank, u.station, u.is_active
         FROM sessions s
         JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = $1`,
            [hashToken(token)]
        );

        const row = rows[0];

        if (!row) {
            return res
                .status(401)
                .json({ error: "unauthorized", message: "Invalid session." });
        }

        if (new Date(row.expires_at) < new Date()) {
            // Clean up as we go rather than needing a scheduled job.
            await db.query("DELETE FROM sessions WHERE id = $1", [row.session_id]);
            return res
                .status(401)
                .json({ error: "unauthorized", message: "Session expired." });
        }

        if (!row.is_active) {
            return res
                .status(401)
                .json({ error: "unauthorized", message: "Account is disabled." });
        }

        req.user = {
            id: row.id,
            service_number: row.service_number,
            name: row.name,
            rank: row.rank,
            station: row.station,
        };
        req.sessionId = row.session_id;

        next();
    } catch (err) {
        next(err);
    }
}

module.exports = { requireAuth };