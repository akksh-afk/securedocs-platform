import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Lock, User, Smartphone, KeyRound } from "lucide-react";

import * as api from "../api/client";
import { useAuth } from "../auth-context";

// ---------------------------------------------------------------
// Screen 1: service number, password and registered mobile number,
// then the code texted to that number.
//
// Two steps. The first proves the officer knows the password and
// names their own phone; only then is a code sent. The second proves
// they hold the phone. Neither alone produces a session.
// ---------------------------------------------------------------

export default function Login() {
    const navigate = useNavigate();
    const { signIn } = useAuth();

    const [stage, setStage] = useState("credentials");
    const [serviceNumber, setServiceNumber] = useState("");
    const [password, setPassword] = useState("");
    const [mobile, setMobile] = useState("");
    const [code, setCode] = useState("");
    const [otpToken, setOtpToken] = useState(null);
    const [notice, setNotice] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [resendIn, setResendIn] = useState(0);

    useEffect(() => {
        if (resendIn <= 0) return undefined;
        const t = setTimeout(() => setResendIn((s) => s - 1), 1000);
        return () => clearTimeout(t);
    }, [resendIn]);

    async function sendCode(e) {
        if (e) e.preventDefault();
        setError(null);
        setBusy(true);
        try {
            const res = await api.requestOtp(serviceNumber.trim(), password, mobile.trim());
            setNotice(res.message);
            setOtpToken(res.otp_token);
            setResendIn(res.resend_after || 60);
            setCode("");
            setStage("otp");
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    }

    async function submitCode(e) {
        e.preventDefault();
        setError(null);
        setBusy(true);
        try {
            const res = await api.verifyOtp(otpToken, code.trim());
            await signIn(res.session_token, res.user);
            navigate("/cases", { replace: true });
        } catch (err) {
            setError(err.message);
            setCode("");
        } finally {
            setBusy(false);
        }
    }

    return (
        <div className="login-page">
            <div className="login-card">
                <div className="login-header">
                    <div className="login-icon">
                        {stage === "credentials" ? <Lock size={35} /> : <KeyRound size={35} />}
                    </div>
                    <h1>SecureDocs</h1>
                    <p>Secure Legal Document Management</p>
                </div>

                {error && <div className="alert alert-error">{error}</div>}

                {stage === "credentials" ? (
                    <form onSubmit={sendCode}>
                        <div className="input-group">
                            <label>Service number</label>
                            <div className="input-wrapper">
                                <User size={18} />
                                <input
                                    value={serviceNumber}
                                    onChange={(e) => setServiceNumber(e.target.value)}
                                    placeholder="DL-INS-1001"
                                    autoComplete="username"
                                    autoFocus
                                    required
                                />
                            </div>
                        </div>

                        <div className="input-group">
                            <label>Password</label>
                            <div className="input-wrapper">
                                <Lock size={18} />
                                <input
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    autoComplete="current-password"
                                    required
                                />
                            </div>
                        </div>

                        <div className="input-group">
                            <label>Registered mobile number</label>
                            <div className="input-wrapper">
                                <Smartphone size={18} />
                                <input
                                    type="tel"
                                    value={mobile}
                                    onChange={(e) => setMobile(e.target.value)}
                                    placeholder="98765 43210"
                                    autoComplete="tel"
                                    inputMode="tel"
                                    required
                                />
                            </div>
                        </div>

                        <button type="submit" className="login-button" disabled={busy}>
                            {busy ? "Checking..." : "Send code"}
                        </button>
                    </form>
                ) : (
                    <form onSubmit={submitCode}>
                        {notice && <p className="mfa-hint">{notice}</p>}

                        <div className="input-group">
                            <label>6-digit code</label>
                            <div className="input-wrapper">
                                <KeyRound size={18} />
                                <input
                                    value={code}
                                    onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                                    inputMode="numeric"
                                    pattern="[0-9]{6}"
                                    maxLength={6}
                                    autoComplete="one-time-code"
                                    autoFocus
                                    required
                                />
                            </div>
                        </div>

                        <button type="submit" className="login-button" disabled={busy}>
                            {busy ? "Verifying..." : "Sign in"}
                        </button>
                        <button
                            type="button"
                            className="link-button"
                            disabled={busy || resendIn > 0}
                            onClick={() => sendCode()}
                        >
                            {resendIn > 0 ? `Resend code in ${resendIn}s` : "Resend code"}
                        </button>
                        <button
                            type="button"
                            className="link-button"
                            onClick={() => {
                                setStage("credentials");
                                setError(null);
                                setOtpToken(null);
                            }}
                        >
                            Back
                        </button>
                    </form>
                )}
            </div>
        </div>
    );
}
