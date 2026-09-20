import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UploadCloud, ShieldAlert } from "lucide-react";

import * as api from "../api/client";

// ---------------------------------------------------------------
// Upload into any case you are assigned to, without going through the
// case first. The case list is the same assignment-scoped one used
// everywhere, so this cannot be used to file into a case you are not
// on - the picker simply will not contain it.
// ---------------------------------------------------------------

const DOC_TYPES = [
    "fir",
    "statement",
    "forensic_report",
    "charge_sheet",
    "court_filing",
    "notice",
    "identity_document",
    "other",
];

export default function Upload() {
    const navigate = useNavigate();

    const [cases, setCases] = useState([]);
    const [caseId, setCaseId] = useState("");
    const [title, setTitle] = useState("");
    const [docType, setDocType] = useState("fir");
    const [file, setFile] = useState(null);

    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [done, setDone] = useState(null);

    useEffect(() => {
        let cancelled = false;
        api.listCases()
            .then((r) => {
                if (cancelled) return;
                setCases(r.items || []);
                if (r.items && r.items.length === 1) setCaseId(r.items[0].id);
            })
            .catch((err) => {
                if (!cancelled) setError(err.message);
            });
        return () => {
            cancelled = true;
        };
    }, []);

    const selected = cases.find((c) => c.id === caseId);

    async function submit(e) {
        e.preventDefault();
        if (!caseId || !file) return;

        setBusy(true);
        setError(null);
        setDone(null);
        try {
            const res = await api.uploadDocument({
                caseId,
                title: title.trim(),
                docType,
                file,
            });
            setDone(res);
            setTitle("");
            setFile(null);
            e.target.reset();
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <div>
            <div className="page-header">
                <h1>Upload a document</h1>
                <p>Its fingerprint is taken before anything is stored</p>
            </div>

            {error && <div className="alert alert-error">{error}</div>}

            {done && (
                <div className="alert alert-ok">
                    Uploaded. The fingerprint is being recorded on the ledger and the
                    text reader will pick it up shortly.{" "}
                    <button
                        type="button"
                        className="link-button"
                        onClick={() => navigate(`/documents/${done.document_id}`)}
                    >
                        Open it
                    </button>
                </div>
            )}

            <section className="panel">
                <form onSubmit={submit} className="upload-form">
                    <div className="input-group">
                        <label>Case</label>
                        <select
                            value={caseId}
                            onChange={(e) => setCaseId(e.target.value)}
                            required
                        >
                            <option value="">Choose a case...</option>
                            {cases.map((c) => (
                                <option key={c.id} value={c.id}>
                                    {c.case_number} - {c.title}
                                </option>
                            ))}
                        </select>
                    </div>

                    {selected && selected.sensitivity === "protected" && (
                        <div className="alert alert-warn">
                            <ShieldAlert size={14} /> This case is protected. Anything
                            released from it has identities removed first - and register
                            the victim on the case rather than relying on automatic
                            detection.
                        </div>
                    )}

                    <div className="input-group">
                        <label>Description (optional)</label>
                        <input
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                            placeholder="The evidence number is assigned automatically"
                        />
                    </div>

                    <div className="input-group">
                        <label>Type</label>
                        <select value={docType} onChange={(e) => setDocType(e.target.value)}>
                            {DOC_TYPES.map((t) => (
                                <option key={t} value={t}>
                                    {t.replace(/_/g, " ")}
                                </option>
                            ))}
                        </select>
                    </div>

                    <div className="input-group">
                        <label>File</label>
                        <input
                            type="file"
                            onChange={(e) => setFile(e.target.files[0] || null)}
                            required
                        />
                        <p className="muted">
                            Up to 25 MB. Images and PDFs are read automatically so their
                            contents become searchable.
                        </p>
                    </div>

                    <button
                        type="submit"
                        className="btn"
                        disabled={busy || !file || !caseId}
                    >
                        <UploadCloud size={15} /> {busy ? "Uploading..." : "Upload"}
                    </button>
                </form>
            </section>
        </div>
    );
}
