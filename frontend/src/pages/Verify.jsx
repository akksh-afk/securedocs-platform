import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CheckCircle2, XCircle, RefreshCw } from "lucide-react";

import * as api from "../api/client";

// ---------------------------------------------------------------
// Screen 5. This is the demo.
//
// The verdict is deliberately enormous and deliberately only two
// colours. Someone watching from the back of a room has to be able to
// read it, and there is no third state to explain: either the bytes on
// disk still hash to what the ledger recorded, or they do not.
//
// The two hashes sit side by side underneath, because "trust me, they
// differ" is not a demonstration.
// ---------------------------------------------------------------

const FAILURE_TEXT = {
    ciphertext_modified:
        "The encrypted file on disk was modified. Decryption failed its authentication tag, " +
        "so the contents could not even be read back.",
    file_missing: "The stored file is missing from storage. Nothing is left to compare.",
};

export default function Verify() {
    const { documentId } = useParams();

    const [result, setResult] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);

    async function run() {
        setLoading(true);
        setError(null);
        try {
            setResult(await api.verifyDocument(documentId));
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }

    // Runs on mount and whenever the document changes. The cancelled
    // flag matters: navigating away mid-verify must not write a verdict
    // for the previous document onto the new screen.
    useEffect(() => {
        let cancelled = false;

        (async () => {
            try {
                const res = await api.verifyDocument(documentId);
                if (!cancelled) setResult(res);
            } catch (err) {
                if (!cancelled) setError(err.message);
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [documentId]);

    const verified = result && result.verified;

    return (
        <div>
            <div className="page-header">
                <h1>Integrity verification</h1>
                <p>
                    <Link to={`/documents/${documentId}`}>back to document</Link>
                </p>
            </div>

            {error && <div className="alert alert-error">{error}</div>}

            {loading && <div className="page-loading">Recomputing hash...</div>}

            {!loading && result && (
                <>
                    <div className={`verdict ${verified ? "verdict-ok" : "verdict-bad"}`}>
                        {verified ? <CheckCircle2 size={64} /> : <XCircle size={64} />}
                        <div>
                            <h2>{verified ? "AUTHENTIC" : "TAMPERED"}</h2>
                            <p>
                                {verified
                                    ? "The stored file hashes exactly to the value recorded on the ledger."
                                    : "The stored file does not match the value recorded on the ledger."}
                            </p>
                        </div>
                    </div>

                    {!verified && result.failure && (
                        <div className="alert alert-error">
                            {FAILURE_TEXT[result.failure] || result.failure}
                        </div>
                    )}

                    <section className="panel">
                        <h2>The comparison</h2>

                        <div className="hash-compare">
                            <div className={verified ? "hash-box ok" : "hash-box bad"}>
                                <label>Hash of the file on disk now</label>
                                <code>{result.stored_hash || "could not be read"}</code>
                            </div>

                            <div className="hash-box">
                                <label>Hash recorded on the ledger at upload</label>
                                <code>{result.ledger_hash || result.recorded_hash || "-"}</code>
                            </div>
                        </div>
                    </section>

                    <section className="panel">
                        <h2>Provenance</h2>
                        <dl className="detail-list">
                            <dt>Version</dt>
                            <dd>{result.version}</dd>

                            <dt>Ledger transaction</dt>
                            <dd>
                                <code className="hash">{result.ledger_tx_id || "not anchored"}</code>
                            </dd>

                            <dt>Anchored at</dt>
                            <dd>
                                {result.anchored_at
                                    ? new Date(result.anchored_at).toLocaleString()
                                    : "-"}
                            </dd>

                            <dt>Signed by</dt>
                            <dd>
                                {result.signed_by
                                    ? `${result.signed_by.name} (${result.signed_by.service_number}, ${result.signed_by.rank})`
                                    : "-"}
                            </dd>
                        </dl>
                    </section>

                    <button className="btn btn-secondary" onClick={run}>
                        <RefreshCw size={15} /> Verify again
                    </button>
                </>
            )}
        </div>
    );
}
