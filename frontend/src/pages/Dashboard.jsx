import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Folder, FileText, ShieldAlert, ScanLine } from "lucide-react";

import * as api from "../api/client";
import { useAuth } from "../auth-context";

// ---------------------------------------------------------------
// The overview screen.
//
// Every number here is counted from what this officer can actually
// reach - the same assignment-scoped endpoints the rest of the app
// uses. There is deliberately no "documents in the system" figure,
// because no officer has a view of that, and inventing one would
// misrepresent how the system works to the person reading the screen.
// ---------------------------------------------------------------

export default function Dashboard() {
    const { user } = useAuth();

    const [cases, setCases] = useState([]);
    const [docs, setDocs] = useState([]);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;

        (async () => {
            try {
                const [c, d] = await Promise.all([api.listCases(), api.listDocuments()]);
                if (cancelled) return;
                setCases(c.items || []);
                setDocs(d.items || []);
            } catch (err) {
                if (!cancelled) setError(err.message);
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();

        return () => {
            cancelled = true;
        };
    }, []);

    if (loading) return <div className="page-loading">Loading overview...</div>;

    const protectedCases = cases.filter((c) => c.sensitivity === "protected").length;
    const unread = docs.filter(
        (d) => d.ocr_status === "pending" || d.ocr_status === "processing"
    ).length;

    const stats = [
        { icon: Folder, label: "Cases assigned to you", value: cases.length, to: "/cases" },
        { icon: FileText, label: "Documents you can open", value: docs.length, to: "/documents" },
        { icon: ShieldAlert, label: "Protected cases", value: protectedCases, to: "/cases" },
        { icon: ScanLine, label: "Still being read", value: unread, to: "/documents" },
    ];

    return (
        <div>
            <div className="page-header">
                <h1>{user ? `Good day, ${user.name}` : "Overview"}</h1>
                <p>{user && `${user.rank.replace(/_/g, " ")} · ${user.station}`}</p>
            </div>

            {error && <div className="alert alert-error">{error}</div>}

            <div className="stat-row">
                {stats.map(({ icon: Icon, label, value, to }) => (
                    <Link key={label} to={to} className="stat-card">
                        <Icon size={20} />
                        <strong>{value}</strong>
                        <span>{label}</span>
                    </Link>
                ))}
            </div>

            {unread > 0 && (
                <div className="alert alert-warn">
                    {unread} document{unread === 1 ? " is" : "s are"} still being read.
                    Search will not find {unread === 1 ? "it" : "them"} until that
                    finishes - and if the number never falls, the text reader is not
                    running.
                </div>
            )}

            <section className="panel">
                <h2>Recently added</h2>
                {docs.length === 0 ? (
                    <p className="muted">
                        Nothing yet. Open a case to upload the first document.
                    </p>
                ) : (
                    <div className="document-list">
                        {docs.slice(0, 8).map((d) => (
                            <Link key={d.id} to={`/documents/${d.id}`} className="document-item">
                                <FileText size={20} />
                                <div>
                                    <h4>{d.title}</h4>
                                    <p>
                                        {d.case_number} · {String(d.doc_type).replace(/_/g, " ")} ·{" "}
                                        {new Date(d.created_at).toLocaleDateString()}
                                    </p>
                                </div>
                                {d.sensitivity === "protected" && (
                                    <span className="badge badge-protected">
                                        <ShieldAlert size={13} /> Protected
                                    </span>
                                )}
                            </Link>
                        ))}
                    </div>
                )}
            </section>
        </div>
    );
}
