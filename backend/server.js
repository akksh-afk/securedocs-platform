require("dotenv").config();

const path = require("path");
const fs = require("fs");

const express = require("express");
const cors = require("cors");

const authRoutes = require("./routes/auth");
const caseRoutes = require("./routes/cases");
const documentRoutes = require("./routes/documents");
const icjsRoutes = require("./routes/icjs");
const userRoutes = require("./routes/users");
const notificationRoutes = require("./routes/notifications");
const ledger = require("./services/ledger");
const integrity = require("./services/integrity");
const sms = require("./services/sms");
const { requireAuth } = require("./middleware/auth");
const { PERMISSIONS } = require("./middleware/policy");

const app = express();

// Locked to the Vite dev server. Before the demo, set CORS_ORIGIN to
// wherever the frontend actually runs. Never leave it wide open.
app.use(
    cors({
        origin: process.env.CORS_ORIGIN || "http://localhost:5173",
        credentials: true,
    })
);

app.use(express.json({ limit: "100kb" }));

// How many reverse proxies sit in front of this process. It decides
// which X-Forwarded-For entry becomes req.ip, which is what the OTP
// rate limits and every audit entry record.
//
// NOT `true`: that trusts the whole header, so any client could claim
// any address and walk straight past the per-IP limits. 0 (the default)
// uses the real socket address - correct when nothing is in front.
// Behind one proxy (Render, nginx) set TRUST_PROXY_HOPS=1.
app.set("trust proxy", Number(process.env.TRUST_PROXY_HOPS || 0));

app.use("/api/v1/auth", authRoutes);
app.use("/api/v1/cases", caseRoutes);
app.use("/api/v1/documents", documentRoutes);
app.use("/api/v1/icjs", icjsRoutes);
app.use("/api/v1/users", userRoutes);
app.use("/api/v1/notifications", notificationRoutes);

// permissions is what the rank allows, for deciding which controls to
// show. It is a hint to the screens, never a decision - every route
// still runs its own check in policy.js.
app.get("/api/v1/me", requireAuth, (req, res) => {
    res.json({ user: { ...req.user, permissions: PERMISSIONS[req.user.rank] || [] } });
});

// ---------------------------------------------------------------
// Serve the built frontend from this same process.
//
// One port, one URL, no second server to start and no CORS to
// configure - the screens and the API share an origin. Run
// `npm run build` in frontend/ and this picks it up automatically. If
// there is no build, the API still runs exactly as before, which is
// what the Vite dev server expects during development.
// ---------------------------------------------------------------
const CLIENT_DIR = path.join(__dirname, "..", "frontend", "dist");
const HAS_CLIENT = fs.existsSync(path.join(CLIENT_DIR, "index.html"));

if (HAS_CLIENT) {
    // Asset filenames carry a content hash, so they are safe to cache
    // hard. index.html must never be cached or a browser keeps serving
    // yesterday's app after an update.
    app.use(
        express.static(CLIENT_DIR, {
            index: false,
            setHeaders: (res, filePath) => {
                if (filePath.endsWith("index.html")) {
                    res.setHeader("Cache-Control", "no-store");
                }
            },
        })
    );
}

// Anything that is not the API and not a real file is a page address -
// hand it the app and let the browser router work out the rest. This is
// what makes a deep link like /documents/<id>/verify survive a refresh.
app.use((req, res, next) => {
    if (!HAS_CLIENT) return next();
    if (req.method !== "GET") return next();
    if (req.path.startsWith("/api/")) return next();
    if (req.path.includes(".")) return next();

    return res.sendFile(path.join(CLIENT_DIR, "index.html"));
});

app.use((req, res) => {
    res.status(404).json({ error: "not_found", message: "Not found." });
});

// Multer's own errors are worth surfacing properly - a silent 500 on
// an oversized upload is a confusing bug to chase.
app.use((err, req, res, next) => {
    if (err && err.code === "LIMIT_FILE_SIZE") {
        return res
            .status(413)
            .json({ error: "file_too_large", message: "Maximum upload size is 25 MB." });
    }

    // Logged in full, never sent. Stack traces tell an attacker about
    // your file layout and dependency versions.
    console.error(err);
    res
        .status(500)
        .json({ error: "internal_error", message: "Something went wrong." });
});

const PORT = process.env.PORT || 5000;

app.listen(PORT, async () => {
    let rehydrated = true;
    const n = await ledger.rehydrate().catch((err) => {
        rehydrated = false;
        console.error("ledger rehydrate failed:", err.message);
        return 0;
    });
    console.log("");
    console.log(`  SecureDocs is running:  http://localhost:${PORT}`);
    console.log("");
    if (!HAS_CLIENT) {
        console.log("  API only - no frontend build found.");
        console.log("  Build it with:  cd frontend && npm run build");
        console.log("");
    }

    // Say which ledger is behind this process. Confusing the in-memory
    // stub for the Fabric network in front of an audience would be a
    // claim nobody could defend.
    console.log(`Ledger backend: ${ledger.backend}`);
    if (ledger.backend === "stub") {
        console.log("  (in-memory - not independent, set LEDGER_BACKEND=fabric)");
    }
    if (n) console.log(`  rehydrated ${n} anchors`);

    // After rehydrate, never before, and never at all if it failed: on
    // the stub ledger an anchor that has not been reloaded looks like a
    // missing ledger record, so sweeping now would alert every officer
    // that every file in every case had been tampered with.
    if (rehydrated) {
        integrity.start();
        console.log(
            `Integrity check: every ${integrity.SWEEP_MINUTES} minutes on cases under investigation`
        );
    } else {
        console.error(
            "Integrity check: NOT started - the ledger could not be loaded, so a sweep " +
            "could not tell tampering from a missing anchor. Restart once the ledger is reachable."
        );
    }

    console.log(`SMS sign-in codes: ${sms.provider || "NOT CONFIGURED"}`);
    if (!sms.provider) {
        console.log(
            "  (set SMS_PROVIDER=console for development, or twilio for real SMS -" +
            " until then no sign-in code can be sent)"
        );
    } else if (sms.provider === "console") {
        console.log(
            process.env.NODE_ENV === "production"
                ? "  (console refuses in production - set SMS_PROVIDER=twilio, or sign-in will not work)"
                : "  (development - codes print in this terminal, set SMS_PROVIDER=twilio for real SMS)"
        );
    }

    if (process.env.REDACTION_ENABLED !== "true") {
        console.log("Redaction: DISABLED - protected cases will refuse to export");
    }

    // On a host that cannot run a second process, the text reader runs
    // inside this one. Off by default: a dedicated process is better
    // whenever there is somewhere to put it.
    if (process.env.RUN_WORKER_INLINE === "true") {
        require("./worker/ocr-worker").start();
        console.log("OCR worker: running inside this process");
    }
});