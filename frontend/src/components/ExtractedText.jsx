import { useEffect, useState, useCallback } from "react";
import { ScanLine, ShieldAlert, RefreshCw } from "lucide-react";

import * as api from "../api/client";

// ---------------------------------------------------------------
// OCR output, the entities pulled out of it, and the queue state while
// it is still being read.
//
// Polls while the worker has not finished, because "pending" is a
// temporary answer and an officer who just uploaded a scan should not
// have to reload to find out it is ready.
// ---------------------------------------------------------------

const POLL_MS = 4000;

const STATUS_TEXT = {
    pending: "Queued for text recognition",
    processing: "Being read now",
    done: "Text recognised",
    failed: "Could not be read",
};

// Evidentiary entities are shown to everyone. Identifying ones are only
// ever present for a non-protected case - the server strips them
// otherwise - but they are labelled so nobody screenshots them by
// accident during a demo.
const GROUPS = [
    { key: "fir_numbers", label: "FIR", identifying: false },
    { key: "sections", label: "Sections", identifying: false },
    { key: "dates", label: "Dates", identifying: false },
    { key: "persons", label: "Persons", identifying: true },
    { key: "addresses", label: "Addresses", identifying: true },
    { key: "phones", label: "Phones", identifying: true },
];

// How sure the reader is, and what that means for the officer reading
// this screen. It is not trivia: everything redaction removes is found
// in this text, so a badly-read page is a page whose names may not have
// been found - and on a protected case that is the thing to know before
// releasing anything.
function confidenceBand(value) {
    if (value === null || value === undefined) return null;
    if (value >= 85) return { label: "high", tone: "ok" };
    if (value >= 60) return { label: "fair", tone: "warn" };
    return { label: "low", tone: "bad" };
}

const SOURCE_TEXT = {
    "text-layer": "read directly from the PDF, not guessed",
    ocr: "recognised from the image",
};

export default function ExtractedText({ documentId, ocrStatus }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = useCallback(
        async (shouldApply = () => true) => {
            try {
                const res = await api.getText(documentId);
                if (shouldApply()) {
                    setData(res);
                    setError(null);
                }
            } catch (err) {
                if (!shouldApply()) return;
                setError(
                    err.status === 503
                        ? "Recognised text is withheld on this case until redaction is available."
                        : err.message
                );
            } finally {
                if (shouldApply()) setLoading(false);
            }
        },
        [documentId]
    );

    useEffect(() => {
        let cancelled = false;
        (async () => {
            await load(() => !cancelled);
        })();
        return () => {
            cancelled = true;
        };
    }, [load]);

    // Keep asking while the worker still has it.
    const status = data ? data.ocr_status : ocrStatus;
    const inFlight = status === "pending" || status === "processing";

    useEffect(() => {
        if (!inFlight) return undefined;

        let cancelled = false;
        const id = setInterval(() => {
            load(() => !cancelled);
        }, POLL_MS);

        return () => {
            cancelled = true;
            clearInterval(id);
        };
    }, [inFlight, load]);

    if (loading) return <div className="page-loading">Loading recognised text...</div>;

    return (
        <section className="panel">
            <h2>
                <ScanLine size={16} /> Recognised text
            </h2>

            <p className="muted">
                <span className={`status status-${status}`}>
                    {STATUS_TEXT[status] || status}
                </span>
                {inFlight && " - this page updates itself"}
            </p>

            {data && data.entities && data.entities.confidence !== undefined &&
                data.entities.confidence !== null && (() => {
                    const value = Math.round(data.entities.confidence);
                    const band = confidenceBand(value);
                    const source = data.entities.source;

                    return (
                        <div className="reading-quality">
                            <span className="entity-label">Reading quality</span>
                            <span className={`chip chip-${band.tone}`}>
                                {value}% - {band.label}
                            </span>
                            {source && (
                                <span className="muted">{SOURCE_TEXT[source] || source}</span>
                            )}
                        </div>
                    );
                })()}

            {data && data.entities && confidenceBand(data.entities.confidence)?.tone === "bad" && (
                <div className="alert alert-warn">
                    <ShieldAlert size={14} /> This page was read poorly, so the details
                    below are probably incomplete. Everything redaction removes is found
                    in this text - on a protected case, check the identities registered
                    on the case before releasing anything.
                </div>
            )}

            {error && <div className="alert alert-error">{error}</div>}

            {data && data.redacted && (
                <div className="alert alert-warn">
                    <ShieldAlert size={14} /> Protected case: identities have been removed
                    from this text and identifying entities are not returned at all.
                </div>
            )}

            {data && data.entities && (
                <div className="entity-groups">
                    {GROUPS.map(({ key, label, identifying }) => {
                        const values = data.entities[key];
                        if (!Array.isArray(values) || values.length === 0) return null;

                        return (
                            <div key={key} className="entity-group">
                                <span className="entity-label">{label}</span>
                                {values.map((v) => (
                                    <span
                                        key={`${key}-${v}`}
                                        className={`chip ${identifying ? "chip-identifying" : ""}`}
                                    >
                                        {v}
                                    </span>
                                ))}
                            </div>
                        );
                    })}
                </div>
            )}

            {data && data.text ? (
                <pre className="ocr-text">{data.text}</pre>
            ) : (
                !error && (
                    <p className="muted">
                        {inFlight
                            ? "Nothing yet - the worker has not finished with this document."
                            : "No text was recognised. Start the worker with: npm run worker"}
                    </p>
                )
            )}

            <button className="btn btn-secondary btn-small" onClick={() => load()}>
                <RefreshCw size={13} /> Refresh
            </button>
        </section>
    );
}
