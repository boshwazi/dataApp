import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { BadgeCheck, Database, LayoutDashboard, Plus, Trash2 } from "lucide-react";
import { api, dashboardsApi, type DashboardSummary, type Dataset, type ProviderSettings } from "../api";
import { EmptyState, Skeleton, useCrumbs, useDialogs, useToast } from "../components/ui";
import { useAccount } from "../components/account";
import { PublishBadge } from "./Dataset";

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
  const title = wanted === "all" ? "Home" : show === "datasets" ? "Datasets" : "Dashboards";

  return (
    <div className="page wide">
      <div className="home-head">
        <h1>{title}</h1>
        <span className="spacer" />
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
            "The AI isn't connected yet. The people running datalemon need to set it up."
          )}
        </div>
      )}

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
            <ul className="project-list">
              {dashboards.map((d) => (
                <li key={d.id}>
                  <LayoutDashboard size={18} className="row-icon" aria-hidden="true" />
                  <Link to={`/dashboards/${d.id}`} className="project-link">
                    <strong>{d.name}</strong>
                    <span className="muted small">
                      {d.dataset ? `On ${d.dataset.name}` : "Its dataset was removed"}, {d.pages} {d.pages === 1 ? "page" : "pages"}, updated{" "}
                      {new Date(d.updated_at).toLocaleDateString()}
                    </span>
                  </Link>
                  {creator && (
                    <>
                      <span className={`badge ${d.published ? (d.unpublished_changes ? "warn" : "ok-badge") : ""}`}>
                        {d.published ? (d.unpublished_changes ? "Draft changes" : "Published") : "Draft"}
                      </span>
                      <button className="icon danger" aria-label={`Delete ${d.name}`} onClick={() => removeDashboard(d)}>
                        <Trash2 size={16} />
                      </button>
                    </>
                  )}
                </li>
              ))}
            </ul>
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
            <ul className="project-list">
              {datasets.map((d) => (
                <li key={d.id}>
                  <Database size={18} className="row-icon" aria-hidden="true" />
                  <Link to={`/datasets/${d.id}/get-data`} className="project-link">
                    <strong>
                      {d.name}
                      {d.certified && <BadgeCheck size={15} className="certified-icon" aria-label="Certified" />}
                    </strong>
                    <span className="muted small">
                      {d.sources.length} {d.sources.length === 1 ? "source" : "sources"}, used by {d.dashboards.length}{" "}
                      {d.dashboards.length === 1 ? "dashboard" : "dashboards"}
                    </span>
                  </Link>
                  <PublishBadge published={d.status.published} version={d.status.version} changes={d.status.unpublished_changes} />
                  <button className="icon danger" aria-label={`Delete ${d.name}`} onClick={() => removeDataset(d)}>
                    <Trash2 size={16} />
                  </button>
                </li>
              ))}
            </ul>
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
