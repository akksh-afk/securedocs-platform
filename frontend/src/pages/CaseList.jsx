import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Folder, ShieldAlert, Plus } from "lucide-react";

import * as api from "../api/client";
import { useAuth } from "../auth-context";

// ---------------------------------------------------------------
// Screen 2: the cases this officer is assigned to.
//
// There is no "all cases" view and there is not meant to be. The
// backend joins through case_assignments, so an unassigned case is not
// hidden here - it is genuinely absent from the response.
// ---------------------------------------------------------------

// Opening a case. The station and the creator come from the session on
// the server; the creator is put on the case automatically.
function NewCaseForm() {
    const navigate = useNavigate();
    const [caseNumber, setCaseNumber] = useState("");
    const [title, setTitle] = useState("");
    const [sensitivity, setSensitivity] = useState("normal");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);

    async function submit(e) {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            const created = await api.createCase({
                caseNumber: caseNumber.trim(),
                title: title.trim(),
                sensitivity,
            });
            navigate(`/cases/${created.id}`);
        } catch (err) {
            setError(err.message);
            setBusy(false);
        }
    }

    return (
        <section className="panel">
            <h2>
                <Plus size={16} /> Open a new case
            </h2>
            {error && <div className="alert alert-error">{error}</div>}
            <form onSubmit={submit} className="upload-form">
                <div className="input-group">
                    <label>Case number</label>
                    <input
                        value={caseNumber}
                        onChange={(e) => setCaseNumber(e.target.value)}
                        placeholder="FIR/0231/2026"
                        required
                    />
                </div>
                <div className="input-group">
                    <label>Title</label>
                    <input value={title} onChange={(e) => setTitle(e.target.value)} required />
                </div>
                <div className="input-group">
                    <label>Sensitivity</label>
                    <select value={sensitivity} onChange={(e) => setSensitivity(e.target.value)}>
                        <option value="normal">normal</option>
                        <option value="restricted">restricted</option>
                        <option value="protected">protected (BNS S.72 - forces redaction)</option>
                    </select>
                </div>
                <button type="submit" className="btn" disabled={busy}>
                    {busy ? "Opening..." : "Open case"}
                </button>
            </form>
        </section>
    );
}

export default function CaseList() {
    const { user } = useAuth();
    const [cases, setCases] = useState([]);
    const [total, setTotal] = useState(0);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        api.listCases()
            .then((res) => {
                setCases(res.items || []);
                setTotal(res.total || 0);
            })
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }, []);

    return (
        <div>
            <div className="page-header">
                <h1>My cases</h1>
                <p>
                    {loading
                        ? "Loading..."
                        : `${total} case${total === 1 ? "" : "s"} assigned to you`}
                </p>
            </div>

            {error && <div className="alert alert-error">{error}</div>}

            {user && user.permissions && user.permissions.includes("case.create") && (
                <NewCaseForm />
            )}

            {!loading && cases.length === 0 && !error && (
                <div className="empty-state">
                    <Folder size={32} />
                    <h3>No cases assigned</h3>
                    <p>
                        You are not assigned to any case. Rank alone does not grant
                        access - an inspector must assign you.
                    </p>
                </div>
            )}

            <div className="card-grid">
                {cases.map((c) => (
                    <Link key={c.id} to={`/cases/${c.id}`} className="case-card">
                        <div className="case-card-head">
                            <span className="case-number">{c.case_number}</span>
                            {c.sensitivity === "protected" && (
                                <span className="badge badge-protected">
                                    <ShieldAlert size={13} /> Protected
                                </span>
                            )}
                            {c.sensitivity === "restricted" && (
                                <span className="badge badge-restricted">Restricted</span>
                            )}
                        </div>

                        <h3>{c.title}</h3>

                        <div className="case-card-foot">
                            <span className={`status status-${c.status}`}>
                                {String(c.status).replace(/_/g, " ")}
                            </span>
                            <span className="muted">
                                {new Date(c.created_at).toLocaleDateString()}
                            </span>
                        </div>
                    </Link>
                ))}
            </div>
        </div>
    );
}
