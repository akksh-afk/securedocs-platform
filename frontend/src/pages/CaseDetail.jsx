import { useEffect, useState, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import {
    FileText,
    Upload as UploadIcon,
    ShieldAlert,
    Plus,
    Users,
    History,
    X,
} from "lucide-react";

import * as api from "../api/client";
import { useAuth } from "../auth-context";
import { ChainBanner, Timeline } from "./AuditTimeline";

// ---------------------------------------------------------------
// Screen 3: one case, its evidence, its officers, and everything that
// has happened on it.
//
// On a protected case this also shows the identities redaction will
// strip from every export. That list is only visible to officers on
// the case, and it is never included in an export.
//
// Which controls appear follows user.permissions from /me. That only
// decides what is shown - the server checks every action again.
// ---------------------------------------------------------------

const STATUSES = ["open", "under_investigation", "charge_sheeted", "closed"];

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

export default function CaseDetail() {
    const { caseId } = useParams();
    const { user } = useAuth();
    const can = (p) => Boolean(user && user.permissions && user.permissions.includes(p));

    const [kase, setKase] = useState(null);
    const [documents, setDocuments] = useState([]);
    const [identities, setIdentities] = useState([]);
    const [audit, setAudit] = useState(null);
    const [directory, setDirectory] = useState([]);
    const [officerToAdd, setOfficerToAdd] = useState("");
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);

    const [title, setTitle] = useState("");
    const [docType, setDocType] = useState("fir");
    const [file, setFile] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [notice, setNotice] = useState(null);

    const [newIdentity, setNewIdentity] = useState("");

    // shouldApply lets the caller abandon a response it no longer wants:
    // navigating to another case mid-load must not paint the old one's
    // documents over the new screen.
    const load = useCallback(
        async (shouldApply = () => true) => {
            try {
                // The trail is loaded alongside the case but its failure
                // is its own: a slow chain check must not cost the
                // officer the evidence list and the upload form too.
                const [c, docs, trail] = await Promise.all([
                    api.getCase(caseId),
                    api.listCaseDocuments(caseId),
                    api.getCaseAudit(caseId).catch((err) => ({ error: err.message })),
                ]);
                if (!shouldApply()) return;

                setKase(c);
                setDocuments(docs || []);
                setAudit(trail);

                if (c.sensitivity === "protected") {
                    // Only inspectors and above can add to this list, but
                    // anyone on the case can see what is being protected.
                    const ids = await api
                        .listProtectedIdentities(caseId)
                        .catch(() => []);
                    if (shouldApply()) setIdentities(ids);
                }
            } catch (err) {
                if (shouldApply()) setError(err.message);
            } finally {
                if (shouldApply()) setLoading(false);
            }
        },
        [caseId]
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

    // The officer directory is only readable by ranks that assign.
    const canAssign = can("case.assign");
    useEffect(() => {
        if (!canAssign) return;
        api.listUsers()
            .then((r) => setDirectory(r.items || []))
            .catch(() => {});
    }, [canAssign]);

    // Every change reloads the whole screen, audit trail included, so
    // what was just done is on the record in front of the officer.
    async function act(fn, message) {
        setError(null);
        setNotice(null);
        try {
            const result = await fn();
            if (message) setNotice(typeof message === "function" ? message(result) : message);
            await load();
        } catch (err) {
            setError(err.message);
        }
    }

    async function submitUpload(e) {
        e.preventDefault();
        if (!file) return;

        const form = e.target;
        setUploading(true);
        await act(
            () => api.uploadDocument({ caseId, title: title.trim(), docType, file }),
            (res) => {
                setTitle("");
                setFile(null);
                form.reset();
                return `Filed as ${res.evidence_number}. The hash is being anchored to the ledger.`;
            }
        );
        setUploading(false);
    }

    async function addIdentity(e) {
        e.preventDefault();
        const value = newIdentity.trim();
        if (value.length < 2) return;

        await act(async () => {
            await api.addProtectedIdentity(caseId, value);
            setNewIdentity("");
        });
    }

    if (loading) return <div className="page-loading">Loading case...</div>;
    if (!kase) return <div className="alert alert-error">{error || "Not found."}</div>;

    const onCase = new Set((kase.officers || []).map((o) => o.id));
    const assignable = directory.filter((o) => o.is_active && !onCase.has(o.id));

    return (
        <div>
            <div className="page-header">
                <div className="case-card-head">
                    <h1>{kase.case_number}</h1>
                    {kase.sensitivity === "protected" && (
                        <span className="badge badge-protected">
                            <ShieldAlert size={13} /> Protected
                        </span>
                    )}
                </div>
                <p>{kase.title}</p>
                {can("case.update") ? (
                    <select
                        value={kase.status}
                        onChange={(e) =>
                            act(() => api.updateCase(caseId, { status: e.target.value }))
                        }
                    >
                        {STATUSES.map((s) => (
                            <option key={s} value={s}>
                                {s.replace(/_/g, " ")}
                            </option>
                        ))}
                    </select>
                ) : (
                    <span className={`status status-${kase.status}`}>
                        {String(kase.status).replace(/_/g, " ")}
                    </span>
                )}
            </div>

            {error && <div className="alert alert-error">{error}</div>}
            {notice && <div className="alert alert-ok">{notice}</div>}

            <section className="panel">
                <h2>
                    <Users size={16} /> Officers on this case
                </h2>
                <ul className="identity-list">
                    {(kase.officers || []).map((o) => (
                        <li key={o.id}>
                            <span>
                                {o.name} ({o.service_number}, {o.rank.replace(/_/g, " ")})
                            </span>
                            {canAssign && o.id !== user.id && (
                                <button
                                    type="button"
                                    className="link-button"
                                    title="Remove from case"
                                    onClick={() =>
                                        act(
                                            () => api.unassignOfficer(caseId, o.id),
                                            `${o.name} removed from the case.`
                                        )
                                    }
                                >
                                    <X size={14} /> Remove
                                </button>
                            )}
                        </li>
                    ))}
                </ul>

                {canAssign && (
                    <form
                        className="inline-form"
                        onSubmit={(e) => {
                            e.preventDefault();
                            if (!officerToAdd) return;
                            act(
                                () => api.assignOfficer(caseId, officerToAdd),
                                "Officer assigned."
                            ).then(() => setOfficerToAdd(""));
                        }}
                    >
                        <select
                            value={officerToAdd}
                            onChange={(e) => setOfficerToAdd(e.target.value)}
                        >
                            <option value="">Assign an officer...</option>
                            {assignable.map((o) => (
                                <option key={o.id} value={o.id}>
                                    {o.name} - {o.service_number}, {o.rank.replace(/_/g, " ")},{" "}
                                    {o.station}
                                </option>
                            ))}
                        </select>
                        <button type="submit" className="btn btn-small" disabled={!officerToAdd}>
                            <Plus size={14} /> Assign
                        </button>
                    </form>
                )}
            </section>

            {kase.sensitivity === "protected" && (
                <section className="panel panel-warn">
                    <h2>
                        <ShieldAlert size={16} /> Protected identities
                    </h2>
                    <p className="muted">
                        Removed from every export of this case under BNS S.72. This
                        list is never exported.
                    </p>

                    <ul className="identity-list">
                        {identities.map((i) => (
                            <li key={i.id}>
                                <span>{i.value}</span>
                                <span className="muted">{i.kind}</span>
                            </li>
                        ))}
                        {identities.length === 0 && (
                            <li className="muted">
                                Nothing registered yet - exports will only have
                                pattern-matched data removed.
                            </li>
                        )}
                    </ul>

                    <form onSubmit={addIdentity} className="inline-form">
                        <input
                            value={newIdentity}
                            onChange={(e) => setNewIdentity(e.target.value)}
                            placeholder="Name, address or number to protect"
                        />
                        <button type="submit" className="btn btn-small">
                            <Plus size={14} /> Add
                        </button>
                    </form>
                </section>
            )}

            {can("document.upload") && (
                <section className="panel">
                    <h2>
                        <UploadIcon size={16} /> File evidence
                    </h2>

                    <form onSubmit={submitUpload} className="upload-form">
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
                            <label>Description (optional)</label>
                            <input
                                value={title}
                                onChange={(e) => setTitle(e.target.value)}
                                placeholder="The evidence number is assigned automatically"
                            />
                        </div>

                        <div className="input-group">
                            <label>File</label>
                            <input
                                type="file"
                                onChange={(e) => setFile(e.target.files[0] || null)}
                                required
                            />
                        </div>

                        <button type="submit" className="btn" disabled={uploading || !file}>
                            {uploading ? "Uploading..." : "File evidence"}
                        </button>
                    </form>
                </section>
            )}

            <section className="panel">
                <h2>Evidence</h2>

                {documents.length === 0 ? (
                    <p className="muted">No evidence filed in this case yet.</p>
                ) : (
                    <div className="document-list">
                        {documents.map((d) => (
                            <Link
                                key={d.id}
                                to={`/documents/${d.id}`}
                                className="document-item"
                            >
                                <FileText size={20} />
                                <div>
                                    <h4>
                                        {d.evidence_number}
                                        {d.title !== d.evidence_number && ` - ${d.title}`}
                                        {d.integrity_failed && (
                                            <span className="badge badge-protected">
                                                <ShieldAlert size={13} /> Integrity check failed
                                            </span>
                                        )}
                                        {d.screening_flagged && (
                                            <span className="badge badge-restricted">
                                                <ShieldAlert size={13} /> Screening flagged
                                            </span>
                                        )}
                                    </h4>
                                    <p>
                                        {String(d.doc_type).replace(/_/g, " ")} · v
                                        {d.current_version} · filed by {d.created_by_name} ·{" "}
                                        {new Date(d.created_at).toLocaleDateString()}
                                    </p>
                                </div>
                            </Link>
                        ))}
                    </div>
                )}
            </section>

            {audit && (
                <section className="panel">
                    <h2>
                        <History size={16} /> Case audit trail
                    </h2>
                    <p className="muted">
                        Every action on this case since it was opened, oldest first, with
                        the officer who took it. Entries cannot be edited or removed.
                    </p>
                    {audit.error ? (
                        <div className="alert alert-error">
                            The audit trail could not be loaded: {audit.error}
                        </div>
                    ) : (
                        <>
                            <ChainBanner audit={audit} />
                            <Timeline entries={audit.entries} />
                            {audit.entries.length === 0 && (
                                <p className="muted">No events recorded for this case.</p>
                            )}
                        </>
                    )}
                </section>
            )}
        </div>
    );
}
