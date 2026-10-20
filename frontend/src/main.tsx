import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { Layout } from "./Layout";
import { Dataset } from "./pages/Dataset";
import { GetData } from "./pages/GetData";
import { Home } from "./pages/Home";
import { Model } from "./pages/Model";
import { Settings } from "./pages/Settings";
import { Transform } from "./pages/Transform";
import "./styles.css";

// Dashboards carry the chart library, so they load only when opened.
const Dashboard = lazy(() => import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })));

// Links from before datasets and dashboards were split.
function OldProjectLink() {
  const { projectId, "*": rest } = useParams();
  const stage = rest && rest !== "report" ? rest : "get-data";
  return <Navigate to={`/datasets/${projectId}/${stage}`} replace />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="settings" element={<Settings />} />
          <Route path="datasets/:datasetId" element={<Dataset />}>
            <Route index element={<Navigate to="get-data" replace />} />
            <Route path="get-data" element={<GetData />} />
            <Route path="transform" element={<Transform />} />
            <Route path="model" element={<Model />} />
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
    </BrowserRouter>
  </StrictMode>,
);
