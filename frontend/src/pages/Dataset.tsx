import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useOutletContext, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, type BrokenVisual, type Dataset as DatasetData } from "../api";

// The dataset half of the pipeline, in Power BI order. Dashboards build on what is published here.
const STAGES = [
  { path: "get-data", name: "Get Data" },
  { path: "transform", name: "Transform" },
  { path: "model", name: "Model" },
];

export type DatasetContext = {
  project: DatasetData;
  refresh: () => Promise<DatasetData>;
};

export function useDataset() {
  return useOutletContext<DatasetContext>();
}

export function Dataset() {
  const { datasetId = "" } = useParams();
  const [params] = useSearchParams();
  const [dataset, setDataset] = useState<DatasetData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [problems, setProblems] = useState<BrokenVisual[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await api.getDataset(datasetId);
    setDataset(next);
    return next;
  }, [datasetId]);

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, [refresh]);

  async function rename() {
    if (!dataset) return;
    const name = prompt("Dataset name", dataset.name);
    if (!name?.trim() || name === dataset.name) return;
    try {
      setDataset(await api.renameDataset(dataset.id, name));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function publish(confirm = false) {
    setPublishing(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.publishDataset(datasetId, confirm);
      setDataset(result);
      setProblems(null);
      setNotice(
        result.dashboards.length
          ? `Published version ${result.status.version}. ${result.dashboards.length} ${result.dashboards.length === 1 ? "dashboard now uses" : "dashboards now use"} it.`
          : `Published version ${result.status.version}. Dashboards can now be built on it.`,
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.detail?.problems) setProblems(e.detail.problems);
      else setError((e as Error).message);
    } finally {
      setPublishing(false);
    }
  }

  if (error && !dataset) return <p className="bad">{error}</p>;
  if (!dataset) return <p className="muted">Loading…</p>;

  const { status } = dataset;
  // Coming from "Bring new data": once published, go back to the dashboard waiting for it.
  const waiting = params.get("for");
  const nextDashboard = waiting ?? (dataset.dashboards.length === 1 ? dataset.dashboards[0].id : null);

  return (
    <div className="project">
      <header className="project-header">
        <span className="kind-label">Dataset</span>
        <h1>{dataset.name}</h1>
        <button className="link" onClick={rename}>
          Rename
        </button>
        <span className="spacer" />
        <PublishBadge published={status.published} version={status.version} changes={status.unpublished_changes} />
        <button onClick={() => publish(false)} disabled={publishing || (status.published && !status.unpublished_changes)}>
          {publishing ? "Publishing…" : status.published ? "Publish changes" : "Publish dataset"}
        </button>
      </header>

      {error && <p className="bad">{error}</p>}
      {notice && (
        <div className="notice small">
          {notice}{" "}
          {nextDashboard && <Link to={`/dashboards/${nextDashboard}`}>Continue to the dashboard →</Link>}
        </div>
      )}
      {problems && (
        <div className="notice bad-notice small">
          <p>
            <strong>Publishing would break {problems.length} dashboard {problems.length === 1 ? "visual" : "visuals"}:</strong>
          </p>
          <ul>
            {problems.map((p, i) => (
              <li key={i}>
                <Link to={`/dashboards/${p.dashboard_id}`}>{p.dashboard}</Link> ({p.version}) · {p.page} · {p.visual}: {p.error}
              </li>
            ))}
          </ul>
          <div className="actions">
            <button className="secondary danger" onClick={() => publish(true)} disabled={publishing}>
              Publish anyway
            </button>
            <button className="secondary" onClick={() => setProblems(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {dataset.dashboards.length > 0 && (
        <p className="muted small used-by">
          Used by:{" "}
          {dataset.dashboards.map((d, i) => (
            <span key={d.id}>
              {i > 0 && ", "}
              <Link to={`/dashboards/${d.id}`}>{d.name}</Link>
            </span>
          ))}
        </p>
      )}

      <div className="project-body">
        <nav className="stage-rail" aria-label="Stages">
          {STAGES.map((stage, i) => (
            <NavLink key={stage.path} to={stage.path}>
              <span className="step">{i + 1}</span>
              {stage.name}
            </NavLink>
          ))}
          <p className="rail-hint muted small">Publish when the model is ready. Dashboards read the published version.</p>
        </nav>
        <section className="stage">
          <Outlet context={{ project: dataset, refresh } satisfies DatasetContext} />
        </section>
      </div>
    </div>
  );
}

export function PublishBadge({ published, version, changes }: { published: boolean; version?: number | null; changes: boolean }) {
  if (!published) return <span className="badge">Not published</span>;
  return (
    <span className={`badge ${changes ? "warn" : "ok-badge"}`}>
      {changes ? `Draft changes · v${version ?? ""} live` : `Published${version ? ` v${version}` : ""}`}
    </span>
  );
}
