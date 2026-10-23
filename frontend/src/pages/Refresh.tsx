import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, refreshApi, type LoadMode, type RefreshEntry, type RefreshState } from "../api";
import { useDialogs } from "../components/ui";
import { useDataset } from "./Dataset";

const SCHEDULE_LABELS: Record<string, string> = {
  off: "Off",
  hourly: "Every hour",
  every_6_hours: "Every 6 hours",
  daily: "Every day",
  weekdays: "Weekdays (Monday to Friday)",
  weekly: "Every week",
};
const TIMED = ["daily", "weekdays", "weekly"];
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const PROVIDERS: Record<string, string> = { onedrive: "OneDrive", sharepoint: "SharePoint", gdrive: "Google Drive" };

const TRIGGER_LABELS = {
  manual: "Pulled now",
  replace: "File replaced",
  schedule: "Scheduled",
  powerbi: "From a newer .pbix",
  linked: "Its linked table's dataset published",
} as const;

/** Malaysia time, whatever the computer's clock is set to. */
function myt(iso: string) {
  return new Date(iso).toLocaleString("en-MY", { timeZone: "Asia/Kuala_Lumpur", dateStyle: "medium", timeStyle: "short" });
}

/** New data in, same steps, model and dashboards on top; every refresh is checked and recorded. */
export function Refresh() {
  const { project, refresh: refreshDataset } = useDataset();
  const dialogs = useDialogs();
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

  async function rollBack(version: number) {
    const ok = await dialogs.confirm({
      title: `Roll back to version ${version}?`,
      body: "Dashboards go back to that version's data and model, published again as the newest version. Your draft isn't changed.",
      confirmLabel: "Roll back",
    });
    if (!ok) return;
    await act(`v${version}`, async () => {
      try {
        await refreshApi.rollBack(project.id, version);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409 && e.detail?.problems)) throw e;
        const names = e.detail.problems.map((p) => `${p.dashboard}: ${p.visual}`).join(", ");
        const anyway = await dialogs.confirm({
          title: "Some visuals would stop working",
          body: `Version ${version} doesn't have what these need: ${names}. Roll back anyway?`,
          confirmLabel: "Roll back anyway",
          danger: true,
        });
        if (!anyway) return refreshApi.state(project.id);
        await refreshApi.rollBack(project.id, version, true);
      }
      return refreshApi.state(project.id);
    });
  }

  if (!state) return <p className={error ? "bad" : "muted"}>{error ?? "Loading…"}</p>;

  const pullable = state.sources.filter((s) => s.pullable);
  const linked = state.sources.some((s) => s.kind === "dataset");
  const { settings, alert } = state;
  const configure = (update: Parameters<typeof refreshApi.configure>[1]) =>
    act("settings", () => refreshApi.configure(project.id, update));

  return (
    <div className="refresh">
      {alert && (
        <div className={`notice refresh-alert is-${alert.status}`} role="status">
          <span>
            <strong>{alert.trigger === "schedule" ? "The scheduled refresh" : "The refresh after a linked table changed"}</strong>{" "}
            {alert.status === "failed" ? "failed" : "passed with warnings"} on {myt(alert.at)}. {alert.text}
          </span>
          <button className="link" onClick={() => act("alert", () => refreshApi.dismissAlert(project.id))}>
            Dismiss
          </button>
        </div>
      )}

      <section className="card refresh-settings">
        <h2>Refresh</h2>
        <p className="muted small">
          New data replaces old rows (or is appended, if you choose); the Applied Steps, model and dashboards stay as they are.
          Every refresh is checked, and dashboards only change when a version is published.
        </p>
        {!pullable.length && (
          <p className="notice">
            This dataset has only uploaded files, so there's nothing to pull on a schedule. Refresh them with "Replace file" below,
            or add the file from a shared OneDrive, SharePoint or Google Drive link in <Link to="../get-data">Get Data</Link>.
          </p>
        )}
        {pullable.length > 0 && <div className="refresh-controls">
          <label>
            Pull sheets, shared links and linked tables
            <select value={settings.schedule} onChange={(e) => configure({ schedule: e.target.value })} disabled={!pullable.length || busy !== null}>
              {state.schedules.map((s) => (
                <option key={s} value={s}>
                  {SCHEDULE_LABELS[s] ?? s}
                </option>
              ))}
            </select>
          </label>
          {settings.schedule === "weekly" && (
            <label>
              On
              <select value={settings.day} onChange={(e) => configure({ day: Number(e.target.value) })} disabled={busy !== null}>
                {DAYS.map((d, i) => (
                  <option key={d} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
          )}
          {TIMED.includes(settings.schedule) && (
            <label>
              At (Malaysia time)
              <input
                type="time"
                defaultValue={settings.at}
                key={settings.at}
                onBlur={(e) => e.target.value && e.target.value !== settings.at && configure({ at: e.target.value })}
                disabled={busy !== null}
              />
            </label>
          )}
          <button onClick={() => act("all", () => refreshApi.pullAll(project.id))} disabled={!pullable.length || busy !== null}>
            {busy === "all" ? "Pulling…" : "Pull all now"}
          </button>
        </div>}
        <div className="refresh-options">
          <label className="check">
            <input type="checkbox" checked={settings.auto_publish} onChange={(e) => configure({ auto_publish: e.target.checked })} disabled={busy !== null} />
            Publish automatically when the checks pass
          </label>
          {linked && (
            <label className="check">
              <input type="checkbox" checked={settings.follow_links} onChange={(e) => configure({ follow_links: e.target.checked })} disabled={busy !== null} />
              Pull linked tables as soon as their dataset publishes
            </label>
          )}
        </div>

        {settings.next_run && (
          <p className="muted small">
            {settings.retry ? `Couldn't download; trying again (attempt ${settings.retry + 1}) on ` : "Next scheduled pull: "}
            {myt(settings.next_run)}
          </p>
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
                <th>Each refresh</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {state.sources.map((s) => (
                <tr key={s.id}>
                  <td className="source-name">{s.table}</td>
                  <td className="small">
                    {s.kind === "gsheet"
                      ? "Google Sheet"
                      : s.kind === "dataset"
                        ? "Linked table"
                        : s.kind === "link"
                          ? `${PROVIDERS[s.provider ?? ""] ?? "Shared"} link${s.file ? `: ${s.file}` : ""}`
                          : s.file}
                  </td>
                  <td className="num">{s.rows.toLocaleString()}</td>
                  <td className="small">{myt(s.refreshed_at)}</td>
                  <td>
                    <LoadPicker
                      load={s.load}
                      columns={s.columns}
                      disabled={busy !== null || s.kind === "dataset" || s.kind === "powerbi"}
                      onChange={(load) => act(`load-${s.id}`, () => refreshApi.setLoad(project.id, s.id, load))}
                    />
                  </td>
                  <td>
                    {s.pullable ? (
                      <button className="secondary" onClick={() => act(s.id, () => refreshApi.pull(project.id, s.id))} disabled={busy !== null}>
                        {busy === s.id ? "Pulling…" : "Pull now"}
                      </button>
                    ) : (
                      <ReplaceButton
                        busy={busy === s.id}
                        disabled={busy !== null}
                        label={s.load.mode === "append" ? "Add rows from a file" : "Replace file"}
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

      {state.versions.length > 0 && (
        <section>
          <h3 className="section-title">Published versions</h3>
          <p className="muted small">The last {state.versions.length > 1 ? "few versions are" : "version is"} kept, so you can go back if new data was wrong.</p>
          <ul className="version-list">
            {state.versions.map((v) => (
              <li key={v.version}>
                <span className="version-name">Version {v.version}</span>
                <span className="muted small">
                  {myt(v.published_at)}
                  {v.rolled_back_from ? `, a roll-back to version ${v.rolled_back_from}` : ""}
                </span>
                <span className="spacer" />
                {v.live ? (
                  <span className="badge ok-badge">Live</span>
                ) : (
                  <button className="secondary small-button" onClick={() => rollBack(v.version)} disabled={busy !== null}>
                    {busy === `v${v.version}` ? "Rolling back…" : "Roll back to this"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

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

function LoadPicker(props: { load: LoadMode; columns: string[]; disabled: boolean; onChange: (load: LoadMode) => void }) {
  const { load } = props;
  return (
    <span className="load-picker">
      <select
        aria-label="What each refresh does"
        value={load.mode}
        onChange={(e) => props.onChange({ mode: e.target.value as LoadMode["mode"], key: null })}
        disabled={props.disabled}
      >
        <option value="replace">Replace rows</option>
        <option value="append">Append rows</option>
      </select>
      {load.mode === "append" && (
        <select
          aria-label="Skip rows already loaded, matched by"
          value={load.key ?? ""}
          onChange={(e) => props.onChange({ mode: "append", key: e.target.value || null })}
          disabled={props.disabled}
          title="Rows whose value in this column is already loaded are skipped"
        >
          <option value="">Keep every row</option>
          {props.columns.map((c) => (
            <option key={c} value={c}>
              Skip if {c} is loaded
            </option>
          ))}
        </select>
      )}
    </span>
  );
}

function ReplaceButton({ busy, disabled, label, onFile }: { busy: boolean; disabled: boolean; label: string; onFile: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button className="secondary" onClick={() => input.current?.click()} disabled={disabled}>
        {busy ? "Loading…" : label}
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
          {TRIGGER_LABELS[entry.trigger] ?? entry.trigger}, {myt(entry.at)}
        </span>
        <span className="spacer" />
        {entry.published_version ? (
          <span className="badge ok-badge">Published v{entry.published_version}</span>
        ) : (
          <span className="muted small">
            {entry.retry_at
              ? `Trying again at ${myt(entry.retry_at)}`
              : entry.status === "failed"
                ? "Not published: dashboards keep the last good version"
                : "Not published yet"}
          </span>
        )}
      </header>
      {entry.sources.length > 0 && (
        <p className="small">
          {entry.sources.map((s) => `${s.table}: ${s.rows_before.toLocaleString()} → ${s.rows_after.toLocaleString()} rows`).join(", ")}
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
