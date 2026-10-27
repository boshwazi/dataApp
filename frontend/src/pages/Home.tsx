import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { BadgeCheck, Database, LayoutDashboard, Plus, Search, Trash2 } from "lucide-react";
import {
  api,
  dashboardsApi,
  reportApi,
  type DashboardSummary,
  type DashboardTemplate,
  type Dataset,
  type DesignBrief,
  type ProviderSettings,
  type RefreshAlert,
} from "../api";
import { EmptyState, Skeleton, useCrumbs, useDialogs, useToast } from "../components/ui";
import { useAccount } from "../components/account";
import { QuickStart } from "../components/QuickStart";
import { NextSteps } from "../components/NextSteps";
import { t } from "../i18n";

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
  const [alerts, setAlerts] = useState<(RefreshAlert & { dataset_id: string; dataset_name: string })[]>([]);
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState<Choice | null>(null);
  const [filter, setFilter] = useState("");
  const navigate = useNavigate();
  const toast = useToast();
  const dialogs = useDialogs();

  useCrumbs([{ label: t(wanted === "all" ? "Home" : show === "datasets" ? "Datasets" : "Dashboards") }]);

  function load() {
    dashboardsApi.list().then(setDashboards).catch((e) => toast(e.message, "error"));
    if (creator) api.listDatasets().then(setDatasets).catch((e) => toast(e.message, "error"));
    if (creator) api.refreshAlerts().then((list) => setAlerts(list as typeof alerts)).catch(() => setAlerts([]));
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
  const title = t(wanted === "all" ? "Home" : show === "datasets" ? "Datasets" : "Dashboards");

  return (
    <div className="page wide">
      <div className="home-head">
        <h1>{title}</h1>
        <span className="spacer" />
        <label className="filter-box">
          <Search size={15} aria-hidden="true" />
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`${t("Filter by name")}…`} aria-label={t("Filter by name")} />
        </label>
        {creator && show !== "dashboards" && (
          <button className="secondary" onClick={newDataset}>
            <Database size={16} aria-hidden="true" /> {t("New dataset")}
          </button>
        )}
        {creator && show !== "datasets" && (
          <button onClick={() => setCreating(creating ? null : published.length ? "existing" : "new")} aria-expanded={!!creating}>
            <Plus size={16} aria-hidden="true" /> {t("New dashboard")}
          </button>
        )}
      </div>
      {settings && !connected && creator && (
        <div className="notice">
          {account.me.platform_admin ? (
            <>
              {t("Connect your AI provider first:")} <Link to="/settings">{t("open Settings")}</Link> {t("and run the connection test.")}
            </>
          ) : (
            t("The AI isn't connected yet. The people running datawazi need to set it up.")
          )}
        </div>
      )}

      {show !== "dashboards" &&
        alerts.map((a) => (
          <div key={a.dataset_id} className="notice refresh-alert" role="status">
            <strong>
              {t(
                `{name}: the ${a.trigger === "schedule" ? "scheduled refresh" : "refresh after a linked table changed"} ${a.status === "failed" ? "failed" : "passed with warnings"}`,
                { name: a.dataset_name },
              )}
            </strong>{" "}
            ({new Date(a.at).toLocaleString()}). {a.text} <Link to={`/datasets/${a.dataset_id}/refresh`}>{t("Open Refresh")}</Link>
          </div>
        ))}
      {creator && show === "all" && datasets && dashboards && <NextSteps datasets={datasets} dashboards={dashboards} />}
      {creator && connected && show === "all" && <QuickStart />}

      {creating && (
        <NewDashboard
          choice={creating}
          onChoice={setCreating}
          published={published}
          ai={!!settings?.has_api_key}
          onCreated={(path) => navigate(path)}
          onError={(m) => toast(m, "error")}
        />
      )}

      {show !== "datasets" && (
        <section className="home-section">
          {show === "all" && <h2 className="section-title">{t("Dashboards")}</h2>}
          {dashboards === null ? (
            <Skeleton lines={3} height={52} />
          ) : dashboards.length === 0 ? (
            <EmptyState icon={<LayoutDashboard size={28} />} title={creator ? t("No dashboards yet") : t("Nothing published yet")}>
              {creator
                ? t("A dashboard is built by the AI from a dataset's measures. Click New dashboard to make the first one.")
                : t("Dashboards show up here once someone in your team publishes them.")}
            </EmptyState>
          ) : (
            <table className="catalog">
              <thead>
                <tr>
                  <th>{t("Name")}</th>
                  <th>{t("Dataset")}</th>
                  <th className="num">{t("Pages")}</th>
                  {creator && <th>{t("Status")}</th>}
                  <th>{t("Updated")}</th>
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
                    <td className="muted">{d.dataset ? <span className="cell-clip" title={d.dataset.name}>{d.dataset.name}</span> : <span className="bad">{t("Removed")}</span>}</td>
                    <td className="num">{d.pages}</td>
                    {creator && (
                      <td>
                        <span className={`status-dot ${d.published ? (d.unpublished_changes ? "warn" : "ok") : ""}`}>
                          {t(d.published ? (d.unpublished_changes ? "Draft changes" : "Published") : "Draft")}
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
          {show === "all" && <h2 className="section-title">{t("Datasets")}</h2>}
          <p className="muted small section-hint">{t("Load, clean and model data once, then reuse it in any number of dashboards.")}</p>
          {datasets === null ? (
            <Skeleton lines={3} height={52} />
          ) : datasets.length === 0 ? (
            <EmptyState
              icon={<Database size={28} />}
              title={t("No datasets yet")}
              action={
                <button onClick={newDataset}>
                  <Plus size={16} aria-hidden="true" /> New dataset
                </button>
              }
            >
              {t("Start with a CSV, an Excel file, a Google Sheet or the sample data.")}
            </EmptyState>
          ) : (
            <table className="catalog">
              <thead>
                <tr>
                  <th>{t("Name")}</th>
                  <th className="num">{t("Tables")}</th>
                  <th className="num">{t("Steps")}</th>
                  <th className="num">{t("Measures")}</th>
                  <th>{t("Status")}</th>
                  <th className="num">{t("Used by")}</th>
                  <th>{t("Updated")}</th>
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
                        {d.status.published
                          ? t(d.status.unpublished_changes ? "Draft changes, v{v} live" : "Published v{v}", { v: d.status.version ?? "" })
                          : t("Not published")}
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
  ai: boolean;
  onCreated: (path: string) => void;
  onError: (message: string) => void;
}) {
  const { choice, published } = props;
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState(published[0]?.id ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [templates, setTemplates] = useState<DashboardTemplate[]>([]);
  const [start, setStart] = useState<string>(props.ai ? "choose" : "blank"); // "blank", "choose" or a template id
  const [description, setDescription] = useState("");

  useEffect(() => {
    if (props.ai) reportApi.templates().then(setTemplates).catch(() => setTemplates([]));
  }, [props.ai]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(t("Creating…"));
    try {
      if (choice === "existing") {
        const dashboard = await dashboardsApi.create(name || "Untitled dashboard", datasetId);
        const brief: DesignBrief | null =
          start === "blank" && !description.trim()
            ? null
            : { ...(start === "choose" ? { choose: true } : start !== "blank" ? { template: start } : {}), description: description.trim() || undefined };
        if (props.ai && brief) {
          setBusy(t("Designing the pages…"));
          try {
            await reportApi.ai(dashboard.id, brief);
          } catch (e) {
            props.onError(`The dashboard was made, but the AI couldn't design it: ${(e as Error).message}`);
          }
        }
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
      setBusy(null);
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
          <strong>{t("Use existing data")}</strong>
          <span className="muted small">
            {published.length ? t("Build on a published dataset; it stays in sync.") : t("No published datasets yet.")}
          </span>
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={choice === "new"}
          className={`choice ${choice === "new" ? "on" : ""}`}
          onClick={() => props.onChoice("new")}
        >
          <strong>{t("Bring new data")}</strong>
          <span className="muted small">{t("Load files or a Google Sheet; the AI cleans and models it first.")}</span>
        </button>
      </div>
      <div className="inline-form">
        <label className="field grow">
          <span>{t("Dashboard name")}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Sales review" />
        </label>
        {choice === "existing" && (
          <label className="field">
            <span>{t("Dataset")}</span>
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
        <button type="submit" className="align-end" disabled={!!busy || (choice === "existing" && !datasetId)}>
          {busy ?? t(choice === "existing" ? "Create dashboard" : "Start with data")}
        </button>
      </div>
      {choice === "existing" && props.ai && (
        <fieldset className="template-picker" disabled={!!busy}>
          <legend>{t("Start with")}</legend>
          <div className="template-grid" role="radiogroup" aria-label={t("Start with")}>
            {[
              { id: "choose", name: t("Let the AI choose"), description: t("It picks the template that fits this data and fills it.") },
              ...templates,
              { id: "blank", name: t("A blank page"), description: t("Add visuals yourself, or ask the AI later.") },
            ].map((tpl) => (
              <button
                key={tpl.id}
                type="button"
                role="radio"
                aria-checked={start === tpl.id}
                className={`template-card ${start === tpl.id ? "on" : ""}`}
                onClick={() => setStart(tpl.id)}
              >
                <strong>{tpl.name}</strong>
                <span className="muted small">{tpl.description}</span>
                {"pages" in tpl && <span className="template-pages small">{(tpl as DashboardTemplate).pages.join(" / ")}</span>}
              </button>
            ))}
          </div>
          <label className="field">
            <span>{t("What should it show? (optional)")}</span>
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("For example: a monthly view for branch managers, focused on which products are slowing down")}
            />
          </label>
        </fieldset>
      )}
    </form>
  );
}

/** "3 hours ago", "yesterday", "12 Mar": how long ago, as a data catalog shows it. */
function ago(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return t("Just now");
  if (seconds < 3600) return t("{n} min ago", { n: Math.floor(seconds / 60) });
  if (seconds < 86400) return t("{n} h ago", { n: Math.floor(seconds / 3600) });
  if (seconds < 172800) return t("Yesterday");
  if (seconds < 604800) return t("{n} days ago", { n: Math.floor(seconds / 86400) });
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
