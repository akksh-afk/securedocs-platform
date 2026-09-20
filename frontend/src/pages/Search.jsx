import { useState } from "react";
import { Link } from "react-router-dom";
import { Search as SearchIcon, ShieldAlert } from "lucide-react";

import * as api from "../api/client";

// ---------------------------------------------------------------
// Full-text search across the documents this officer can reach.
//
// Two things worth understanding about the results:
//
//   - It searches the recognised text, so a document that has not been
//     read yet cannot match, however obviously relevant it is.
//   - A protected case never shows a preview. The extract would be the
//     victim's own statement, and previews are cut mid-name, so no
//     word-based removal could make one safe.
// ---------------------------------------------------------------

// ts_headline wraps matches in <b> but does not escape the text around
// them, and that text is OCR of a document somebody uploaded. Rendering
// it as HTML would run script out of an evidence file.
function plainSnippet(html) {
    return String(html || "")
        .replace(/<[^>]*>/g, "")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, "&");
}

export default function Search() {
    const [term, setTerm] = useState("");
    const [results, setResults] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);

    async function run(e) {
        e.preventDefault();
        const q = term.trim();
        if (!q) return;

        setBusy(true);
        setError(null);
        try {
            setResults(await api.searchDocuments(q));
        } catch (err) {
            setError(err.message);
            setResults(null);
        } finally {
            setBusy(false);
        }
    }

    return (
        <div>
            <div className="page-header">
                <h1>Search</h1>
                <p>Searches inside documents, not just their titles</p>
            </div>

            <section className="panel">
                <form onSubmit={run} className="inline-form">
                    <input
                        value={term}
                        onChange={(e) => setTerm(e.target.value)}
                        placeholder="A name, an FIR number, a place..."
                        autoFocus
                    />
                    <button type="submit" className="btn" disabled={busy || !term.trim()}>
                        <SearchIcon size={15} /> {busy ? "Searching..." : "Search"}
                    </button>
                </form>

                <p className="muted">
                    Only cases you are assigned to. Every search is recorded in the audit
                    log, because searching is itself a way of learning what exists.
                </p>
            </section>

            {error && <div className="alert alert-error">{error}</div>}

            {results && (
                <section className="panel">
                    <h2>
                        {results.total} result{results.total === 1 ? "" : "s"}
                    </h2>

                    {results.items.length === 0 && (
                        <p className="muted">
                            Nothing matched. Search reads the recognised text, so a
                            document that has not been read yet cannot match - check its
                            status under Documents.
                        </p>
                    )}

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
                                    {r.sensitivity === "protected" ? (
                                        <>
                                            <ShieldAlert size={13} /> Preview withheld -
                                            protected case
                                        </>
                                    ) : (
                                        "No preview"
                                    )}
                                </p>
                            )}
                        </Link>
                    ))}
                </section>
            )}
        </div>
    );
}
