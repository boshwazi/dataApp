import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useOutletContext, useParams } from "react-router-dom";
import { api, type Project as ProjectData } from "../api";

// Pipeline order, as in Power BI. Later stages arrive in later milestones.
const STAGES = [
  { path: "get-data", name: "Get Data", ready: true },
  { path: "transform", name: "Transform", ready: false },
  { path: "model", name: "Model", ready: false },
  { path: "report", name: "Report", ready: false },
  { path: "ask", name: "Ask", ready: false },
  { path: "export", name: "Export", ready: false },
];

export type ProjectContext = {
  project: ProjectData;
  refresh: () => Promise<ProjectData>;
};

export function useProject() {
  return useOutletContext<ProjectContext>();
}

export function Project() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<ProjectData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await api.getProject(projectId);
    setProject(next);
    return next;
  }, [projectId]);

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, [refresh]);

  async function rename() {
    if (!project) return;
    const name = prompt("Project name", project.name);
    if (!name?.trim() || name === project.name) return;
    try {
      setProject(await api.renameProject(project.id, name));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error) return <p className="bad">{error}</p>;
  if (!project) return <p className="muted">Loading…</p>;

  return (
    <div className="project">
      <header className="project-header">
        <h1>{project.name}</h1>
        <button className="link" onClick={rename}>
          Rename
        </button>
      </header>
      <div className="project-body">
        <nav className="stage-rail" aria-label="Stages">
          {STAGES.map((stage, i) =>
            stage.ready ? (
              <NavLink key={stage.path} to={stage.path}>
                <span className="step">{i + 1}</span>
                {stage.name}
              </NavLink>
            ) : (
              <span key={stage.path} className="disabled" title="Coming in a later milestone">
                <span className="step">{i + 1}</span>
                {stage.name}
              </span>
            ),
          )}
        </nav>
        <section className="stage">
          <Outlet context={{ project, refresh } satisfies ProjectContext} />
        </section>
      </div>
    </div>
  );
}
