import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  transformApi,
  type AIRun,
  type QueryDetail,
  type QuerySummary,
  type StepPreview,
  type StepRows,
  type TransformState,
} from "../api";
import { ProfileTable } from "./GetData";
import { useDataset } from "./Dataset";

export function Transform() {
  const { project, refresh } = useDataset();
  const [params, setParams] = useSearchParams();
  const [state, setState] = useState<TransformState | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cleaning, setCleaning] = useState<Set<string>>(new Set());
  const [runs, setRuns] = useState<Record<string, AIRun | { error: string }>>({});
  const [version, setVersion] = useState(0);

  const selectedId = params.get("table") ?? state?.queries[0]?.id ?? null;
  const selected = state?.queries.find((q) => q.id === selectedId) ?? null;
  const select = (id: string) => setParams({ table: id }, { replace: true });

  useEffect(() => {
    transformApi.state(project.id).then(setState).catch((e) => setError(e.message));
    api.getSettings().then((s) => setHasKey(s.has_api_key)).catch(() => setHasKey(false));
  }, [project.id]);

  const apply = useCallback((next: TransformState) => {
    setState(next);
    void refresh(); // the header shows whether the dataset now differs from what is published
    setVersion((v) => v + 1);
  }, [refresh]);

  async function run(action: () => Promise<TransformState>) {
    setError(null);
    try {
      apply(await action());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function clean(ids: string[]) {
    setCleaning((current) => new Set([...current, ...ids]));
    // One table at a time: each run sees the steps the previous one added.
    for (const id of ids) {
      try {
        const result = await transformApi.aiClean(project.id, id);
        setRuns((current) => ({ ...current, [id]: result }));
        apply(result);
      } catch (e) {
        setRuns((current) => ({ ...current, [id]: { error: (e as Error).message } }));
      } finally {
        setCleaning((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
      }
    }
  }

  if (!state) return <p className={error ? "bad" : "muted"}>{error ?? "Loading…"}</p>;

  if (state.queries.length === 0) {
    return (
      <div className="empty-state">
        <h2>Transform</h2>
        <p className="muted">Add data in Get Data first. Each table you load appears here, ready to be cleaned.</p>
      </div>
    );
  }

  const busy = cleaning.size > 0;
  const cleanable = state.queries.filter((q) => !q.error).map((q) => q.id);
  const unapproved = state.queries.reduce(
    (n, q) => n + q.steps.filter((s) => s.status === "AI").length + (q.status === "AI" ? 1 : 0),
    0,
  );

  return (
    <div className="get-data">
      <aside className="sources">
        <h2>Tables</h2>
        <ul className="source-list">
          {state.queries.map((query) => (
            <QueryItem
              key={query.id}
              query={query}
              selected={query.id === selectedId}
              cleaning={cleaning.has(query.id)}
              onSelect={() => select(query.id)}
            />
          ))}
        </ul>
        <div className="side-actions">
          <button onClick={() => clean(cleanable)} disabled={!hasKey || busy || cleanable.length === 0}>
            {busy ? "AI is cleaning…" : "Clean all tables with AI"}
          </button>
          <button
            className="secondary"
            onClick={() => run(() => transformApi.approveAll(project.id))}
            disabled={unapproved === 0 || busy}
          >
            {unapproved ? `Approve all AI steps (${unapproved})` : "All steps approved"}
          </button>
          <button className="secondary" onClick={() => run(() => transformApi.undo(project.id))} disabled={!state.can_undo || busy}>
            Undo last change
          </button>
        </div>
        {!hasKey && <p className="muted small">Add an API key in Settings so the AI can clean your tables.</p>}
        {error && <p className="bad small">{error}</p>}
      </aside>

      <div className="source-detail">
        {selected && (
          <QueryDetailView
            key={`${selected.id}-${version}`}
            projectId={project.id}
            query={selected}
            hasKey={hasKey}
            cleaning={cleaning.has(selected.id)}
            run={runs[selected.id]}
            onClean={() => clean([selected.id])}
            onChange={run}
          />
        )}
      </div>
    </div>
  );
}

function QueryItem(props: { query: QuerySummary; selected: boolean; cleaning: boolean; onSelect: () => void }) {
  const { query } = props;
  const aiSteps = query.steps.filter((s) => s.status === "AI").length;
  return (
    <li>
      <button className={`source-item ${props.selected ? "selected" : ""}`} onClick={props.onSelect}>
        <span className="source-name">{query.name}</span>
        <span className="muted small">
          {query.rows === null ? "not loaded" : `${query.rows.toLocaleString()} rows`} · {query.steps.length}{" "}
          {query.steps.length === 1 ? "step" : "steps"}
        </span>
        <span className="badges">
          {query.kind === "lookup" && (
            <span className={`badge ${query.status === "AI" ? "ai" : ""}`}>lookup{query.status === "AI" ? " · AI" : ""}</span>
          )}
          {query.error && <span className="badge bad-badge">error</span>}
          {query.issue_count ? <span className="badge warn">{query.issue_count} {query.issue_count === 1 ? "issue" : "issues"}</span> : null}
          {query.issue_count === 0 && <span className="badge ok-badge">clean</span>}
          {props.cleaning ? <span className="badge">AI cleaning…</span> : aiSteps > 0 && <span className="badge ai">{aiSteps} AI</span>}
        </span>
      </button>
    </li>
  );
}

type DetailProps = {
  projectId: string;
  query: QuerySummary;
  hasKey: boolean;
  cleaning: boolean;
  run?: AIRun | { error: string };
  onClean: () => void;
  onChange: (action: () => Promise<TransformState>) => Promise<void>;
};

function QueryDetailView({ projectId, query, hasKey, cleaning, run, onClean, onChange }: DetailProps) {
  const [step, setStep] = useState(query.steps.length);
  const [tab, setTab] = useState<"preview" | "profile">("preview");
  const [detail, setDetail] = useState<QueryDetail | null>(null);

  useEffect(() => {
    transformApi.query(projectId, query.id).then(setDetail).catch(() => setDetail(null));
  }, [projectId, query.id]);

  const rename = () => {
    const name = prompt("Table name", query.name);
    if (name?.trim() && name !== query.name) void onChange(() => transformApi.rename(projectId, query.id, name));
  };

  const last = query.steps.length;
  const aiCount = query.steps.filter((s) => s.status === "AI").length;

  return (
    <>
      <header className="detail-header">
        <div>
          <h2>{query.name}</h2>
          <p className="muted small">
            {query.kind === "lookup" ? `Lookup table from ${query.base_table ?? "a removed table"}` : `From ${query.source_table ?? "a removed source"}`}
            {query.rows !== null && ` · ${query.rows.toLocaleString()} rows · ${query.columns.length} columns`}
          </p>
        </div>
        <div className="actions">
          <button className="secondary" onClick={rename}>
            Rename
          </button>
          {query.kind === "lookup" && (
            <button className="secondary danger" onClick={() => onChange(() => transformApi.removeTable(projectId, query.id))}>
              Remove table
            </button>
          )}
          <button onClick={onClean} disabled={!hasKey || cleaning || !!query.error}>
            {cleaning ? "AI is cleaning…" : "Clean with AI"}
          </button>
        </div>
      </header>

      {query.error && <div className="notice bad-notice">{query.error}</div>}
      {query.kind === "lookup" && query.status === "AI" && (
        <div className="ai-card">
          <span className="badge ai">AI</span>
          <div className="grow">
            <p>The AI split this lookup table off {query.base_table}.</p>
            {query.reason && <p className="muted small">{query.reason}</p>}
          </div>
          <button className="secondary" onClick={() => onChange(() => transformApi.approveAll(projectId, query.id))}>
            Approve table
          </button>
        </div>
      )}
      {run && <RunResult run={run} />}

      <div className="section-head">
        <h3 className="section-title">Applied steps</h3>
        {aiCount > 0 && (
          <button className="link small" onClick={() => onChange(() => transformApi.approveAll(projectId, query.id))}>
            Approve all {aiCount} AI steps
          </button>
        )}
      </div>
      <ol className="steps">
        <li className={step === 0 ? "selected" : ""}>
          <button className="step-main" onClick={() => setStep(0)}>
            <span className="step-number">0</span>
            <span>
              {query.kind === "lookup"
                ? `Copy of ${query.base_table} ${query.split_after_step ? `after its step ${query.split_after_step}` : "as loaded"}`
                : `Source: ${query.source_table}`}
            </span>
          </button>
        </li>
        {query.steps.map((s, i) => (
          <li key={s.id} className={step === i + 1 ? "selected" : ""}>
            <button className="step-main" onClick={() => setStep(i + 1)}>
              <span className="step-number">{i + 1}</span>
              <span>
                <span className="step-label">{s.label}</span>
                {s.reason && <span className="muted small step-reason">{s.reason}</span>}
              </span>
            </button>
            <span className="step-tools">
              <span className={`badge ${s.status === "AI" ? "ai" : "ok-badge"}`}>{s.status}</span>
              {s.status === "AI" && (
                <button className="link small" onClick={() => onChange(() => transformApi.approve(projectId, query.id, s.id))}>
                  Approve
                </button>
              )}
              <button
                className="icon"
                title="Move up"
                disabled={i === 0}
                onClick={() => onChange(() => transformApi.move(projectId, query.id, s.id, i - 1))}
              >
                ↑
              </button>
              <button
                className="icon"
                title="Move down"
                disabled={i === last - 1}
                onClick={() => onChange(() => transformApi.move(projectId, query.id, s.id, i + 1))}
              >
                ↓
              </button>
              <button
                className="icon danger"
                title="Remove step"
                onClick={() => onChange(() => transformApi.remove(projectId, query.id, s.id))}
              >
                ×
              </button>
            </span>
          </li>
        ))}
      </ol>
      {query.steps.length === 0 && !query.error && (
        <p className="muted small">
          No steps yet. {hasKey ? "Ask the AI to clean this table." : "Add an API key in Settings to let the AI clean it."}
        </p>
      )}

      {!query.error && (
        <>
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "preview"} onClick={() => setTab("preview")}>
              {step === 0 ? "Source preview" : `Step ${step} preview`}
            </button>
            <button role="tab" aria-selected={tab === "profile"} onClick={() => setTab("profile")}>
              Result profile
            </button>
          </div>
          {tab === "preview" ? (
            <StepPreviewView projectId={projectId} queryId={query.id} step={step} />
          ) : detail?.profile ? (
            <ProfileTable columns={detail.profile.columns} rows={detail.profile.rows} ai={null} />
          ) : (
            <p className="muted">Loading…</p>
          )}
        </>
      )}
    </>
  );
}

function RunResult({ run }: { run: AIRun | { error: string } }) {
  if ("error" in run) return <div className="notice bad-notice small">{run.error}</div>;
  return (
    <div className="ai-card">
      <span className="badge ai">AI</span>
      <div>
        <p>{run.summary || "The AI reviewed this table."}</p>
        <p className="muted small">
          {run.added.length ? `Added ${run.added.length} ${run.added.length === 1 ? "step" : "steps"}.` : "No new steps needed."}
          {run.new_tables?.length > 0 && ` Split off ${run.new_tables.join(", ")}.`}
          {run.rejected.length > 0 && ` ${run.rejected.length} proposed ${run.rejected.length === 1 ? "step was" : "steps were"} refused by the checks:`}
        </p>
        {run.rejected.map((r, i) => (
          <p key={i} className="issue small">
            {r.error}
          </p>
        ))}
      </div>
    </div>
  );
}

function StepPreviewView({ projectId, queryId, step }: { projectId: string; queryId: string; step: number }) {
  const [preview, setPreview] = useState<StepPreview | null>(null);
  const [side, setSide] = useState<"after" | "before">("after");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPreview(null);
    setSide("after");
    transformApi.preview(projectId, queryId, step).then(setPreview).catch((e) => setError(e.message));
  }, [projectId, queryId, step]);

  if (error) return <p className="bad">{error}</p>;
  if (!preview) return <p className="muted">Loading…</p>;

  const impact = preview.impact;
  const rows: StepRows = side === "before" && preview.before ? preview.before : preview.after;

  return (
    <>
      {impact && (
        <div className="impact">
          <span>
            Rows: {impact.rows_before.toLocaleString()}
            {impact.rows_after !== impact.rows_before && <> → <strong>{impact.rows_after.toLocaleString()}</strong></>}
          </span>
          {impact.type_changes.map((t) => (
            <span key={t.column}>
              {t.column}: {t.from} → <strong>{t.to}</strong>
            </span>
          ))}
          {impact.blank_changes.map((b) => (
            <span key={b.column}>
              {b.column} blanks: {b.before} → <strong>{b.after}</strong>
            </span>
          ))}
          {impact.added_columns.length > 0 && <span>Added: {impact.added_columns.join(", ")}</span>}
          {impact.removed_columns.length > 0 && <span>Removed: {impact.removed_columns.join(", ")}</span>}
          <span className="segmented">
            <button className={side === "before" ? "on" : ""} onClick={() => setSide("before")}>
              Before
            </button>
            <button className={side === "after" ? "on" : ""} onClick={() => setSide("after")}>
              After
            </button>
          </span>
        </div>
      )}
      <div className="table-wrap">
        <table className="grid preview">
          <thead>
            <tr>
              <th className="num muted">#</th>
              {rows.columns.map((c) => (
                <th key={c.name}>
                  {c.name} <span className={`type type-${c.type}`}>{c.type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.rows.map((row, i) => (
              <tr key={i}>
                <td className="num muted">{i + 1}</td>
                {row.map((value, j) => (
                  <td key={j} className={rows.columns[j].type !== "text" ? "num" : ""}>
                    {value === null ? <span className="null">null</span> : value}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Showing {rows.rows.length.toLocaleString()} of {rows.total.toLocaleString()} rows
      </p>
    </>
  );
}
