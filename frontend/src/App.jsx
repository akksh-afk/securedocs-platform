import { Routes, Route, Navigate } from "react-router-dom";

import { AuthProvider, RequireAuth } from "./auth";
import Layout from "./components/Layout";

import Login from "./pages/Login";
import CaseList from "./pages/CaseList";
import CaseDetail from "./pages/CaseDetail";
import DocumentView from "./pages/DocumentView";
import Verify from "./pages/Verify";
import AuditTimeline from "./pages/AuditTimeline";

// ---------------------------------------------------------------
// Six screens, as specified. Everything except login sits behind
// RequireAuth, and a 401 anywhere drops the token and lands back here.
//
// The older mock pages (Dashboard, Documents, Upload, Search, Users)
// are intentionally not routed. They were never wired to the API and
// showed invented data, which is not something to have one click away
// during a demo.
// ---------------------------------------------------------------

function Shell({ children }) {
    return (
        <RequireAuth>
            <Layout>{children}</Layout>
        </RequireAuth>
    );
}

export default function App() {
    return (
        <AuthProvider>
            <Routes>
                <Route path="/" element={<Login />} />

                <Route
                    path="/cases"
                    element={
                        <Shell>
                            <CaseList />
                        </Shell>
                    }
                />
                <Route
                    path="/cases/:caseId"
                    element={
                        <Shell>
                            <CaseDetail />
                        </Shell>
                    }
                />
                <Route
                    path="/documents/:documentId"
                    element={
                        <Shell>
                            <DocumentView />
                        </Shell>
                    }
                />
                <Route
                    path="/documents/:documentId/verify"
                    element={
                        <Shell>
                            <Verify />
                        </Shell>
                    }
                />
                <Route
                    path="/documents/:documentId/audit"
                    element={
                        <Shell>
                            <AuditTimeline />
                        </Shell>
                    }
                />

                <Route path="*" element={<Navigate to="/cases" replace />} />
            </Routes>
        </AuthProvider>
    );
}
