import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ShieldAlert } from "lucide-react";

import * as api from "../api/client";

// ---------------------------------------------------------------
// Every document this officer can open, across all their cases.
//
// The filter is client-side on purpose: this list is already limited
// to one officer's cases by the server, so it is small, and filtering
// here keeps it instant. If a station ever has enough documents for
// that to stop being true, move the filter into the query rather than
// paging blindly.
// ---------------------------------------------------------------

const TYPES = [
    "fir",
    "statement",
    "forensic_report",
    "charge_sheet",
    "court_filing",
    "notice",
    "other",
];

export default function Documents() {
    const [docs, setDocs] = useState([]);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [type, setType] = useState("");
    const [term, setTerm] = useState("");

    useEffect(() => {
        let cancelled = false;

        api.listDocuments()
            .then((r) => {
                if (!cancelled) setDocs(r.items || []);
            })
            .catch((err) => {
                if (!cancelled) setError(err.message);
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, []);

    const shown = useMemo(() => {
        const needle = term.trim().toLowerCase();
        return docs.filter(
            (d) =>
                (!type || d.doc_type === type) &&
                (!needle ||
                    d.title.toLowerCase().includes(needle) ||
                    d.case_number.toLowerCase().includes(needle))
        );
    }, [docs, type, term]);

    if (loading) return <div className="page-loading">Loading documents...</div>;

    return (
        <div>
            <div className="page-header">
                <h1>Documents</h1>
                <p>
                    {docs.length} document{docs.length === 1 ? "" : "s"} across the cases
                    you are assigned to
                </p>
            </div>

            {error && <div className="alert alert-error">{error}</div>}

            <section className="panel">
                <div className="inline-form">
                    <input
                        value={term}
                        onChange={(e) => setTerm(e.target.value)}
                        placeholder="Filter by title or case number"
                    />
                    <select value={type} onChange={(e) => setType(e.target.value)}>
                        <option value="">All types</option>
                        {TYPES.map((t) => (
                            <option key={t} value={t}>
                                {t.replace(/_/g, " ")}
                            </option>
                        ))}
                    </select>
                </div>

                <p className="muted">
                    This filters titles and case numbers. To search inside the documents
                    themselves, use <Link to="/search">Search</Link>.
                </p>

                {shown.length === 0 ? (
                    <p className="muted">
                        {docs.length === 0
                            ? "No documents in your cases yet."
                            : "Nothing matches that filter."}
                    </p>
                ) : (
                    <table className="data-table">
                        <thead>
                        <tr>
                            <th>Title</th>
                            <th>Case</th>
                            <th>Type</th>
                            <th>Ver</th>
                            <th>Ledger</th>
                            <th>Text</th>
                        </tr>
                        </thead>
                        <tbody>
                        {shown.map((d) => (
                            <tr key={d.id}>
                                <td>
                                    <Link to={`/documents/${d.id}`}>{d.title}</Link>
                                    {d.sensitivity === "protected" && (
                                        <span className="badge badge-protected">
                                            <ShieldAlert size={12} /> Protected
                                        </span>
                                    )}
                                </td>
                                <td>
                                    <Link to={`/cases/${d.case_id}`}>{d.case_number}</Link>
                                </td>
                                <td>{String(d.doc_type).replace(/_/g, " ")}</td>
                                <td>{d.current_version}</td>
                                <td>
                                    <span className={`status status-${d.anchor_status}`}>
                                        {d.anchor_status}
                                    </span>
                                </td>
                                <td>
                                    <span className={`status status-${d.ocr_status}`}>
                                        {d.ocr_status}
                                    </span>
                                </td>
                            </tr>
                        ))}
                        </tbody>
                    </table>
                )}
            </section>
        </div>
    );
}
