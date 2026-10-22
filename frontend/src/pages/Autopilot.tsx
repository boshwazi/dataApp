import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Check, Sparkles, Upload, X } from "lucide-react";
import { api, type AutopilotRun, type AutopilotStepKey } from "../api";
import { useToast } from "../components/ui";
import { useDataset } from "./Dataset";

const POLL_MS = 1500;
const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xlsm";

// Where each step's work can be seen (and changed) afterwards.
const STEP_AT: Record<AutopilotStepKey, string | null> = {
  read: "get-data",
  clean: "transform",
  model: "model",
  publish: "model",
  dashboard: null,
};
const STAGE_NAME: Record<string, string> = { "get-data": "Get Data", transform: "Transform", model: "Model" };

export function Autopilot() {
  const { project, refresh } = useDataset();
  const [run, setRun] = useState<AutopilotRun | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [name, setName] = useState(`${project.name} dashboard`);
  const wasRunning = useRef(false);

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

  const running = run?.status === "running";
  const fresh = !run;

  return (
    <div className="autopilot">
      <header className="autopilot-head">
        <h1>
          {fresh
            ? "Let datawazi build it for you"
            : running
              ? "Building your dashboard"
              : run.status === "done"
                ? "Your dashboard is ready"
                : "Autopilot stopped"}
        </h1>
        <p className="muted">
          {fresh
            ? "Autopilot cleans your tables, builds the model, publishes the dataset and designs a dashboard. It accepts the AI's checked changes for you; the dashboard stays a draft until you publish it."
            : running
              ? "You can leave this page; it keeps going. Open any step to see what it did, or ask the AI to change something."
              : run.status === "done"
                ? "Everything Autopilot added is marked, so you can review it, change it in each step, or ask the AI."
                : run.error}
        </p>
      </header>

      {fresh ? (
        <div className="autopilot-start">
          <label>
            Dashboard name
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button onClick={start} disabled={starting || project.sources.length === 0}>
            <Sparkles size={16} aria-hidden="true" /> {starting ? "Starting…" : "Start Autopilot"}
          </button>
          {project.sources.length === 0 && (
            <p className="muted small">
              Add some data first in <Link to="../get-data">Get Data</Link>.
            </p>
          )}
        </div>
      ) : (
        <ol className="autopilot-steps" aria-live="polite">
          {run.steps.map((step, i) => {
            const at = step.key === "dashboard" ? (run.dashboard_id ? `/dashboards/${run.dashboard_id}` : null) : STEP_AT[step.key];
            return (
              <li key={step.key} className={`autopilot-step is-${step.status}`}>
                <StepMarker n={i + 1} status={step.status} />
                <div className="autopilot-step-body">
                  <span className="autopilot-step-label">{step.label}</span>
                  <span className="autopilot-step-detail">
                    {step.status === "running" ? "Working on it…" : step.status === "pending" ? "Waiting" : step.detail}
                  </span>
                </div>
                {at && step.status !== "pending" && step.status !== "running" && (
                  <Link className="autopilot-step-open" to={at.startsWith("/") ? at : `../${at}`}>
                    {step.status === "failed" ? "Fix it" : "See what it did"}
                  </Link>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {run && !running && (
        <div className="autopilot-end">
          {run.status === "done" && run.dashboard_id && (
            <Link className="button" to={`/dashboards/${run.dashboard_id}`}>
              View dashboard
            </Link>
          )}
          {run.status !== "done" && run.fix_at && (
            <Link className="button" to={`../${run.fix_at}`}>
              Open {STAGE_NAME[run.fix_at]}
            </Link>
          )}
          <button className="secondary" onClick={start} disabled={starting}>
            {run.status === "done" ? "Run again" : "Try again"}
          </button>
        </div>
      )}
      {error && <p className="bad small">{error}</p>}
    </div>
  );
}

function StepMarker({ n, status }: { n: number; status: AutopilotRun["steps"][number]["status"] }) {
  return (
    <span className="autopilot-marker" aria-label={`Step ${n}: ${status}`}>
      {status === "running" && (
        <svg className="autopilot-ring" viewBox="0 0 36 36" aria-hidden="true">
          <circle cx="18" cy="18" r="16" />
        </svg>
      )}
      {status === "done" ? <Check size={16} strokeWidth={2.5} aria-hidden="true" /> : status === "failed" ? <X size={16} strokeWidth={2.5} aria-hidden="true" /> : n}
    </span>
  );
}

/** Home's quick start: drop a file, and Autopilot takes it from there. */
export function QuickStart() {
  const [busy, setBusy] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const toast = useToast();

  async function begin(files: File[]) {
    if (!files.length || busy) return;
    const name = files[0].name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim() || "My data";
    try {
      setBusy("Creating the dataset…");
      const dataset = await api.createDataset(name);
      setBusy("Uploading…");
      const added = await api.uploadFiles(dataset.id, files);
      if (!added.added.length) {
        toast(added.errors[0] ?? "None of those files could be read.", "error");
        navigate(`/datasets/${dataset.id}/get-data`);
        return;
      }
      await api.startAutopilot(dataset.id);
      navigate(`/datasets/${dataset.id}/autopilot`);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  function dropped(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    void begin([...e.dataTransfer.files]);
  }

  return (
    <section
      className={`quick-start ${over ? "over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={dropped}
    >
      <Upload size={20} aria-hidden="true" />
      <div>
        <h2>Drop a spreadsheet and get a dashboard</h2>
        <p className="muted small">
          CSV or Excel. Autopilot cleans it, models it and designs a draft dashboard; you can change any step later.
        </p>
      </div>
      <button className="secondary" onClick={() => input.current?.click()} disabled={!!busy}>
        {busy ?? "Choose files"}
      </button>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        multiple
        hidden
        onChange={(e) => void begin([...(e.target.files ?? [])])}
      />
    </section>
  );
}
