import { StrictMode, Suspense, lazy, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { Layout } from "./Layout";
import { Home } from "./pages/Home";
import "@fontsource/host-grotesk/400.css";
import "@fontsource/host-grotesk/500.css";
import "@fontsource/host-grotesk/600.css";
import "@fontsource/host-grotesk/700.css";
import "@fontsource/fragment-mono/400.css";
import { CrumbProvider, DialogProvider, ToastProvider } from "./components/ui";
import { AccountProvider, WorkspaceGate, useAccount } from "./components/account";
import type { Role } from "./api";
import "./styles.css";
import { applyTheme } from "./components/theme";
import { applyDensity } from "./components/density";

/** A page loaded when it's first opened, so the first screen downloads only what it shows. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function page<K extends string>(load: () => Promise<{ [key in K]: ComponentType<any> }>, name: K) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Lazy = lazy<ComponentType<any>>(() => load().then((module) => ({ default: module[name] })));
  return function Page(props: Record<string, unknown>) {
    return (
      <Suspense fallback={<p className="muted page-loading">Loading…</p>}>
        <Lazy {...props} />
      </Suspense>
    );
  };
}

const Dashboard = page(() => import("./pages/Dashboard"), "Dashboard");
const ExportView = page(() => import("./pages/ExportView"), "ExportView");
const Dataset = page(() => import("./pages/Dataset"), "Dataset");
const GetData = page(() => import("./pages/GetData"), "GetData");
const Transform = page(() => import("./pages/Transform"), "Transform");
const Model = page(() => import("./pages/Model"), "Model");
const Refresh = page(() => import("./pages/Refresh"), "Refresh");
const Lineage = page(() => import("./pages/Lineage"), "Lineage");
const Security = page(() => import("./pages/Security"), "Security");
const Help = page(() => import("./pages/Help"), "Help");
const Autopilot = page(() => import("./pages/Autopilot"), "Autopilot");
const SemanticModels = page(() => import("./pages/SemanticModels"), "SemanticModels");
const Settings = page(() => import("./pages/Settings"), "Settings");
const Members = page(() => import("./pages/Members"), "Members");
const Invite = page(() => import("./pages/Invite"), "Invite");
const Landing = page(() => import("./pages/Landing"), "Landing");
const Onboarding = page(() => import("./pages/Onboarding"), "Onboarding");
const WorkspaceSettings = page(() => import("./pages/WorkspaceSettings"), "WorkspaceSettings");
const Account = page(() => import("./pages/Account"), "Account");
const Billing = page(() => import("./pages/Billing"), "Billing");

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

applyTheme(); // before the first paint, so a dark screen never flashes white
applyDensity();

/** The landing page at /welcome needs no account, so it shows at once, signed in or not. */
function Root() {
  return useLocation().pathname === "/welcome" ? <Landing /> : <App />;
}

function App() {
  return (
      <ToastProvider>
      <DialogProvider>
      <CrumbProvider>
      <AccountProvider>
      <WorkspaceGate>
      <Routes>
        <Route path="invite/:token" element={<Invite />} />
        {/* The landing page's Sign in links: once signed in, start at Home. */}
        <Route path="sign-in" element={<Navigate to="/" replace />} />
        {/* A new workspace's first steps: invite the team, then start with data. */}
        <Route path="start" element={<Need role="creator"><Onboarding /></Need>} />
        {/* The PDF export is a page of its own, without the app's sidebar. */}
        <Route
          path="dashboards/:dashboardId/export"
          element={<ExportView />}
        />
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="datasets" element={<Need role="creator"><Home show="datasets" /></Need>} />
          <Route path="dashboards" element={<Home show="dashboards" />} />
          <Route path="help" element={<Help />} />
          <Route path="account" element={<Account />} />
          <Route path="semantic-models" element={<Need role="creator"><SemanticModels /></Need>} />
          <Route path="settings" element={<PlatformAdmin><Settings /></PlatformAdmin>} />
          <Route path="members" element={<Need role="admin"><Members /></Need>} />
          <Route path="workspace" element={<Need role="admin"><WorkspaceSettings /></Need>} />
          <Route path="billing" element={<Need role="admin"><Billing /></Need>} />
          <Route path="datasets/:datasetId" element={<Need role="creator"><Dataset /></Need>}>
            <Route index element={<Navigate to="get-data" replace />} />
            <Route path="get-data" element={<GetData />} />
            <Route path="transform" element={<Transform />} />
            <Route path="model" element={<Model />} />
            <Route path="refresh" element={<Refresh />} />
            <Route path="lineage" element={<Lineage />} />
            <Route path="security" element={<Security />} />
            <Route path="autopilot" element={<Autopilot />} />
          </Route>
          <Route
            path="dashboards/:dashboardId"
            element={<Dashboard />}
          />
          <Route path="projects/:projectId/*" element={<OldProjectLink />} />
        </Route>
      </Routes>
      </WorkspaceGate>
      </AccountProvider>
      </CrumbProvider>
      </DialogProvider>
      </ToastProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </StrictMode>,
);
