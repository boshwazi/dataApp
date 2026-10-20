import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useOutletContext, useParams, useSearchParams } from "react-router-dom";
import {
  activityApi,
  api,
  ApiError,
  datasetChat,
  type BrokenVisual,
  type Dataset as DatasetData,
  type DatasetStage,
} from "../api";
import { ActivityDrawer } from "../components/ActivityDrawer";
import { ChatPanel, type ChatHandle } from "../components/ChatPanel";

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
  const location = useLocation();
  const stage = STAGES.find((s) => location.pathname.endsWith(`/${s.path}`)) ?? STAGES[0];
  const onRefresh = location.pathname.endsWith("/refresh") || location.pathname.endsWith("/lineage");
  const showChat = chatOpen && !onRefresh;
  const thread = useMemo(() => datasetChat(datasetId, stage.key), [datasetId, stage.key]);

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
      setDataset(await api.certify(dataset.id, !dataset.certified));
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
        <span className="kind-label">Dataset</span>
        <h1>{dataset.name}</h1>
        <button className="link" onClick={rename}>
          Rename
        </button>
        <span className="spacer" />
        <button className="secondary" onClick={() => setShowActivity(true)}>
          Activity
        </button>
        <button className="secondary" onClick={toggleChat} aria-pressed={chatOpen}>
          {chatOpen ? "Hide chat" : "AI chat"}
        </button>
        {dataset.certified && <span className="badge ok-badge" title="A trusted, shared dataset">✓ Certified</span>}
        <button className="link small" onClick={toggleCertified}>
          {dataset.certified ? "Remove certification" : "Certify"}
        </button>
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
      {dataset.linked_by.length > 0 && (
        <p className="muted small used-by">
          Linked by:{" "}
          {dataset.linked_by.map((d, i) => (
            <span key={d.id}>
              {i > 0 && ", "}
              <Link to={`/datasets/${d.id}/get-data`}>{d.name}</Link> ({d.tables.join(", ")})
            </span>
          ))}
        </p>
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

      <div className={`project-body ${showChat ? "with-chat" : ""}`}>
        <nav className="stage-rail" aria-label="Stages">
          {STAGES.map((s, i) => (
            <NavLink key={s.path} to={s.path}>
              <span className="step">{i + 1}</span>
              {s.name}
              {dataset.stale[s.key] && (
                <span className="stale-dot" title={`${dataset.stale[s.key]} changed since this stage was updated`}>
                  needs re-run
                </span>
              )}
            </NavLink>
          ))}
          <NavLink to="refresh" className="rail-extra">
            <span className="step">↻</span>
            Refresh
          </NavLink>
          <NavLink to="lineage">
            <span className="step">⇢</span>
            Lineage
          </NavLink>
          <p className="rail-hint muted small">Publish when the model is ready. Dashboards read the published version.</p>
        </nav>
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

export function PublishBadge({ published, version, changes }: { published: boolean; version?: number | null; changes: boolean }) {
  if (!published) return <span className="badge">Not published</span>;
  return (
    <span className={`badge ${changes ? "warn" : "ok-badge"}`}>
      {changes ? `Draft changes · v${version ?? ""} live` : `Published${version ? ` v${version}` : ""}`}
    </span>
  );
}
