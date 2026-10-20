import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, dashboardsApi, type DashboardSummary, type Dataset, type ProviderSettings } from "../api";
import { PublishBadge } from "./Dataset";

type Choice = "existing" | "new";

export function Home() {
  const [settings, setSettings] = useState<ProviderSettings | null>(null);
  const [dashboards, setDashboards] = useState<DashboardSummary[] | null>(null);
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<Choice | null>(null);
  const navigate = useNavigate();

  function load() {
    dashboardsApi.list().then(setDashboards).catch((e) => setError(e.message));
    api.listDatasets().then(setDatasets).catch((e) => setError(e.message));
  }

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setSettings(null));
    load();
  }, []);

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const connected = settings?.capabilities.tools !== undefined;
  const published = (datasets ?? []).filter((d) => d.status.published);

  return (
    <div className="page wide">
      <div className="home-head">
        <h1>Home</h1>
        <span className="spacer" />
        <button onClick={() => setCreating(creating ? null : published.length ? "existing" : "new")}>New dashboard</button>
      </div>
      {settings && !connected && (
        <div className="notice">
          Connect your AI provider first: <Link to="/settings">open Settings</Link> and run the connection test.
        </div>
      )}
      {error && <p className="bad">{error}</p>}

      {creating && (
        <NewDashboard
          choice={creating}
          onChoice={setCreating}
          published={published}
          onCreated={(path) => navigate(path)}
          onError={setError}
        />
      )}

      <section>
        <h2 className="section-title">Dashboards</h2>
        {dashboards?.length === 0 && <p className="muted small">No dashboards yet. Click "New dashboard" to make one.</p>}
        <ul className="project-list">
          {dashboards?.map((d) => (
            <li key={d.id}>
              <Link to={`/dashboards/${d.id}`} className="project-link">
                <strong>{d.name}</strong>
                <span className="muted small">
                  {d.dataset ? `On ${d.dataset.name}` : "Dataset removed"} · {d.pages} {d.pages === 1 ? "page" : "pages"} · updated{" "}
                  {new Date(d.updated_at).toLocaleString()}
                </span>
              </Link>
              <span className={`badge ${d.published ? (d.unpublished_changes ? "warn" : "ok-badge") : ""}`}>
                {d.published ? (d.unpublished_changes ? "Draft changes" : "Published") : "Draft"}
              </span>
              <button
                className="link danger"
                onClick={() => confirm(`Delete the dashboard "${d.name}"? Its dataset is kept.`) && act(() => dashboardsApi.remove(d.id))}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <div className="section-head">
          <h2 className="section-title">Datasets</h2>
          <NewDataset onCreated={(id) => navigate(`/datasets/${id}/get-data`)} onError={setError} />
        </div>
        <p className="muted small">
          A dataset is loaded, cleaned and modelled once, then reused by any number of dashboards.
        </p>
        {datasets?.length === 0 && <p className="muted small">No datasets yet.</p>}
        <ul className="project-list">
          {datasets?.map((d) => (
            <li key={d.id}>
              <Link to={`/datasets/${d.id}/get-data`} className="project-link">
                <strong>{d.name}</strong>
                <span className="muted small">
                  {d.sources.length} {d.sources.length === 1 ? "source" : "sources"} · used by {d.dashboards.length}{" "}
                  {d.dashboards.length === 1 ? "dashboard" : "dashboards"}
                </span>
              </Link>
              <PublishBadge published={d.status.published} version={d.status.version} changes={d.status.unpublished_changes} />
              <button
                className="link danger"
                onClick={() => confirm(`Delete the dataset "${d.name}" and all of its data? This cannot be undone.`) && act(() => api.deleteDataset(d.id))}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function NewDashboard(props: {
  choice: Choice;
  onChoice: (choice: Choice) => void;
  published: Dataset[];
  onCreated: (path: string) => void;
  onError: (message: string) => void;
}) {
  const { choice, published } = props;
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState(published[0]?.id ?? "");
  const [busy, setBusy] = useState(false);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      if (choice === "existing") {
        const dashboard = await dashboardsApi.create(name || "Untitled dashboard", datasetId);
        props.onCreated(`/dashboards/${dashboard.id}`);
      } else {
        // New data: the dataset comes first; the dashboard waits for it to be published.
        const title = name || "Untitled";
        const dataset = await api.createDataset(`${title} data`);
        const dashboard = await dashboardsApi.create(title, dataset.id);
        props.onCreated(`/datasets/${dataset.id}/get-data?for=${dashboard.id}`);
      }
    } catch (e) {
      props.onError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <form className="card new-dashboard" onSubmit={create}>
      <div className="choice-row" role="radiogroup" aria-label="Where the data comes from">
        <button
          type="button"
          role="radio"
          aria-checked={choice === "existing"}
          className={`choice ${choice === "existing" ? "on" : ""}`}
          onClick={() => props.onChoice("existing")}
          disabled={!published.length}
        >
          <strong>Use existing data</strong>
          <span className="muted small">
            {published.length ? "Build on a published dataset; it stays in sync." : "No published datasets yet."}
          </span>
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={choice === "new"}
          className={`choice ${choice === "new" ? "on" : ""}`}
          onClick={() => props.onChoice("new")}
        >
          <strong>Bring new data</strong>
          <span className="muted small">Load files or a Google Sheet; the AI cleans and models it first.</span>
        </button>
      </div>
      <div className="inline-form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Dashboard name" aria-label="Dashboard name" />
        {choice === "existing" && (
          <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)} aria-label="Dataset">
            {published.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} (v{d.status.version})
              </option>
            ))}
          </select>
        )}
        <button type="submit" disabled={busy || (choice === "existing" && !datasetId)}>
          {busy ? "Creating…" : choice === "existing" ? "Create dashboard" : "Start with data"}
        </button>
      </div>
    </form>
  );
}

function NewDataset({ onCreated, onError }: { onCreated: (id: string) => void; onError: (message: string) => void }) {
  async function create() {
    const name = prompt("Dataset name");
    if (!name?.trim()) return;
    try {
      onCreated((await api.createDataset(name)).id);
    } catch (e) {
      onError((e as Error).message);
    }
  }
  return (
    <button className="link small" onClick={create}>
      + New dataset
    </button>
  );
}
