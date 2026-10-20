import { StrictMode, Suspense, lazy, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { Layout } from "./Layout";
import { Dataset } from "./pages/Dataset";
import { GetData } from "./pages/GetData";
import { Home } from "./pages/Home";
import { Invite } from "./pages/Invite";
import { Members } from "./pages/Members";
import { Model } from "./pages/Model";
import { Lineage } from "./pages/Lineage";
import { Refresh } from "./pages/Refresh";
import { Settings } from "./pages/Settings";
import { Transform } from "./pages/Transform";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import { CrumbProvider, DialogProvider, ToastProvider } from "./components/ui";
import { AccountProvider, WorkspaceGate, useAccount } from "./components/account";
import type { Role } from "./api";
import "./styles.css";

// Dashboards carry the chart library, so they load only when opened.
const Dashboard = lazy(() => import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const ExportView = lazy(() => import("./pages/ExportView").then((m) => ({ default: m.ExportView })));

// Links from before datasets and dashboards were split.
function OldProjectLink() {
  const { projectId, "*": rest } = useParams();
  const stage = rest && rest !== "report" ? rest : "get-data";
  return <Navigate to={`/datasets/${projectId}/${stage}`} replace />;
}

// Pages a role may not use send it home; the API refuses those calls as well.
function Need({ role, children }: { role: Role; children: ReactNode }) {
  return useAccount().can(role) ? children : <Navigate to="/" replace />;
}

function PlatformAdmin({ children }: { children: ReactNode }) {
  return useAccount().me.platform_admin ? children : <Navigate to="/" replace />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
      <DialogProvider>
      <CrumbProvider>
      <AccountProvider>
      <WorkspaceGate>
      <Routes>
        <Route path="invite/:token" element={<Invite />} />
        {/* The PDF export is a page of its own, without the app's sidebar. */}
        <Route
          path="dashboards/:dashboardId/export"
          element={
            <Suspense fallback={<p className="muted">Preparing the export…</p>}>
              <ExportView />
            </Suspense>
          }
        />
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="datasets" element={<Need role="creator"><Home show="datasets" /></Need>} />
          <Route path="dashboards" element={<Home show="dashboards" />} />
          <Route path="settings" element={<PlatformAdmin><Settings /></PlatformAdmin>} />
          <Route path="members" element={<Need role="admin"><Members /></Need>} />
          <Route path="datasets/:datasetId" element={<Need role="creator"><Dataset /></Need>}>
            <Route index element={<Navigate to="get-data" replace />} />
            <Route path="get-data" element={<GetData />} />
            <Route path="transform" element={<Transform />} />
            <Route path="model" element={<Model />} />
            <Route path="refresh" element={<Refresh />} />
            <Route path="lineage" element={<Lineage />} />
          </Route>
          <Route
            path="dashboards/:dashboardId"
            element={
              <Suspense fallback={<p className="muted">Loading…</p>}>
                <Dashboard />
              </Suspense>
            }
          />
          <Route path="projects/:projectId/*" element={<OldProjectLink />} />
        </Route>
      </Routes>
      </WorkspaceGate>
      </AccountProvider>
      </CrumbProvider>
      </DialogProvider>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
);
