import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./Layout";
import { GetData } from "./pages/GetData";
import { Home } from "./pages/Home";
import { Model } from "./pages/Model";
import { Project } from "./pages/Project";
import { Settings } from "./pages/Settings";
import { Transform } from "./pages/Transform";
import "./styles.css";

// The Report tab carries the chart library, so it loads only when opened.
const Report = lazy(() => import("./pages/Report").then((m) => ({ default: m.Report })));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="settings" element={<Settings />} />
          <Route path="projects/:projectId" element={<Project />}>
            <Route index element={<Navigate to="get-data" replace />} />
            <Route path="get-data" element={<GetData />} />
            <Route path="transform" element={<Transform />} />
            <Route path="model" element={<Model />} />
            <Route
              path="report"
              element={
                <Suspense fallback={<p className="muted">Loading…</p>}>
                  <Report />
                </Suspense>
              }
            />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
