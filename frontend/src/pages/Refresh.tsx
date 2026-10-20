import { useEffect, useRef, useState } from "react";
import { refreshApi, type RefreshEntry, type RefreshState } from "../api";
import { useDataset } from "./Dataset";

const SCHEDULE_LABELS: Record<string, string> = {
  off: "Off",
  hourly: "Every hour",
  every_6_hours: "Every 6 hours",
  daily: "Every day",
  weekly: "Every week",
};

const TRIGGER_LABELS = { manual: "Pulled now", replace: "File replaced", schedule: "Scheduled" } as const;

/** New data in, same steps, model and dashboards on top; every refresh is checked and recorded. */
export function Refresh() {
  const { project, refresh: refreshDataset } = useDataset();
  const [state, setState] = useState<RefreshState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [latest, setLatest] = useState<RefreshEntry | null>(null);

  useEffect(() => {
    refreshApi.state(project.id).then(setState).catch((e) => setError(e.message));
  }, [project.id]);

  async function act(label: string, action: () => Promise<RefreshState>) {
    setBusy(label);
    setError(null);
    try {
      const next = await action();
      setState(next);
      if (next.entry) setLatest(next.entry);
      void refreshDataset();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (!state) return <p className={error ? "bad" : "muted"}>{error ?? "Loading…"}</p>;

  const sheets = state.sources.filter((s) => s.kind === "gsheet" || s.kind === "dataset");
  const { settings } = state;

  return (
    <div className="refresh">
      <section className="card refresh-settings">
        <h2>Refresh</h2>
        <p className="muted small">
          New data replaces old rows; the Applied Steps, model and dashboards stay as they are. Every refresh is checked,
          and dashboards only change when a version is published.
        </p>
        <div className="refresh-controls">
          <label>
            Pull Google Sheets and linked tables
            <select
              value={settings.schedule}
              onChange={(e) => act("schedule", () => refreshApi.configure(project.id, { schedule: e.target.value }))}
              disabled={!sheets.length || busy !== null}
            >
              {state.schedules.map((s) => (
                <option key={s} value={s}>
                  {SCHEDULE_LABELS[s] ?? s}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={settings.auto_publish}
              onChange={(e) => act("auto", () => refreshApi.configure(project.id, { auto_publish: e.target.checked }))}
              disabled={busy !== null}
            />
            Publish automatically when the checks pass
          </label>
          <button onClick={() => act("all", () => refreshApi.pullAll(project.id))} disabled={!sheets.length || busy !== null}>
            {busy === "all" ? "Pulling…" : "Pull all now"}
          </button>
        </div>
        {!sheets.length && (
          <p className="muted small">This dataset has only uploaded files. Refresh them with "Replace file" below.</p>
        )}
        {settings.next_run && (
          <p className="muted small">Next scheduled pull: {new Date(settings.next_run).toLocaleString()}</p>
        )}
      </section>

      {error && <p className="bad">{error}</p>}
      {latest && <EntryCard entry={latest} highlight />}

      <section>
        <h3 className="section-title">Sources</h3>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Table</th>
                <th>From</th>
                <th className="num">Rows</th>
                <th>Last loaded</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {state.sources.map((s) => (
                <tr key={s.id}>
                  <td className="source-name">{s.table}</td>
                  <td className="small">{s.kind === "gsheet" ? "Google Sheet" : s.kind === "dataset" ? "Linked table" : s.file}</td>
                  <td className="num">{s.rows.toLocaleString()}</td>
                  <td className="small">{new Date(s.refreshed_at).toLocaleString()}</td>
                  <td>
                    {s.kind === "gsheet" || s.kind === "dataset" ? (
                      <button className="secondary" onClick={() => act(s.id, () => refreshApi.pull(project.id, s.id))} disabled={busy !== null}>
                        {busy === s.id ? "Pulling…" : "Pull now"}
                      </button>
                    ) : (
                      <ReplaceButton
                        busy={busy === s.id}
                        disabled={busy !== null}
                        onFile={(file) => act(s.id, () => refreshApi.replace(project.id, s.id, file))}
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h3 className="section-title">History</h3>
        {state.history.length === 0 ? (
          <p className="muted small">No refreshes yet.</p>
        ) : (
          <div className="refresh-history">
            {state.history.map((entry, i) => (
              <EntryCard key={i} entry={entry} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function ReplaceButton({ busy, disabled, onFile }: { busy: boolean; disabled: boolean; onFile: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button className="secondary" onClick={() => input.current?.click()} disabled={disabled}>
        {busy ? "Loading…" : "Replace file"}
      </button>
      <input
        ref={input}
        type="file"
        accept=".csv,.tsv,.txt,.xlsx,.xlsm"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
    </>
  );
}

function EntryCard({ entry, highlight = false }: { entry: RefreshEntry; highlight?: boolean }) {
  const badge = { ok: "ok-badge", warnings: "warn", failed: "bad-badge" }[entry.status];
  const label = { ok: "Checks passed", warnings: "Passed with warnings", failed: "Checks failed" }[entry.status];
  return (
    <article className={`refresh-entry ${highlight ? "highlight" : ""}`}>
      <header>
        <span className={`badge ${badge}`}>{label}</span>
        <span className="muted small">
          {TRIGGER_LABELS[entry.trigger]} · {new Date(entry.at).toLocaleString()}
        </span>
        <span className="spacer" />
        {entry.published_version ? (
          <span className="badge ok-badge">Published v{entry.published_version}</span>
        ) : (
          <span className="muted small">
            {entry.status === "failed" ? "Not published: dashboards keep the last good version" : "Not published yet"}
          </span>
        )}
      </header>
      {entry.sources.length > 0 && (
        <p className="small">
          {entry.sources
            .map((s) => `${s.table}: ${s.rows_before.toLocaleString()} → ${s.rows_after.toLocaleString()} rows`)
            .join(" · ")}
        </p>
      )}
      {entry.checks.length > 0 && (
        <ul className="refresh-checks">
          {entry.checks.map((c, i) => (
            <li key={i} className={`check-${c.level}`}>
              {c.level === "error" ? "✗" : c.level === "warning" ? "⚠" : "ℹ"} {c.text}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
