import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useOutletContext, useParams, useSearchParams } from "react-router-dom";
import {
  activityApi,
  api,
  ApiError,
  datasetChat,
  powerbiApi,
  type BrokenVisual,
  type Dataset as DatasetData,
  type DatasetStage,
} from "../api";
import { BadgeCheck, Check, FileDown, GitBranch, History, MessageSquare, MoreHorizontal, Pencil, RefreshCw } from "lucide-react";
import { ActivityDrawer } from "../components/ActivityDrawer";
import { ChatPanel, type ChatHandle } from "../components/ChatPanel";
import { Menu, useCrumbs, useDialogs, useToast } from "../components/ui";

// The dataset half of the pipeline, in Power BI order. Dashboards build on what is published here.
const STAGES: { path: string; key: DatasetStage; name: string; suggestions: string[] }[] = [
  {
    path: "get-data",
    key: "get_data",
    name: "Get Data",
    suggestions: ["What does each table contain?", "Which table has the most data quality issues?"],
  },
  {
    path: "transform",
    key: "transform",
    name: "Transform",
    suggestions: ["Treat blank regions as Unknown", "Which columns still have issues?", "Explain the steps on the main table"],
  },
  {
    path: "model",
    key: "model",
    name: "Model",
    suggestions: ["Add a profit margin measure", "How are the tables connected?", "Add a measure for average order value"],
  },
];

const CHAT_KEY = "chatOpen";

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
  const [chatOpen, setChatOpen] = useState(() => {
    try {
      return localStorage.getItem(CHAT_KEY) !== "closed";
    } catch {
      return true;
    }
  });
  const [showActivity, setShowActivity] = useState(false);
  const [version, setVersion] = useState(0);
  const chat = useRef<ChatHandle | null>(null);
  const dialogs = useDialogs();
  const toast = useToast();
  const location = useLocation();
  const stage = STAGES.find((s) => location.pathname.endsWith(`/${s.path}`)) ?? STAGES[0];
  const extra = location.pathname.endsWith("/refresh") ? "Refresh" : location.pathname.endsWith("/lineage") ? "Lineage" : null;
  const onRefresh = extra !== null;
  const showChat = chatOpen && !onRefresh;
  const thread = useMemo(() => datasetChat(datasetId, stage.key), [datasetId, stage.key]);

  useCrumbs(
    dataset && [
      { label: "Datasets", to: "/datasets" },
      { label: dataset.name, to: `/datasets/${dataset.id}/get-data` },
      { label: extra ?? stage.name },
    ],
  );

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
    const name = await dialogs.prompt({ title: "Rename dataset", label: "Name", initial: dataset.name, confirmLabel: "Rename" });
    if (!name || name === dataset.name) return;
    try {
      setDataset(await api.renameDataset(dataset.id, name));
      toast("Dataset renamed");
    } catch (e) {
      toast((e as Error).message, "error");
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

  function toggleChat() {
    setChatOpen((open) => {
      try {
        localStorage.setItem(CHAT_KEY, open ? "closed" : "open");
      } catch {
        // private mode
      }
      return !open;
    });
  }

  async function toggleCertified() {
    if (!dataset) return;
    try {
      const next = await api.certify(dataset.id, !dataset.certified);
      setDataset(next);
      toast(next.certified ? "Certified: others can link to it as a trusted source" : "Certification removed");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function markReviewed() {
    try {
      setDataset(await activityApi.stageReviewed(datasetId, stage.key));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function askToCheck(changedStage: string) {
    if (!chatOpen) toggleChat();
    // The panel may only mount now, so send on the next tick.
    setTimeout(
      () =>
        chat.current?.send(
          `${changedStage} changed since this stage was last updated. Check this stage against it and fix anything that no longer fits.`,
        ),
      0,
    );
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
        <div className="title-block">
          <span className="kind-label">Dataset</span>
          <h1>
            {dataset.name}
            {dataset.certified && <BadgeCheck size={20} className="certified-icon" aria-label="Certified" />}
          </h1>
          <button className="icon" onClick={rename} aria-label="Rename dataset" title="Rename">
            <Pencil size={15} />
          </button>
        </div>
        <span className="spacer" />
        {!onRefresh && (
          <button className="secondary" onClick={toggleChat} aria-pressed={chatOpen}>
            <MessageSquare size={16} aria-hidden="true" /> AI chat
          </button>
        )}
        <Menu
          label="More actions"
          icon={<MoreHorizontal size={18} />}
          items={[
            { label: "Activity", icon: <History size={16} aria-hidden="true" />, onSelect: () => setShowActivity(true) },
            {
              label: "Download for Power BI",
              icon: <FileDown size={16} aria-hidden="true" />,
              onSelect: () =>
                void powerbiApi
                  .download(dataset.id, dataset.name)
                  .then(() => toast("Downloaded a Power BI project (.zip). Unzip it and open the .pbip in Power BI Desktop."))
                  .catch((e) => toast((e as Error).message, "error")),
            },
            {
              label: dataset.certified ? "Remove certification" : "Certify as trusted",
              icon: <BadgeCheck size={16} aria-hidden="true" />,
              onSelect: toggleCertified,
            },
          ]}
        />
        <button onClick={() => publish(false)} disabled={publishing || !dataset.sources.length || (status.published && !status.unpublished_changes)}>
          {publishing ? "Publishing…" : status.published ? "Publish changes" : "Publish"}
        </button>
      </header>
      <p className="meta-line">
        <span className={`status-dot ${status.published ? (status.unpublished_changes ? "warn" : "ok") : ""}`}>
          {status.published
            ? status.unpublished_changes
              ? `Draft changes, v${status.version} is live`
              : `Published v${status.version}`
            : "Not published yet"}
        </span>
        {dataset.dashboards.length > 0 && (
          <span>
            Used by{" "}
            {dataset.dashboards.map((d, i) => (
              <span key={d.id}>
                {i > 0 && ", "}
                <Link to={`/dashboards/${d.id}`}>{d.name}</Link>
              </span>
            ))}
          </span>
        )}
        {dataset.linked_by.length > 0 && (
          <span>
            Linked by{" "}
            {dataset.linked_by.map((d, i) => (
              <span key={d.id}>
                {i > 0 && ", "}
                <Link to={`/datasets/${d.id}/get-data`}>{d.name}</Link> ({d.tables.join(", ")})
              </span>
            ))}
          </span>
        )}
      </p>

      <Pipeline
        dataset={dataset}
        current={onRefresh ? null : stage.key}
        publishing={publishing}
        onPublish={() => publish(false)}
        nextDashboard={nextDashboard}
      />

      {error && <p className="bad">{error}</p>}
      {notice && (
        <div className="notice small">
          {notice}{" "}
          {nextDashboard && <Link to={`/dashboards/${nextDashboard}`}>Continue to the dashboard</Link>}
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
                <Link to={`/dashboards/${p.dashboard_id}`}>{p.dashboard}</Link> ({p.version}), {p.page}, {p.visual}: {p.error}
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

      <div className={`project-body ${showChat ? "with-chat" : ""}`}>
        <section className="stage">
          {!onRefresh && dataset.stale[stage.key] && (
            <div className="notice stale-banner small">
              <span>
                <strong>{dataset.stale[stage.key]}</strong> changed since {stage.name} was last updated.
              </span>
              <span className="actions">
                <button className="secondary" onClick={() => askToCheck(dataset.stale[stage.key]!)}>
                  Ask AI to check
                </button>
                <button className="link small" onClick={markReviewed}>
                  Mark as reviewed
                </button>
              </span>
            </div>
          )}
          <Outlet key={version} context={{ project: dataset, refresh } satisfies DatasetContext} />
        </section>
        {showChat && (
          <ChatPanel
            title={`${stage.name} AI`}
            thread={thread}
            suggestions={stage.suggestions}
            onClose={toggleChat}
            onApplied={() => {
              setVersion((v) => v + 1); // reload the stage view with the AI's changes
              void refresh();
            }}
            handle={(h) => (chat.current = h)}
          />
        )}
      </div>
      {showActivity && <ActivityDrawer load={() => activityApi.dataset(datasetId)} onClose={() => setShowActivity(false)} />}
    </div>
  );
}

/** Get Data → Transform → Model → Publish → Dashboard, with what is done ticked and one clear next step. */
function Pipeline(props: {
  dataset: DatasetData;
  current: DatasetStage | null;
  publishing: boolean;
  onPublish: () => void;
  nextDashboard: string | null;
}) {
  const { dataset, current, nextDashboard } = props;
  const { progress, status, stale } = dataset;
  const live = status.published && !status.unpublished_changes;
  const dashboardTo = nextDashboard ? `/dashboards/${nextDashboard}` : "/dashboards?new=1";
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  const steps = [
    { key: "get_data", name: "Get Data", to: "get-data", done: progress.sources > 0, detail: plural(progress.sources, "table", "tables") },
    { key: "transform", name: "Transform", to: "transform", done: progress.steps > 0, detail: plural(progress.steps, "step", "steps") },
    { key: "model", name: "Model", to: "model", done: progress.measures > 0, detail: plural(progress.measures, "measure", "measures") },
    {
      key: "publish",
      name: "Publish",
      to: null,
      done: live,
      detail: status.published ? (status.unpublished_changes ? "Changes waiting" : `v${status.version} live`) : "Not yet",
    },
    {
      key: "dashboard",
      name: "Dashboard",
      to: dashboardTo,
      done: dataset.dashboards.length > 0,
      detail: dataset.dashboards.length ? `${dataset.dashboards.length} using it` : "None yet",
    },
  ];

  let next: { label: string; to?: string; action?: () => void; disabled?: boolean } | null = null;
  if (current === "get_data") next = { label: "Next: Transform", to: "transform", disabled: !progress.sources };
  else if (current === "transform") next = { label: "Next: Model", to: "model", disabled: !progress.sources };
  else if (current === "model")
    next = live
      ? { label: nextDashboard ? "Open the dashboard" : "Build a dashboard", to: dashboardTo }
      : { label: props.publishing ? "Publishing…" : "Next: Publish", action: props.onPublish, disabled: props.publishing || !progress.sources };

  return (
    <div className="pipeline-bar">
      <ol className="pipeline" aria-label="Dataset steps">
        {steps.map((s, i) => {
          const here = s.key === current;
          const needs = s.key in stale ? stale[s.key as DatasetStage] : null;
          const body = (
            <>
              <span className={`pipe-dot ${s.done ? "done" : ""}`} aria-hidden="true">
                {s.done ? <Check size={13} strokeWidth={3} /> : i + 1}
              </span>
              <span className="pipe-text">
                <span className="pipe-name">{s.name}</span>
                <span className="pipe-detail">{needs ? "Needs re-run" : s.detail}</span>
              </span>
            </>
          );
          return (
            <li key={s.key} className={`${here ? "here" : ""} ${needs ? "needs" : ""}`}>
              {s.to === null ? (
                <span className="pipe-step static" title={s.done ? "Published and up to date" : "Publish makes the dataset available to dashboards"}>
                  {body}
                </span>
              ) : s.key === "dashboard" ? (
                <Link className="pipe-step" to={s.to}>
                  {body}
                </Link>
              ) : (
                <Link className="pipe-step" to={s.to} aria-current={here ? "step" : undefined}>
                  {body}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
      <nav className="pipeline-extra" aria-label="Dataset tools">
        <NavLink to="refresh">
          <RefreshCw size={15} aria-hidden="true" /> Refresh
        </NavLink>
        <NavLink to="lineage">
          <GitBranch size={15} aria-hidden="true" /> Lineage
        </NavLink>
      </nav>
      {next &&
        (next.to ? (
          next.disabled ? (
            <button className="next-step" disabled>
              {next.label}
            </button>
          ) : (
            <Link className="button next-step" to={next.to}>
              {next.label}
            </Link>
          )
        ) : (
          <button className="next-step" onClick={next.action} disabled={next.disabled}>
            {next.label}
          </button>
        ))}
    </div>
  );
}

export function PublishBadge({ published, version, changes }: { published: boolean; version?: number | null; changes: boolean }) {
  if (!published) return <span className="badge">Not published</span>;
  return (
    <span className={`badge ${changes ? "warn" : "ok-badge"}`}>
      {changes ? `Draft changes (v${version ?? ""} live)` : `Published${version ? ` v${version}` : ""}`}
    </span>
  );
}
