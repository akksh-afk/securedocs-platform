import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Download, FileCheck, History, ShieldCheck, Link2 } from "lucide-react";

import * as api from "../api/client";
import ExtractedText from "../components/ExtractedText";

// ---------------------------------------------------------------
// Screen 4: metadata, version history, and the ways out of here -
// download, certificate, verify, audit.
//
// Nothing is ever overwritten, so the version list only grows. An old
// version stays downloadable and verifiable forever.
// ---------------------------------------------------------------

export default function DocumentView() {
    const { documentId } = useParams();

    const [doc, setDoc] = useState(null);
    const [versions, setVersions] = useState([]);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(null);

    useEffect(() => {
        Promise.all([api.getDocument(documentId), api.listVersions(documentId)])
            .then(([d, v]) => {
                setDoc(d);
                setVersions(v || []);
            })
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }, [documentId]);

    async function download() {
        setError(null);
        setNotice(null);
        setBusy("download");
        try {
            const { name, releasedTo } = await api.downloadDocument(
                documentId,
                doc.title
            );
            setNotice(
                `Downloaded ${name}. Watermarked and released to ${releasedTo || "you"}.`
            );
        } catch (err) {
            // 503 on a protected case is the redaction refusal, not a
            // crash. Say what it means.
            if (err.status === 503) {
                setError(
                    "This case requires redaction before release, and this file cannot be " +
                    "redacted safely. Nothing was sent - that is the intended behaviour."
                );
            } else {
                setError(err.message);
            }
        } finally {
            setBusy(null);
        }
    }

    async function certificate() {
        setError(null);
        setNotice(null);
        setBusy("certificate");
        try {
            await api.downloadCertificate(documentId);
            setNotice("Section 63(4) BSA certificate downloaded.");
        } catch (err) {
            if (err.status === 409) {
                setError(
                    "No certificate can be issued: this document does not currently " +
                    "verify. Run Verify to see why."
                );
            } else {
                setError(err.message);
            }
        } finally {
            setBusy(null);
        }
    }

    if (loading) return <div className="page-loading">Loading document...</div>;
    if (!doc) return <div className="alert alert-error">{error || "Not found."}</div>;

    return (
        <div>
            <div className="page-header">
                <h1>
                    {doc.case_number} / {doc.evidence_number}
                </h1>
                <p>
                    {doc.title !== doc.evidence_number && <>{doc.title} · </>}
                    {String(doc.doc_type).replace(/_/g, " ")} · version{" "}
                    {doc.current_version} · filed by {doc.created_by_name} ·{" "}
                    <Link to={`/cases/${doc.case_id}`}>back to case</Link>
                </p>
            </div>

            {error && <div className="alert alert-error">{error}</div>}
            {notice && <div className="alert alert-ok">{notice}</div>}

            <div className="action-row">
                <button className="btn" onClick={download} disabled={busy === "download"}>
                    <Download size={15} />
                    {busy === "download" ? "Preparing..." : "Download"}
                </button>

                <button
                    className="btn btn-secondary"
                    onClick={certificate}
                    disabled={busy === "certificate"}
                >
                    <FileCheck size={15} />
                    {busy === "certificate" ? "Generating..." : "S.63 certificate"}
                </button>

                <Link className="btn btn-primary" to={`/documents/${documentId}/verify`}>
                    <ShieldCheck size={15} /> Verify
                </Link>

                <Link className="btn btn-secondary" to={`/documents/${documentId}/audit`}>
                    <History size={15} /> Audit trail
                </Link>
            </div>

            <ExtractedText
                documentId={documentId}
                ocrStatus={
                    versions.length
                        ? versions[versions.length - 1].ocr_status
                        : "pending"
                }
            />

            <section className="panel">
                <h2>Version history</h2>
                <p className="muted">
                    Nothing is ever overwritten. Every upload adds a version and keeps
                    the last one.
                </p>

                <table className="data-table">
                    <thead>
                    <tr>
                        <th>Ver</th>
                        <th>SHA-256</th>
                        <th>Uploaded by</th>
                        <th>When</th>
                        <th>Ledger</th>
                        <th>OCR</th>
                        <th>Auto check</th>
                        <th>Screening</th>
                    </tr>
                    </thead>
                    <tbody>
                    {versions.map((v) => (
                        <tr key={v.id}>
                            <td>{v.version}</td>
                            <td>
                                <code className="hash" title={v.sha256}>
                                    {v.sha256.slice(0, 16)}...
                                </code>
                            </td>
                            <td>
                                {v.name}
                                <span className="muted"> ({v.service_number})</span>
                            </td>
                            <td>{new Date(v.uploaded_at).toLocaleString()}</td>
                            <td>
                  <span className={`status status-${v.anchor_status}`}>
                    {v.anchor_status}
                  </span>
                                {v.ledger_tx_id && (
                                    <code className="hash" title={v.ledger_tx_id}>
                                        {" "}
                                        <Link2 size={11} /> {v.ledger_tx_id.slice(0, 10)}...
                                    </code>
                                )}
                            </td>
                            <td>
                                <span className={`status status-${v.ocr_status}`}>
                                    {v.ocr_status}
                                </span>
                            </td>
                            <td>
                                {v.integrity_failed_at ? (
                                    <span className="status status-failed">
                                        failed {new Date(v.integrity_failed_at).toLocaleString()}
                                    </span>
                                ) : v.integrity_checked_at ? (
                                    <span className="muted">
                                        ok {new Date(v.integrity_checked_at).toLocaleString()}
                                    </span>
                                ) : (
                                    <span className="muted">not yet</span>
                                )}
                            </td>
                            <td>
                                {v.screening_status === "skipped" ? (
                                    <span className="muted">-</span>
                                ) : v.screening_disposition ? (
                                    <span
                                        className={
                                            v.screening_disposition === "CLEAR"
                                                ? "muted"
                                                : "status status-failed"
                                        }
                                    >
                                        {v.screening_disposition.replace(/_/g, " ").toLowerCase()}
                                    </span>
                                ) : (
                                    <span className="muted">{v.screening_status}</span>
                                )}
                            </td>
                        </tr>
                    ))}
                    </tbody>
                </table>
            </section>
        </div>
    );
}
