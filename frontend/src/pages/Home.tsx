import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { BadgeCheck, Database, LayoutDashboard, Plus, Search, Trash2 } from "lucide-react";
import { api, dashboardsApi, type DashboardSummary, type Dataset, type ProviderSettings } from "../api";
import { EmptyState, Skeleton, useCrumbs, useDialogs, useToast } from "../components/ui";
import { useAccount } from "../components/account";
import { QuickStart } from "./Autopilot";

type Choice = "existing" | "new";
type Show = "all" | "datasets" | "dashboards";

export function Home({ show: wanted = "all" }: { show?: Show }) {
  const account = useAccount();
  const creator = account.can("creator");
  // Viewers only work with published dashboards.
  const show: Show = creator ? wanted : "dashboards";
  const [settings, setSettings] = useState<ProviderSettings | null>(null);
  const [dashboards, setDashboards] = useState<DashboardSummary[] | null>(null);
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState<Choice | null>(null);
  const [filter, setFilter] = useState("");
  const navigate = useNavigate();
  const toast = useToast();
  const dialogs = useDialogs();

  useCrumbs([{ label: wanted === "all" ? "Home" : show === "datasets" ? "Datasets" : "Dashboards" }]);

  function load() {
    dashboardsApi.list().then(setDashboards).catch((e) => toast(e.message, "error"));
    if (creator) api.listDatasets().then(setDatasets).catch((e) => toast(e.message, "error"));
    else setDatasets([]);
  }

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setSettings(null));
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const published = (datasets ?? []).filter((d) => d.status.published);

  // "New dashboard" from quick search lands here with ?new=1.
  useEffect(() => {
    if (params.get("new") && datasets) {
      setCreating(published.length ? "existing" : "new");
      setParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, datasets]);

  async function removeDashboard(d: DashboardSummary) {
    const ok = await dialogs.confirm({
      title: `Delete ${d.name}?`,
      body: "The dashboard and its pages are deleted. Its dataset is kept.",
      confirmLabel: "Delete dashboard",
      danger: true,
    });
    if (!ok) return;
    try {
      await dashboardsApi.remove(d.id);
      toast(`Deleted ${d.name}`);
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  async function removeDataset(d: Dataset) {
    const ok = await dialogs.confirm({
      title: `Delete ${d.name}?`,
      body: "Its sources, steps, model and published versions are deleted. This can't be undone.",
      confirmLabel: "Delete dataset",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteDataset(d.id);
      toast(`Deleted ${d.name}`);
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  async function newDataset() {
    const name = await dialogs.prompt({ title: "New dataset", label: "Name", confirmLabel: "Create dataset", hint: "For example: Sales 2025" });
    if (!name) return;
    try {
      const dataset = await api.createDataset(name);
      navigate(`/datasets/${dataset.id}/get-data`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  const connected = settings?.capabilities.tools !== undefined;
  const match = (name: string) => name.toLowerCase().includes(filter.trim().toLowerCase());
  const shownDashboards = (dashboards ?? []).filter((d) => match(d.name));
  const shownDatasets = (datasets ?? []).filter((d) => match(d.name));
  const title = wanted === "all" ? "Home" : show === "datasets" ? "Datasets" : "Dashboards";

  return (
    <div className="page wide">
      <div className="home-head">
        <h1>{title}</h1>
        <span className="spacer" />
        <label className="filter-box">
          <Search size={15} aria-hidden="true" />
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by name" aria-label="Filter by name" />
        </label>
        {creator && show !== "dashboards" && (
          <button className="secondary" onClick={newDataset}>
            <Database size={16} aria-hidden="true" /> New dataset
          </button>
        )}
        {creator && show !== "datasets" && (
          <button onClick={() => setCreating(creating ? null : published.length ? "existing" : "new")} aria-expanded={!!creating}>
            <Plus size={16} aria-hidden="true" /> New dashboard
          </button>
        )}
      </div>
      {settings && !connected && creator && (
        <div className="notice">
          {account.me.platform_admin ? (
            <>
              Connect your AI provider first: <Link to="/settings">open Settings</Link> and run the connection test.
            </>
          ) : (
            "The AI isn't connected yet. The people running datawazi need to set it up."
          )}
        </div>
      )}

      {creator && connected && show === "all" && <QuickStart />}

      {creating && (
        <NewDashboard
          choice={creating}
          onChoice={setCreating}
          published={published}
          onCreated={(path) => navigate(path)}
          onError={(m) => toast(m, "error")}
        />
      )}

      {show !== "datasets" && (
        <section className="home-section">
          <h2 className="section-title">Dashboards</h2>
          {dashboards === null ? (
            <Skeleton lines={3} height={52} />
          ) : dashboards.length === 0 ? (
            <EmptyState icon={<LayoutDashboard size={28} />} title={creator ? "No dashboards yet" : "Nothing published yet"}>
              {creator
                ? "A dashboard is built by the AI from a dataset's measures. Click New dashboard to make the first one."
                : "Dashboards show up here once someone in your team publishes them."}
            </EmptyState>
          ) : (
            <table className="catalog">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Dataset</th>
                  <th className="num">Pages</th>
                  {creator && <th>Status</th>}
                  <th>Updated</th>
                  {creator && <th aria-label="Actions" />}
                </tr>
              </thead>
              <tbody>
                {shownDashboards.map((d) => (
                  <tr key={d.id} onClick={() => navigate(`/dashboards/${d.id}`)}>
                    <td className="catalog-name">
                      <LayoutDashboard size={16} className="row-icon" aria-hidden="true" />
                      <Link to={`/dashboards/${d.id}`} onClick={(e) => e.stopPropagation()}>
                        {d.name}
                      </Link>
                    </td>
                    <td className="muted">{d.dataset ? <span className="ident">{d.dataset.name}</span> : <span className="bad">Removed</span>}</td>
                    <td className="num">{d.pages}</td>
                    {creator && (
                      <td>
                        <span className={`status-dot ${d.published ? (d.unpublished_changes ? "warn" : "ok") : ""}`}>
                          {d.published ? (d.unpublished_changes ? "Draft changes" : "Published") : "Draft"}
                        </span>
                      </td>
                    )}
                    <td className="muted" title={new Date(d.updated_at).toLocaleString()}>
                      {ago(d.updated_at)}
                    </td>
                    {creator && (
                      <td className="catalog-actions">
                        <button
                          className="icon danger"
                          aria-label={`Delete ${d.name}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            void removeDashboard(d);
                          }}
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {show !== "dashboards" && (
        <section className="home-section">
          <h2 className="section-title">Datasets</h2>
          <p className="muted small section-hint">Load, clean and model data once, then reuse it in any number of dashboards.</p>
          {datasets === null ? (
            <Skeleton lines={3} height={52} />
          ) : datasets.length === 0 ? (
            <EmptyState
              icon={<Database size={28} />}
              title="No datasets yet"
              action={
                <button onClick={newDataset}>
                  <Plus size={16} aria-hidden="true" /> New dataset
                </button>
              }
            >
              Start with a CSV, an Excel file, a Google Sheet or the sample data.
            </EmptyState>
          ) : (
            <table className="catalog">
              <thead>
                <tr>
                  <th>Name</th>
                  <th className="num">Tables</th>
                  <th className="num">Steps</th>
                  <th className="num">Measures</th>
                  <th>Status</th>
                  <th className="num">Used by</th>
                  <th>Updated</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {shownDatasets.map((d) => (
                  <tr key={d.id} onClick={() => navigate(`/datasets/${d.id}/get-data`)}>
                    <td className="catalog-name">
                      <Database size={16} className="row-icon" aria-hidden="true" />
                      <Link to={`/datasets/${d.id}/get-data`} onClick={(e) => e.stopPropagation()}>
                        {d.name}
                      </Link>
                      {d.certified && <BadgeCheck size={14} className="certified-icon" aria-label="Certified" />}
                    </td>
                    <td className="num">{d.progress.sources}</td>
                    <td className="num">{d.progress.steps}</td>
                    <td className="num">{d.progress.measures}</td>
                    <td>
                      <span className={`status-dot ${d.status.published ? (d.status.unpublished_changes ? "warn" : "ok") : ""}`}>
                        {d.status.published ? (d.status.unpublished_changes ? `Draft changes, v${d.status.version} live` : `Published v${d.status.version}`) : "Not published"}
                      </span>
                    </td>
                    <td className="num">{d.dashboards.length}</td>
                    <td className="muted" title={new Date(d.updated_at).toLocaleString()}>
                      {ago(d.updated_at)}
                    </td>
                    <td className="catalog-actions">
                      <button
                        className="icon danger"
                        aria-label={`Delete ${d.name}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          void removeDataset(d);
                        }}
                      >
                        <Trash2 size={15} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
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
      <h2>New dashboard</h2>
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
        <label className="field grow">
          <span>Dashboard name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Sales review" />
        </label>
        {choice === "existing" && (
          <label className="field">
            <span>Dataset</span>
            <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
              {published.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.certified ? "✓ " : ""}
                  {d.name} (v{d.status.version})
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="submit" className="align-end" disabled={busy || (choice === "existing" && !datasetId)}>
          {busy ? "Creating…" : choice === "existing" ? "Create dashboard" : "Start with data"}
        </button>
      </div>
    </form>
  );
}

/** "3 hours ago", "yesterday", "12 Mar": how long ago, as a data catalog shows it. */
function ago(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 172800) return "Yesterday";
  if (seconds < 604800) return `${Math.floor(seconds / 86400)} days ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
