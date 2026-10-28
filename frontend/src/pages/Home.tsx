import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, ArrowUpDown, BadgeCheck, Database, LayoutDashboard, Plus, Search, Trash2 } from "lucide-react";
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
import { MiniPage } from "../components/miniPage";
import { recentlyOpened } from "../components/recent";
import { t } from "../i18n";

type Choice = "existing" | "new";
type Show = "all" | "datasets" | "dashboards";
type Sort = { key: string; dir: 1 | -1 };
type StatusFilter = "all" | "published" | "draft";

/** How many rows each list shows on Home; the Datasets and Dashboards pages show all of them. */
const HOME_ROWS = 6;

const DASHBOARD_KEYS: Record<string, (d: DashboardSummary) => string | number> = {
  name: (d) => d.name.toLowerCase(),
  dataset: (d) => d.dataset?.name.toLowerCase() ?? "",
  pages: (d) => d.pages,
  status: (d) => (d.published ? (d.unpublished_changes ? 1 : 2) : 0),
  updated: (d) => d.updated_at,
};

const DATASET_KEYS: Record<string, (d: Dataset) => string | number> = {
  name: (d) => d.name.toLowerCase(),
  tables: (d) => d.progress.sources,
  steps: (d) => d.progress.steps,
  measures: (d) => d.progress.measures,
  status: (d) => (d.status.published ? (d.status.unpublished_changes ? 1 : 2) : 0),
  used: (d) => d.dashboards.length,
  updated: (d) => d.updated_at,
};

function sorted<T>(rows: T[], keys: Record<string, (r: T) => string | number>, sort: Sort): T[] {
  const key = keys[sort.key];
  return [...rows].sort((a, b) => {
    const x = key(a);
    const y = key(b);
    return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
  });
}

/** Names sort A to Z first; numbers, statuses and dates sort largest or newest first. */
function nextSort(current: Sort, key: string): Sort {
  if (current.key === key) return { key, dir: current.dir === 1 ? -1 : 1 };
  return { key, dir: key === "name" || key === "dataset" ? 1 : -1 };
}

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
  const [dashboardSort, setDashboardSort] = useState<Sort>({ key: "updated", dir: -1 });
  const [datasetSort, setDatasetSort] = useState<Sort>({ key: "updated", dir: -1 });
  const [dashboardStatus, setDashboardStatus] = useState<StatusFilter>("all");
  const [datasetStatus, setDatasetStatus] = useState<StatusFilter>("all");
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
  const overview = show === "all";
  const keepDashboard = (d: DashboardSummary) =>
    dashboardStatus === "all" || (dashboardStatus === "published" ? d.published : !d.published || d.unpublished_changes);
  const keepDataset = (d: Dataset) =>
    datasetStatus === "all" || (datasetStatus === "published" ? d.status.published : !d.status.published || d.status.unpublished_changes);
  const allDashboards = sorted((dashboards ?? []).filter((d) => match(d.name) && keepDashboard(d)), DASHBOARD_KEYS, dashboardSort);
  const allDatasets = sorted((datasets ?? []).filter((d) => match(d.name) && keepDataset(d)), DATASET_KEYS, datasetSort);
  const shownDashboards = overview && !filter ? allDashboards.slice(0, HOME_ROWS) : allDashboards;
  const shownDatasets = overview && !filter ? allDatasets.slice(0, HOME_ROWS) : allDatasets;
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

      {wanted === "all" && dashboards && dashboards.length > 0 && !filter && <RecentDashboards dashboards={dashboards} creator={creator} />}

      {show !== "datasets" && (
        <section className="home-section">
          <div className="list-head">
            {show === "all" && <h2 className="section-title">{t("Dashboards")}</h2>}
            {!overview && creator && (
              <StatusTabs value={dashboardStatus} onChange={setDashboardStatus} draftLabel={t("Drafts")} label={t("Dashboards")} />
            )}
          </div>
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
                  <SortTh label={t("Name")} k="name" sort={dashboardSort} onSort={setDashboardSort} />
                  <SortTh label={t("Dataset")} k="dataset" sort={dashboardSort} onSort={setDashboardSort} />
                  <SortTh label={t("Pages")} k="pages" sort={dashboardSort} onSort={setDashboardSort} num />
                  {creator && <SortTh label={t("Status")} k="status" sort={dashboardSort} onSort={setDashboardSort} />}
                  <SortTh label={t("Updated")} k="updated" sort={dashboardSort} onSort={setDashboardSort} />
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
          {dashboards && dashboards.length > 0 && allDashboards.length === 0 && <p className="muted small list-empty">{t("No dashboards match.")}</p>}
          {overview && !filter && allDashboards.length > HOME_ROWS && (
            <Link className="view-all" to="/dashboards">
              {t("View all {n} dashboards", { n: allDashboards.length })}
            </Link>
          )}
        </section>
      )}

      {show !== "dashboards" && (
        <section className="home-section">
          {show === "all" && <h2 className="section-title">{t("Datasets")}</h2>}
          <p className="muted small section-hint">{t("Load, clean and model data once, then reuse it in any number of dashboards.")}</p>
          {!overview && (
            <div className="list-head">
              <StatusTabs value={datasetStatus} onChange={setDatasetStatus} draftLabel={t("Not published or changed")} label={t("Datasets")} />
            </div>
          )}
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
                  <SortTh label={t("Name")} k="name" sort={datasetSort} onSort={setDatasetSort} />
                  <SortTh label={t("Tables")} k="tables" sort={datasetSort} onSort={setDatasetSort} num />
                  <SortTh label={t("Steps")} k="steps" sort={datasetSort} onSort={setDatasetSort} num />
                  <SortTh label={t("Measures")} k="measures" sort={datasetSort} onSort={setDatasetSort} num />
                  <SortTh label={t("Status")} k="status" sort={datasetSort} onSort={setDatasetSort} />
                  <SortTh label={t("Used by")} k="used" sort={datasetSort} onSort={setDatasetSort} num />
                  <SortTh label={t("Updated")} k="updated" sort={datasetSort} onSort={setDatasetSort} />
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
          {datasets && datasets.length > 0 && allDatasets.length === 0 && <p className="muted small list-empty">{t("No datasets match.")}</p>}
          {overview && !filter && allDatasets.length > HOME_ROWS && (
            <Link className="view-all" to="/datasets">
              {t("View all {n} datasets", { n: allDatasets.length })}
            </Link>
          )}
        </section>
      )}
    </div>
  );
}

/** A sortable column header: the button sorts, aria-sort says how it's sorted now. */
function SortTh({ label, k, sort, onSort, num }: { label: string; k: string; sort: Sort; onSort: (s: Sort) => void; num?: boolean }) {
  const active = sort.key === k;
  return (
    <th className={num ? "num" : ""} aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button className={`sort-th ${active ? "on" : ""}`} onClick={() => onSort(nextSort(sort, k))}>
        {label}
        {active ? (
          sort.dir === 1 ? <ArrowUp size={12} aria-hidden="true" /> : <ArrowDown size={12} aria-hidden="true" />
        ) : (
          <ArrowUpDown size={12} aria-hidden="true" className="sort-idle" />
        )}
      </button>
    </th>
  );
}

function StatusTabs(props: { value: StatusFilter; onChange: (v: StatusFilter) => void; draftLabel: string; label: string }) {
  const options: [StatusFilter, string][] = [
    ["all", t("All")],
    ["published", t("Published")],
    ["draft", props.draftLabel],
  ];
  return (
    <div className="segmented compact status-tabs" role="group" aria-label={t("Show {what}", { what: props.label })}>
      {options.map(([v, l]) => (
        <button key={v} className={props.value === v ? "on" : ""} aria-pressed={props.value === v} onClick={() => props.onChange(v)}>
          {l}
        </button>
      ))}
    </div>
  );
}

/** Up to four dashboards to pick up again: the ones opened here lately first, then the ones changed lately. */
function RecentDashboards({ dashboards, creator }: { dashboards: DashboardSummary[]; creator: boolean }) {
  const opened = new Map(recentlyOpened().map((o) => [o.id, o.at]));
  const recent = [...dashboards]
    .sort((a, b) => (opened.get(b.id) ?? "").localeCompare(opened.get(a.id) ?? "") || b.updated_at.localeCompare(a.updated_at))
    .slice(0, 4);
  return (
    <section className="home-section" aria-labelledby="recent-title">
      <h2 className="section-title" id="recent-title">
        {t("Recent dashboards")}
      </h2>
      <div className="recent-grid">
        {recent.map((d) => {
          const when = opened.get(d.id);
          return (
            <Link key={d.id} className="recent-card" to={`/dashboards/${d.id}`}>
              <span className="recent-preview">
                {d.preview?.visuals.length ? (
                  <MiniPage visuals={d.preview.visuals} />
                ) : (
                  <span className="recent-empty">
                    <LayoutDashboard size={22} aria-hidden="true" />
                  </span>
                )}
              </span>
              <span className="recent-body">
                <strong className="recent-name" title={d.name}>
                  {d.name}
                </strong>
                <span className="recent-meta">
                  {creator && (
                    <span className={`status-dot ${d.published ? (d.unpublished_changes ? "warn" : "ok") : ""}`}>
                      {t(d.published ? (d.unpublished_changes ? "Draft changes" : "Published") : "Draft")}
                    </span>
                  )}
                  <span className="muted">
                    {when ? t("Opened {when}", { when: lowerFirst(ago(when)) }) : t("Updated {when}", { when: lowerFirst(ago(d.updated_at)) })}
                  </span>
                </span>
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

/** "Yesterday" reads "opened yesterday" mid-sentence; dates such as "20 Oct 2026" stay as they are. */
function lowerFirst(text: string) {
  return /^[A-Z][a-z]/.test(text) ? text.charAt(0).toLowerCase() + text.slice(1) : text;
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
