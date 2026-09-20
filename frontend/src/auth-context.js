import { createContext, useContext } from "react";

// Context and hook live apart from the components that provide them so
// that auth.jsx exports components only - React Fast Refresh cannot
// hot-reload a module that mixes the two.

export const AuthContext = createContext(null);

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
    return ctx;
}
