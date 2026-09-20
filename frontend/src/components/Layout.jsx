import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { Folder, LogOut, Search as SearchIcon, ShieldAlert, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "../auth-context";
import * as api from "../api/client";

// ---------------------------------------------------------------
// The shell every signed-in screen renders inside.
//
// The sidebar is short on purpose. There is no "all documents" or
// "all users" entry, because neither exists for a normal officer -
// everything is reached through a case you are assigned to.
// ---------------------------------------------------------------

// ts_headline wraps matches in <b> but does NOT escape the surrounding
// text, and that text is OCR of a document someone uploaded. Rendering
// it as HTML would run script out of an evidence file. Strip the markup
// and render plain text - the highlight is not worth an XSS.
function plainSnippet(html) {
    return String(html || "")
        .replace(/<[^>]*>/g, "")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, "&");
}

// Unread integrity alerts stay pinned above every screen until the
// officer dismisses them. Polled rather than pushed: the sweep runs
// every 20 minutes, so a minute's delay in showing its result is noise.
const ALERT_POLL_MS = 60 * 1000;

function IntegrityAlerts() {
    const location = useLocation();
    const [alerts, setAlerts] = useState([]);

    // Ids dismissed in this tab. A dismissal and the next poll can be
    // in flight together, and without this the poll's answer - taken
    // before the dismissal landed - puts the banner straight back.
    const dismissed = useRef(new Set());

    useEffect(() => {
        let cancelled = false;
        const load = () =>
            api.listNotifications()
                .then((r) => {
                    if (cancelled) return;
                    setAlerts(
                        r.items.filter((n) => !n.read_at && !dismissed.current.has(n.id))
                    );
                })
                .catch(() => {});

        load();
        const t = setInterval(load, ALERT_POLL_MS);
        return () => {
            cancelled = true;
            clearInterval(t);
        };
    }, [location.pathname]);

    function dismiss(id) {
        dismissed.current.add(id);
        setAlerts((a) => a.filter((n) => n.id !== id));
        api.markNotificationRead(id).catch(() => {});
    }

    return alerts.map((n) => (
        <div
            key={n.id}
            className={`alert ${n.kind === "tamper" ? "alert-error" : "alert-ok"}`}
            role="alert"
        >
            {n.kind === "tamper" ? <ShieldAlert size={15} /> : <ShieldCheck size={15} />}{" "}
            {n.message}{" "}
            <span className="muted">({new Date(n.created_at).toLocaleString()})</span>{" "}
            {n.document_id && (
                <Link to={`/documents/${n.document_id}/verify`} onClick={() => dismiss(n.id)}>
                    Verify it
                </Link>
            )}{" "}
            <button type="button" className="link-button" onClick={() => dismiss(n.id)}>
                Dismiss
            </button>
        </div>
    ));
}

export default function Layout({ children }) {
    const { user, signOut } = useAuth();
    const navigate = useNavigate();

    const [q, setQ] = useState("");
    const [results, setResults] = useState(null);
    const [searching, setSearching] = useState(false);

    async function runSearch(e) {
        e.preventDefault();
        const query = q.trim();
        if (!query) return;

        setSearching(true);
        try {
            setResults(await api.searchDocuments(query));
        } catch {
            setResults({ items: [], total: 0 });
        } finally {
            setSearching(false);
        }
    }

    return (
        <div className="app-layout">
            <aside className="sidebar">
                <div className="logo">
                    <h2>SecureDocs</h2>
                    <p>Secure Legal DMS</p>
                </div>

                <nav className="menu">
                    <NavLink to="/cases" className="menu-item">
                        <Folder size={18} /> My cases
                    </NavLink>
                </nav>

                <div className="sidebar-user">
                    {user && (
                        <>
                            <strong>{user.name}</strong>
                            <span className="muted">
                                {user.rank} · {user.service_number}
                            </span>
                            <span className="muted">{user.station}</span>
                        </>
                    )}
                </div>

                <button
                    className="logout"
                    onClick={async () => {
                        await signOut();
                        navigate("/", { replace: true });
                    }}
                >
                    <LogOut size={18} /> Sign out
                </button>
            </aside>

            <main className="main-content">
                <form className="topbar-search" onSubmit={runSearch}>
                    <SearchIcon size={16} />
                    <input
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        placeholder="Search documents you have access to"
                    />
                    {results && (
                        <button
                            type="button"
                            className="link-button"
                            onClick={() => {
                                setResults(null);
                                setQ("");
                            }}
                        >
                            clear
                        </button>
                    )}
                    {/* An explicit submit button. Without one, pressing
                        Enter in the box does not reliably submit. */}
                    <button type="submit" className="btn btn-small" disabled={!q.trim()}>
                        Search
                    </button>
                </form>

                <IntegrityAlerts />

                {results ? (
                    <section className="panel">
                        <h2>
                            {searching
                                ? "Searching..."
                                : `${results.total} result${results.total === 1 ? "" : "s"}`}
                        </h2>
                        <p className="muted">
                            Only documents in cases you are assigned to. Snippets are
                            withheld on protected cases.
                        </p>

                        {results.items.map((r) => (
                            <Link
                                key={`${r.document_id}-${r.version}`}
                                to={`/documents/${r.document_id}`}
                                className="search-hit"
                            >
                                <div className="timeline-head">
                                    <strong>{r.title}</strong>
                                    <span className="muted">{r.case_number}</span>
                                </div>
                                {r.snippet ? (
                                    <p className="snippet">{plainSnippet(r.snippet)}</p>
                                ) : (
                                    <p className="muted">
                                        {r.sensitivity === "protected"
                                            ? "Snippet withheld - protected case"
                                            : "No preview"}
                                    </p>
                                )}
                            </Link>
                        ))}

                        {!searching && results.items.length === 0 && (
                            <p className="muted">
                                Nothing matched. Search reads OCR text, which is only
                                populated once a document has been processed.
                            </p>
                        )}
                    </section>
                ) : (
                    children
                )}
            </main>
        </div>
    );
}
