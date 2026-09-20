import { useEffect, useState, useCallback } from "react";
import { Navigate, useLocation } from "react-router-dom";

import * as api from "./api/client";
import { AuthContext, useAuth } from "./auth-context";

// ---------------------------------------------------------------
// Who is logged in, and the gate in front of every other screen.
//
// The token lives in sessionStorage; this holds the user it belongs to.
// On a hard refresh we re-fetch /me rather than trusting anything
// cached, because the session may have been revoked in between.
// ---------------------------------------------------------------

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null);

    // Derived from the token up front rather than set inside the effect,
    // so there is no synchronous state write on mount.
    const [loading, setLoading] = useState(() => Boolean(api.tokenStore.get()));

    const signOut = useCallback(async () => {
        // Best effort: if the network call fails the token is dropped
        // locally anyway, so the session cannot be reused from here.
        await api.logout().catch(() => {});
        api.tokenStore.clear();
        setUser(null);
    }, []);

    useEffect(() => {
        let cancelled = false;
        api.setUnauthorizedHandler(() => setUser(null));

        if (!api.tokenStore.get()) return undefined;

        api.me()
            .then((res) => {
                if (!cancelled) setUser(res.user);
            })
            .catch(() => api.tokenStore.clear())
            .finally(() => {
                if (!cancelled) setLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, []);

    const signIn = useCallback((token, u) => {
        api.tokenStore.set(token);
        setUser(u || null);
        return api.me().then((res) => {
            setUser(res.user);
            return res.user;
        });
    }, []);

    return (
        <AuthContext.Provider value={{ user, loading, signIn, signOut }}>
            {children}
        </AuthContext.Provider>
    );
}

/** Everything except the login screen sits behind this. */
export function RequireAuth({ children }) {
    const { user, loading } = useAuth();
    const location = useLocation();

    if (loading) return <div className="page-loading">Checking session...</div>;
    if (!user) return <Navigate to="/" replace state={{ from: location }} />;

    return children;
}
