import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Bell, Check, LayoutDashboard, Network, Sparkles, Table2, UploadCloud, Wand2, X } from "lucide-react";
import { api, type AutopilotRun, type AutopilotStepKey } from "../api";
import { useDataset } from "./Dataset";
import { t } from "../i18n";

const POLL_MS = 1500;

// Where each step's work can be seen (and changed) afterwards.
const STEP_AT: Record<AutopilotStepKey, string | null> = {
  read: "get-data",
  clean: "transform",
  model: "model",
  publish: "model",
  dashboard: null,
};
const STAGE_NAME: Record<string, string> = { "get-data": "Get Data", transform: "Transform", model: "Model" };

// What each step does, in plain words, for the "What's happening now" panel.
const STAGE_INFO: Record<AutopilotStepKey, { icon: ReactNode; title: string; text: string }> = {
  read: {
    icon: <Table2 size={22} />,
    title: "Reading your tables",
    text: "The AI looks at each table's columns and a few sample rows to understand what's in it.",
  },
  clean: {
    icon: <Wand2 size={22} />,
    title: "Cleaning the data",
    text: "Dates, prices, blanks and repeated rows are fixed. Each change is a step with its reason, so you can undo it.",
  },
  model: {
    icon: <Network size={22} />,
    title: "Building the model",
    text: "Tables are related, a date table is added, and measures such as revenue and orders are written and checked.",
  },
  publish: {
    icon: <UploadCloud size={22} />,
    title: "Publishing the dataset",
    text: "The checked version goes live, so dashboards read from it.",
  },
  dashboard: {
    icon: <LayoutDashboard size={22} />,
    title: "Designing a dashboard",
    text: "Pages and charts are chosen to answer the main questions in your data. It stays a draft until you publish it.",
  },
};

function clock(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Seconds since the run started, ticking while it runs. */
function useElapsed(run: AutopilotRun | null | undefined) {
  const [now, setNow] = useState(() => Date.now());
  const running = run?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  if (!run) return 0;
  const end = run.finished_at ? Date.parse(run.finished_at) : now;
  return (end - Date.parse(run.started_at)) / 1000;
}

export function Autopilot() {
  const { project, refresh } = useDataset();
  const [run, setRun] = useState<AutopilotRun | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [name, setName] = useState(`${project.name} dashboard`);
  const wasRunning = useRef(false);
  const elapsed = useElapsed(run);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    async function poll() {
      try {
        const { run: latest } = await api.autopilot(project.id);
        if (stopped) return;
        setRun(latest);
        if (latest?.status === "running") {
          wasRunning.current = true;
          timer = window.setTimeout(poll, POLL_MS);
        } else if (wasRunning.current) {
          wasRunning.current = false;
          void refresh(); // the dataset was cleaned, modelled and published meanwhile
        }
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    }
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
    // Polling restarts when a new run starts (its id changes).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id, run?.id]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      setRun(await api.startAutopilot(project.id, name.trim() || undefined));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  if (run === undefined) return <div className="autopilot" aria-busy="true" />;

  if (!run) {
    return (
      <div className="autopilot">
        <header className="autopilot-head">
          <h1>{t("Let datawazi build it for you")}</h1>
          <p className="muted">
            {t("Autopilot cleans your tables, builds the model, publishes the dataset and designs a dashboard. It accepts the AI's checked changes for you; the dashboard stays a draft until you publish it.")}
          </p>
        </header>
        <div className="autopilot-start">
          <label>
            {t("Dashboard name")}
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button onClick={start} disabled={starting || project.sources.length === 0}>
            <Sparkles size={16} aria-hidden="true" /> {starting ? t("Starting…") : t("Start Autopilot")}
          </button>
          {project.sources.length === 0 && (
            <p className="muted small">
              {t("Add some data first in")} <Link to="../get-data">{t("Get Data")}</Link>.
            </p>
          )}
        </div>
        {error && <p className="bad small">{error}</p>}
      </div>
    );
  }

  const running = run.status === "running";
  const done = run.steps.filter((s) => s.status === "done").length;
  const current = run.steps.find((s) => s.status === "running") ?? run.steps.find((s) => s.status === "pending");
  const stepCount = run.steps.length;

  return (
    <div className="autopilot">
      <header className="autopilot-head">
        <h1>
          {running ? t("Building your dashboard") : run.status === "done" ? t("Your dashboard is ready") : t("Autopilot stopped")}
        </h1>
        <p className="muted">
          {running
            ? t("Each step is done the way you would do it by hand, so you can open it and change anything later.")
            : run.status === "done"
              ? t("Everything Autopilot added is marked, so you can review it, change it in each step, or ask the AI.")
              : t("Nothing is lost: what finished is kept, and you can fix the step that stopped and run it again.")}
        </p>
      </header>

      <div className="ap-grid">
        <section className="ap-main" aria-label={t("Progress")}>
          <div className="ap-progress">
            <div className="ap-progress-text">
              <span>{t("{done} of {all} steps done", { done, all: stepCount })}</span>
              <span className="ap-clock" aria-label={t("Time taken")}>{clock(elapsed)}</span>
            </div>
            <div
              className={`ap-bar${run.status === "failed" || run.status === "stopped" ? " stopped" : ""}`}
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={stepCount}
              aria-valuenow={done}
              aria-label={t("Autopilot progress")}
            >
              <i style={{ width: `${(done / stepCount) * 100}%` }} />
            </div>
          </div>

          <ol className="autopilot-steps" aria-live="polite">
            {run.steps.map((step, i) => {
              const at = step.key === "dashboard" ? (run.dashboard_id ? `/dashboards/${run.dashboard_id}` : null) : STEP_AT[step.key];
              return (
                <li key={step.key} className={`autopilot-step is-${step.status}`}>
                  <StepMarker n={i + 1} status={step.status} />
                  <div className="autopilot-step-body">
                    <span className="autopilot-step-label">{t(step.label)}</span>
                    <span className="autopilot-step-detail">
                      {step.status === "running" ? step.detail || t("Working on it…") : step.status === "pending" ? t("Waiting") : step.detail}
                    </span>
                    {step.status === "running" && <span className="ap-working" aria-hidden="true" />}
                  </div>
                  {at && step.status !== "pending" && step.status !== "running" && (
                    <Link className="autopilot-step-open" to={at.startsWith("/") ? at : `../${at}`}>
                      {step.status === "failed" ? t("Fix it") : t("See what it did")}
                    </Link>
                  )}
                </li>
              );
            })}
          </ol>
        </section>

        <aside className="ap-side">
          {running && current && (
            <div className="ap-card ap-now">
              <span className="ap-now-icon" aria-hidden="true">{STAGE_INFO[current.key].icon}</span>
              <h2>{t("What's happening now")}</h2>
              <h3>{t(STAGE_INFO[current.key].title)}</h3>
              <p>{t(STAGE_INFO[current.key].text)}</p>
              <p className="ap-leave">
                <Bell size={15} aria-hidden="true" /> {t("You can leave this page. It keeps going, and you'll get a notification when it's done.")}
              </p>
            </div>
          )}

          {run.status === "done" && (
            <div className="ap-card ap-result">
              <span className="ap-result-icon" aria-hidden="true"><Check size={26} strokeWidth={3} /></span>
              <h2>{run.dashboard_name}</h2>
              <p>{run.steps.find((s) => s.key === "dashboard")?.detail}</p>
              <p className="muted small">{t("Built in {time}.", { time: clock(elapsed) })}</p>
              {run.dashboard_id && (
                <Link className="button ap-primary" to={`/dashboards/${run.dashboard_id}`}>
                  {t("View dashboard")}
                </Link>
              )}
              <div className="ap-links">
                <Link to="../transform">{t("Review the cleaning")}</Link>
                <Link to="../model">{t("Review the model")}</Link>
                <button className="link" onClick={start} disabled={starting}>{t("Run again")}</button>
              </div>
            </div>
          )}

          {(run.status === "failed" || run.status === "stopped") && (
            <div className="ap-card ap-failed" role="alert">
              <span className="ap-failed-icon" aria-hidden="true"><AlertTriangle size={22} /></span>
              <h2>{t("Autopilot stopped")}</h2>
              <p>{run.error}</p>
              <div className="ap-links">
                {run.fix_at && (
                  <Link className="button" to={`../${run.fix_at}`}>
                    {t("Open {stage}", { stage: STAGE_NAME[run.fix_at] })}
                  </Link>
                )}
                <button className="secondary" onClick={start} disabled={starting}>{t("Try again")}</button>
              </div>
            </div>
          )}
          {error && <p className="bad small">{error}</p>}
        </aside>
      </div>
    </div>
  );
}

function StepMarker({ n, status }: { n: number; status: AutopilotRun["steps"][number]["status"] }) {
  return (
    <span className="autopilot-marker" aria-label={t("Step {n}: {status}", { n, status })}>
      {status === "running" && (
        <svg className="autopilot-ring" viewBox="0 0 36 36" aria-hidden="true">
          <circle cx="18" cy="18" r="16" />
        </svg>
      )}
      {status === "done" ? <Check size={16} strokeWidth={2.5} aria-hidden="true" /> : status === "failed" ? <X size={16} strokeWidth={2.5} aria-hidden="true" /> : n}
    </span>
  );
}
