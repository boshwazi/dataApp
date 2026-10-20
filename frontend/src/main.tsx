import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { Layout } from "./Layout";
import { Dataset } from "./pages/Dataset";
import { GetData } from "./pages/GetData";
import { Home } from "./pages/Home";
import { Model } from "./pages/Model";
import { Lineage } from "./pages/Lineage";
import { Refresh } from "./pages/Refresh";
import { Settings } from "./pages/Settings";
import { Transform } from "./pages/Transform";
import "@fontsource/plus-jakarta-sans/400.css";
import "@fontsource/plus-jakarta-sans/500.css";
import "@fontsource/plus-jakarta-sans/600.css";
import "@fontsource/plus-jakarta-sans/700.css";
import "@fontsource/plus-jakarta-sans/800.css";
import { CrumbProvider, DialogProvider, ToastProvider } from "./components/ui";
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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
      <DialogProvider>
      <CrumbProvider>
      <Routes>
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
          <Route path="datasets" element={<Home show="datasets" />} />
          <Route path="dashboards" element={<Home show="dashboards" />} />
          <Route path="settings" element={<Settings />} />
          <Route path="datasets/:datasetId" element={<Dataset />}>
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
      </CrumbProvider>
      </DialogProvider>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
);
