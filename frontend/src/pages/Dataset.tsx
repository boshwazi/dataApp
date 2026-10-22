import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext, useParams, useSearchParams } from "react-router-dom";
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
import { BadgeCheck, FileDown, ScrollText, GitBranch, History, MoreHorizontal, Pencil, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { ActivityDrawer } from "../components/ActivityDrawer";
import { InstructionsDialog } from "../components/InstructionsDialog";
import { ChatPanel, type ChatHandle } from "../components/ChatPanel";
import { Menu, TopbarActions, useCrumbs, useDialogs, useShortcut, useToast } from "../components/ui";

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
      return localStorage.getItem(CHAT_KEY) === "open";
    } catch {
      return false;
    }
  });
  const [showActivity, setShowActivity] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);
  const [version, setVersion] = useState(0);
  const chat = useRef<ChatHandle | null>(null);
  const dialogs = useDialogs();
  const toast = useToast();
  const location = useLocation();
  const navigate = useNavigate();
  const stage = STAGES.find((s) => location.pathname.endsWith(`/${s.path}`)) ?? STAGES[0];
  const extra = location.pathname.endsWith("/refresh")
    ? "Refresh"
    : location.pathname.endsWith("/lineage")
      ? "Lineage"
      : location.pathname.endsWith("/security")
        ? "Security"
        : null;
  const onRefresh = extra !== null;
  const showChat = chatOpen && !onRefresh;
  const thread = useMemo(() => datasetChat(datasetId, stage.key), [datasetId, stage.key]);

  useCrumbs(
    dataset && [
      { label: "Datasets", to: "/datasets" },
      { label: dataset.name, to: `/datasets/${dataset.id}/${stage.path}` },
      ...(extra ? [{ label: extra }] : []),
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

  useShortcut("i", () => !onRefresh && toggleChat());

  if (error && !dataset) return <p className="bad">{error}</p>;
  if (!dataset) return <p className="muted">Loading…</p>;

  const { status, progress, stale } = dataset;
  // Coming from "Bring new data": once published, go back to the dashboard waiting for it.
  const waiting = params.get("for");
  const nextDashboard = waiting ?? (dataset.dashboards.length === 1 ? dataset.dashboards[0].id : null);
  const done: Record<DatasetStage, boolean> = { get_data: progress.sources > 0, transform: progress.steps > 0, model: progress.measures > 0 };
  const statusText = status.published
    ? status.unpublished_changes
      ? `Draft, v${status.version} is live`
      : `v${status.version} is live`
    : "Not published yet";

  return (
    <div className="workbench">
      <TopbarActions>
        <nav className="stage-tabs" aria-label="Dataset stages">
          {STAGES.map((s) => (
            <NavLink key={s.key} to={s.path} title={stale[s.key] ? `${stale[s.key]} changed since this stage was updated` : undefined}>
              <span className={`stage-dot ${stale[s.key] ? "warn" : done[s.key] ? "ok" : ""}`} aria-hidden="true" />
              {s.name}
            </NavLink>
          ))}
        </nav>
        <span className="topbar-grow" />
        <span className={`topbar-status ${status.published ? (status.unpublished_changes ? "warn" : "ok") : ""}`}>
          {dataset.certified && <BadgeCheck size={14} className="certified-icon" aria-label="Certified" />}
          {statusText}
        </span>
        {!onRefresh && (
          <button className={`ai-button ${chatOpen ? "on" : ""}`} onClick={toggleChat} aria-pressed={chatOpen} title="Ask AI (Ctrl I)">
            <Sparkles size={15} aria-hidden="true" /> Ask AI <kbd>Ctrl I</kbd>
          </button>
        )}
        <Menu
          label="More actions"
          icon={<MoreHorizontal size={18} />}
          items={[
            { label: "Rename", icon: <Pencil size={16} aria-hidden="true" />, onSelect: rename },
            { label: "AI instructions", icon: <ScrollText size={16} aria-hidden="true" />, onSelect: () => setShowInstructions(true) },
            { label: "Refresh data", icon: <RefreshCw size={16} aria-hidden="true" />, onSelect: () => navigate("refresh") },
            { label: "Lineage", icon: <GitBranch size={16} aria-hidden="true" />, onSelect: () => navigate("lineage") },
            { label: "Security (who sees which rows)", icon: <ShieldCheck size={16} aria-hidden="true" />, onSelect: () => navigate("security") },
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
        <button className="primary-compact" onClick={() => publish(false)} disabled={publishing || !dataset.sources.length || (status.published && !status.unpublished_changes)}>
          {publishing ? "Publishing…" : "Publish"}
        </button>
      </TopbarActions>

      {(error || notice || problems || (!onRefresh && stale[stage.key])) && (
        <div className="workbench-notices">
      {error && <p className="bad">{error}</p>}
      {notice && (
        <div className="notice small">
          {notice}{" "}
          {nextDashboard ? (
            <Link to={`/dashboards/${nextDashboard}`}>Continue to the dashboard</Link>
          ) : (
            dataset.dashboards.length === 0 && <Link to="/dashboards?new=1">Build a dashboard</Link>
          )}
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

      {!onRefresh && stale[stage.key] && (
        <div className="notice stale-banner small">
          <span>
            <strong>{stale[stage.key]}</strong> changed since {stage.name} was last updated.
          </span>
          <span className="actions">
            <button className="secondary" onClick={() => askToCheck(stale[stage.key]!)}>
              Ask AI to check
            </button>
            <button className="link small" onClick={markReviewed}>
              Mark as reviewed
            </button>
          </span>
        </div>
      )}
        </div>
      )}

      <div className="workbench-body">
        <Outlet key={version} context={{ project: dataset, refresh } satisfies DatasetContext} />
      </div>
        {showChat && (
          <div className="ai-dock" role="dialog" aria-label={`${stage.name} AI`}>
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
          </div>
        )}
      {showInstructions && (
        <InstructionsDialog
          title="AI instructions for this dataset"
          scope="this dataset (cleaning, modelling and its chats)"
          load={async () => dataset.instructions ?? ""}
          save={async (text) => setDataset(await api.setInstructions(dataset.id, text))}
          onClose={() => setShowInstructions(false)}
        />
      )}
      {showActivity && <ActivityDrawer load={() => activityApi.dataset(datasetId)} onClose={() => setShowActivity(false)} />}
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
