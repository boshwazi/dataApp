import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, ChevronDown, ChevronRight, Combine, CopyMinus, MoreHorizontal, Plus, Rows3, Sparkles, Table2, Undo2, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  transformApi,
  type AIRun,
  type LogicalType,
  type QueryDetail,
  type QuerySummary,
  type StepPreview,
  type StepRows,
  type TransformState,
} from "../api";
import { Menu, PopMenu, useDialogs } from "../components/ui";
import { useDataset } from "./Dataset";

type Change = (action: () => Promise<TransformState>) => Promise<boolean>;

/** Short tags for column types, as in Power Query's column headers. */
export const TYPE_TAG: Record<LogicalType, string> = { text: "abc", integer: "123", decimal: "1.2", date: "date", boolean: "y/n" };

export function TypeTag({ type }: { type: LogicalType }) {
  return (
    <span className={`type-tag type-tag-${type}`} title={type}>
      {TYPE_TAG[type] ?? type}
    </span>
  );
}

/** Transform: tables on the left, the selected table's data in the middle, its Applied Steps on the right. */
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

  const apply = useCallback(
    (next: TransformState) => {
      setState(next);
      void refresh(); // the top bar shows whether the dataset now differs from what is published
      setVersion((v) => v + 1);
    },
    [refresh],
  );

  const change: Change = useCallback(
    async (action) => {
      setError(null);
      try {
        apply(await action());
        return true;
      } catch (e) {
        setError((e as Error).message);
        return false;
      }
    },
    [apply],
  );

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

  if (!state) return <p className={error ? "bad pad" : "muted pad"}>{error ?? "Loading…"}</p>;

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
  const unapproved = state.queries.reduce((n, q) => n + q.steps.filter((s) => s.status === "AI").length + (q.status === "AI" ? 1 : 0), 0);

  return (
    <div className="editor transform-editor">
      <aside className="pane pane-left" aria-label="Tables">
        <div className="pane-head">
          <span>Tables</span>
          <span className="grow" />
          <Menu
            label="Table actions"
            icon={<MoreHorizontal size={16} />}
            items={[
              ...(hasKey && cleanable.length ? [{ label: busy ? "AI is cleaning…" : "Clean all tables with AI", icon: <Sparkles size={15} aria-hidden="true" />, onSelect: () => void clean(cleanable) }] : []),
              ...(unapproved ? [{ label: `Approve all AI steps (${unapproved})`, onSelect: () => void change(() => transformApi.approveAll(project.id)) }] : []),
              { label: "Undo last change", icon: <Undo2 size={15} aria-hidden="true" />, onSelect: () => void change(() => transformApi.undo(project.id)) },
            ]}
          />
        </div>
        <ul className="tree" role="tree" aria-label="Tables and columns">
          {state.queries.map((query) => (
            <TableItem key={query.id} query={query} selected={query.id === selectedId} cleaning={cleaning.has(query.id)} onSelect={() => select(query.id)} />
          ))}
        </ul>
        {!hasKey && <p className="muted small pane-note">Add an API key in Settings so the AI can suggest steps.</p>}
      </aside>

      {selected ? (
        <QueryWorkspace
          key={`${selected.id}-${version}`}
          projectId={project.id}
          query={selected}
          others={state.queries.filter((q) => q.id !== selected.id)}
          canUndo={state.can_undo}
          hasKey={hasKey}
          cleaning={cleaning.has(selected.id)}
          run={runs[selected.id]}
          error={error}
          onClean={() => clean([selected.id])}
          onChange={change}
        />
      ) : (
        <p className="muted pad">Choose a table.</p>
      )}
    </div>
  );
}

function TableItem(props: { query: QuerySummary; selected: boolean; cleaning: boolean; onSelect: () => void }) {
  const { query, selected } = props;
  const aiSteps = query.steps.filter((s) => s.status === "AI").length;
  return (
    <li role="treeitem" aria-expanded={selected} aria-selected={selected}>
      <button className={`tree-row table-row ${selected ? "on" : ""}`} onClick={props.onSelect}>
        {selected ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        <Table2 size={14} aria-hidden="true" />
        <span className="tree-name">{query.name}</span>
        {query.error && <span className="badge bad-badge">error</span>}
        {props.cleaning ? <span className="badge ai">cleaning</span> : aiSteps > 0 && <span className="badge ai">{aiSteps} AI</span>}
        {query.kind === "lookup" && <span className="badge">lookup</span>}
        <span className="tree-count">{query.rows === null ? "" : query.rows.toLocaleString()}</span>
      </button>
      {selected && (
        <ul role="group">
          {query.columns.map((c) => (
            <li key={c.name} role="treeitem" className="tree-row column-row">
              <TypeTag type={c.type} />
              <span className="tree-name">{c.name}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

type WorkspaceProps = {
  projectId: string;
  query: QuerySummary;
  others: QuerySummary[];
  canUndo: boolean;
  hasKey: boolean;
  cleaning: boolean;
  run?: AIRun | { error: string };
  error: string | null;
  onClean: () => void;
  onChange: Change;
};

function QueryWorkspace({ projectId, query, others, canUndo, hasKey, cleaning, run, error, onClean, onChange }: WorkspaceProps) {
  const total = query.steps.length;
  const [step, setStep] = useState(total);
  const [preview, setPreview] = useState<StepPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [detail, setDetail] = useState<QueryDetail | null>(null);
  const [side, setSide] = useState<"after" | "before" | "removed">("after");
  const [removed, setRemoved] = useState<(StepRows & { comparable: boolean }) | null>(null);
  const [menu, setMenu] = useState<{ column: string; type: LogicalType; x: number; y: number } | null>(null);
  const [combine, setCombine] = useState<{ kind: "merge" | "append" | "column"; x: number; y: number } | null>(null);
  const openCombine = (kind: "merge" | "append" | "column") => (e: ReactMouseEvent) => {
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setCombine({ kind, x: box.left, y: box.bottom + 4 });
  };
  const dialogs = useDialogs();

  useEffect(() => {
    transformApi.query(projectId, query.id).then(setDetail).catch(() => setDetail(null));
  }, [projectId, query.id]);

  useEffect(() => {
    setPreview(null);
    setRemoved(null);
    setSide("after");
    setPreviewError(null);
    if (query.error) return;
    transformApi.preview(projectId, query.id, step).then(setPreview).catch((e) => setPreviewError(e.message));
  }, [projectId, query.id, query.error, step]);

  // New steps go after the step on show, like Power Query's "insert step".
  const add = (op: string, params: Record<string, unknown>) =>
    onChange(() => transformApi.addStep(projectId, query.id, op, params, step < total ? step : undefined));

  const impact = preview?.impact ?? null;
  const dropped = impact ? impact.rows_before - impact.rows_after : 0;
  const rows: StepRows | null = preview
    ? side === "removed" && removed
      ? removed
      : side === "before" && preview.before
        ? preview.before
        : preview.after
    : null;

  // Cells this step changed, when rows line up one to one (no rows added or removed).
  const changed = useMemo(() => {
    const out = new Set<string>();
    // Steps that may reorder rows (merge, append, filters, de-duplication) can't be compared row by row.
    const op = query.steps[step - 1]?.op;
    if (!preview?.before || side !== "after" || !impact || impact.rows_before !== impact.rows_after || REORDERS.has(op)) return out;
    const beforeIndex = new Map(preview.before.columns.map((c, i) => [c.name, i]));
    preview.after.rows.forEach((row, r) =>
      row.forEach((value, c) => {
        const b = beforeIndex.get(preview.after.columns[c].name);
        if (b !== undefined && preview.before!.rows[r]?.[b] !== value) out.add(`${r}:${c}`);
      }),
    );
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview, side, impact, step]);

  // Column quality: the whole table on the last step (from its profile), the rows on show otherwise.
  const quality = useMemo(() => {
    const out = new Map<string, { valid: number; blank: number; issue: number; note: string }>();
    if (!rows) return out;
    const profiled = step === total && side === "after" && detail?.profile;
    rows.columns.forEach((col, c) => {
      const p = profiled ? detail!.profile!.columns.find((x) => x.name === col.name) : null;
      if (p) {
        const n = detail!.profile!.rows || 1;
        const issue = p.issues.length ? Math.min(p.placeholders, n) : 0;
        out.set(col.name, {
          valid: (p.filled - issue) / n,
          blank: p.blanks / n,
          issue: issue / n,
          note: p.blanks ? `${pct(p.blanks / n)} blank` : p.issues[0] ?? (p.unique ? "all unique" : `${p.distinct.toLocaleString()} distinct`),
        });
      } else {
        const n = rows.rows.length || 1;
        const blanks = rows.rows.filter((r) => r[c] === null || r[c] === "").length;
        out.set(col.name, { valid: (n - blanks) / n, blank: blanks / n, issue: 0, note: blanks ? `${pct(blanks / n)} blank` : "no blanks" });
      }
    });
    return out;
  }, [rows, detail, step, total, side]);

  function showRemoved() {
    setSide("removed");
    if (!removed) transformApi.removed(projectId, query.id, step).then(setRemoved).catch((e) => setPreviewError(e.message));
  }

  function openMenu(e: ReactMouseEvent, column: string, type: LogicalType) {
    e.preventDefault();
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ column, type, x: e.type === "contextmenu" ? e.clientX : box.left, y: e.type === "contextmenu" ? e.clientY : box.bottom + 2 });
  }

  const shown = step === 0 ? (query.kind === "lookup" ? `Copy of ${query.base_table}` : `Source: ${query.source_table}`) : query.steps[step - 1]?.label;
  const aiSteps = query.steps.filter((s) => s.status === "AI").length;

  return (
    <>
      <section className="pane-center" aria-label={`${query.name} data`}>
        <div className="editor-toolbar">
          <button className="tool" onClick={() => onChange(() => transformApi.undo(projectId))} disabled={!canUndo} title="Undo last change" aria-label="Undo">
            <Undo2 size={16} />
          </button>
          <span className="tool-sep" />
          <button className="tool labelled" onClick={() => add("remove_duplicates", {})} disabled={!!query.error}>
            <CopyMinus size={15} aria-hidden="true" /> Remove duplicates
          </button>
          <button className="tool labelled" onClick={openCombine("column")} disabled={!!query.error} title="Add a column worked out from other columns">
            <Plus size={15} aria-hidden="true" /> Add column
          </button>
          <button className="tool labelled" onClick={openCombine("merge")} disabled={!!query.error || !others.length} title="Bring in columns from another table by matching keys">
            <Combine size={15} aria-hidden="true" /> Merge
          </button>
          <button className="tool labelled" onClick={openCombine("append")} disabled={!!query.error || !others.length} title="Add the rows of other tables under this one">
            <Rows3 size={15} aria-hidden="true" /> Append
          </button>
          <button
            className="tool labelled"
            onClick={async () => {
              const name = await dialogs.prompt({ title: "Rename table", label: "Table name", initial: query.name, confirmLabel: "Rename" });
              if (name && name !== query.name) void onChange(() => transformApi.rename(projectId, query.id, name));
            }}
          >
            Rename
          </button>
          {query.kind === "lookup" && (
            <button className="tool labelled danger" onClick={() => onChange(() => transformApi.removeTable(projectId, query.id))}>
              Remove table
            </button>
          )}
          <button className="tool labelled ai" onClick={onClean} disabled={!hasKey || cleaning || !!query.error} title={hasKey ? "Let the AI suggest steps for this table" : "Add an API key in Settings first"}>
            <Sparkles size={15} aria-hidden="true" /> {cleaning ? "AI is cleaning…" : "Clean with AI"}
          </button>
          <span className="grow" />
          <span className="toolbar-note">
            Step {step} of {total}
            {query.rows !== null && `, ${query.rows.toLocaleString()} rows, ${query.columns.length} columns`}
          </span>
        </div>

        <div className="step-bar">
          <span className="step-bar-label">Step {step}</span>
          <span className="step-bar-text">{shown}</span>
          {impact && (
            <span className="segmented compact">
              <button className={side === "before" ? "on" : ""} onClick={() => setSide("before")}>
                Before
              </button>
              <button className={side === "after" ? "on" : ""} onClick={() => setSide("after")}>
                After
              </button>
              {dropped > 0 && (
                <button className={side === "removed" ? "on" : ""} onClick={showRemoved}>
                  Removed ({dropped.toLocaleString()})
                </button>
              )}
            </span>
          )}
        </div>

        {(error || query.error || run || (query.kind === "lookup" && query.status === "AI")) && (
          <div className="editor-messages">
            {error && <p className="bad small">{error}</p>}
            {query.error && <div className="notice bad-notice small">{query.error}</div>}
            {query.kind === "lookup" && query.status === "AI" && (
              <div className="ai-strip">
                <span className="ai-tag">Suggested by AI</span>
                <span className="grow">
                  Split off {query.base_table}. {query.reason}
                </span>
                <button className="secondary small-button" onClick={() => onChange(() => transformApi.approveAll(projectId, query.id))}>
                  Accept table
                </button>
              </div>
            )}
            {run && <RunResult run={run} />}
          </div>
        )}

        <div className="grid-scroll">
          {previewError && <p className="bad pad">{previewError}</p>}
          {side === "removed" && removed && !removed.comparable && (
            <p className="muted small pad">This step also changed values, so its removed rows can't be told apart from changed ones.</p>
          )}
          {!rows && !previewError && !query.error && <p className="muted pad">Loading…</p>}
          {rows && (
            <table className={`data-grid ${side === "removed" ? "removed-rows" : ""}`}>
              <thead>
                <tr>
                  <th className="row-number" aria-label="Row" />
                  {rows.columns.map((c) => {
                    const q = quality.get(c.name);
                    const typeChanged = impact?.type_changes.some((t) => t.column === c.name) || impact?.added_columns.includes(c.name);
                    return (
                      <th key={c.name} className={typeChanged && side === "after" ? "touched" : ""} onContextMenu={(e) => openMenu(e, c.name, c.type)}>
                        <button className="col-head" onClick={(e) => openMenu(e, c.name, c.type)} aria-haspopup="menu" disabled={side !== "after" || !!query.error}>
                          <TypeTag type={c.type} />
                          <span className="col-name">{c.name}</span>
                          <ChevronDown size={13} className="col-caret" aria-hidden="true" />
                        </button>
                        {q && (
                          <>
                            <span className="quality" aria-hidden="true">
                              <i className="q-valid" style={{ width: `${q.valid * 100}%` }} />
                              <i className="q-issue" style={{ width: `${q.issue * 100}%` }} />
                              <i className="q-blank" style={{ width: `${q.blank * 100}%` }} />
                            </span>
                            <span className="quality-note">{q.note}</span>
                          </>
                        )}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {rows.rows.map((row, r) => (
                  <tr key={r}>
                    <td className="row-number">{r + 1}</td>
                    {row.map((value, c) => (
                      <td key={c} className={`${rows.columns[c].type !== "text" ? "num" : ""} ${changed.has(`${r}:${c}`) ? "changed" : ""}`}>
                        {value === null ? <span className="null">null</span> : value}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <footer className="editor-status">
          {query.error ? <span className="bad">● A step failed</span> : <span className="ok">● All steps valid</span>}
          {rows && (
            <span>
              {side === "removed" ? "Removed rows: " : "Showing "}
              {rows.rows.length.toLocaleString()} of {rows.total.toLocaleString()} rows
            </span>
          )}
          <span>{step === total && side === "after" ? "Column quality from the whole table" : "Column quality from the rows on show"}</span>
          <span className="grow" />
          <span>Click or right-click a column for its actions</span>
        </footer>
      </section>

      <aside className="pane pane-right" aria-label="Applied steps">
        <div className="pane-head">
          <span>Applied steps</span>
          <span className="grow" />
          {aiSteps > 0 && (
            <button className="link small" onClick={() => onChange(() => transformApi.approveAll(projectId, query.id))}>
              Accept all {aiSteps}
            </button>
          )}
          <span className="pane-count">{total}</span>
        </div>
        <ol className="applied-steps">
          <li className={step === 0 ? "on" : ""}>
            <button className="step-row" onClick={() => setStep(0)}>
              <span className="step-n">0</span>
              <span className="step-body">
                <span className="step-title">{query.kind === "lookup" ? `Copy of ${query.base_table}` : "Source"}</span>
                <span className="step-detail">{query.kind === "lookup" ? (query.split_after_step ? `after its step ${query.split_after_step}` : "as loaded") : query.source_table}</span>
              </span>
            </button>
          </li>
          {query.steps.map((s, i) => {
            const n = i + 1;
            const warnings = query.warnings?.filter((w) => w.step === n) ?? [];
            return (
              <li key={s.id} className={`${step === n ? "on" : ""} ${s.status === "AI" ? "ai" : ""}`}>
                <button className="step-row" onClick={() => setStep(n)}>
                  <span className="step-n">{n}</span>
                  <span className="step-body">
                    {s.status === "AI" && <span className="ai-tag">Suggested by AI</span>}
                    {s.status !== "AI" && s.by === "autopilot" && <span className="ai-tag autopilot-tag">Added by Autopilot</span>}
                    <span className="step-title">{s.label}</span>
                    {s.reason && <span className="step-detail">{s.reason}</span>}
                    {warnings.map((w) => (
                      <span key={w.text} className="step-warning" role="note">
                        <AlertTriangle size={12} aria-hidden="true" /> {w.text}
                      </span>
                    ))}
                  </span>
                </button>
                {s.status === "AI" ? (
                  <span className="step-decide">
                    <button className="small-button" onClick={() => onChange(() => transformApi.approve(projectId, query.id, s.id))}>
                      Accept
                    </button>
                    <button className="secondary small-button" onClick={() => onChange(() => transformApi.remove(projectId, query.id, s.id))}>
                      Reject
                    </button>
                  </span>
                ) : (
                  <span className="step-tools">
                    <button className="tool" title="Move up" aria-label={`Move step ${n} up`} disabled={i === 0} onClick={() => onChange(() => transformApi.move(projectId, query.id, s.id, i - 1))}>
                      <ArrowUp size={13} />
                    </button>
                    <button className="tool" title="Move down" aria-label={`Move step ${n} down`} disabled={i === total - 1} onClick={() => onChange(() => transformApi.move(projectId, query.id, s.id, i + 1))}>
                      <ArrowDown size={13} />
                    </button>
                    <button className="tool danger" title="Remove step" aria-label={`Remove step ${n}`} onClick={() => onChange(() => transformApi.remove(projectId, query.id, s.id))}>
                      <X size={13} />
                    </button>
                  </span>
                )}
              </li>
            );
          })}
        </ol>
        {total === 0 && !query.error && (
          <p className="muted small pane-note">No steps yet. Use a column's menu, or {hasKey ? "Clean with AI." : "add an API key to let the AI suggest steps."}</p>
        )}
        {impact && (
          <div className="step-effect">
            <h3>What step {step} changed</h3>
            <dl>
              <dt>Rows</dt>
              <dd>
                {impact.rows_before.toLocaleString()}
                {impact.rows_after !== impact.rows_before ? <> → {impact.rows_after.toLocaleString()}</> : <span className="muted"> unchanged</span>}
              </dd>
              {impact.type_changes.map((t) => (
                <FragmentRow key={`t${t.column}`} label={t.column} value={`${t.from} → ${t.to}`} />
              ))}
              {impact.blank_changes.map((b) => (
                <FragmentRow key={`b${b.column}`} label={`Blanks in ${b.column}`} value={`${b.before.toLocaleString()} → ${b.after.toLocaleString()}`} />
              ))}
              {impact.added_columns.length > 0 && <FragmentRow label="Added" value={impact.added_columns.join(", ")} />}
              {impact.removed_columns.length > 0 && <FragmentRow label="Removed" value={impact.removed_columns.join(", ")} />}
              {changed.size > 0 && <FragmentRow label="Cells changed on show" value={changed.size.toLocaleString()} />}
            </dl>
          </div>
        )}
      </aside>

      {menu && <ColumnMenu menu={menu} onClose={() => setMenu(null)} onAdd={add} />}
      {combine && (
        <PopMenu x={combine.x} y={combine.y} onClose={() => setCombine(null)} label={combine.kind === "merge" ? "Merge" : combine.kind === "append" ? "Append" : "Add column"}>
          {combine.kind === "column" ? (
            <AddColumnForm query={query} onAdd={(params) => (setCombine(null), void add("add_column", params))} />
          ) : combine.kind === "merge" ? (
            <MergeForm query={query} others={others} onAdd={(params) => (setCombine(null), void add("merge", params))} />
          ) : (
            <AppendForm query={query} others={others} onAdd={(params) => (setCombine(null), void add("append", params))} />
          )}
        </PopMenu>
      )}
    </>
  );
}

function FragmentRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

const REORDERS = new Set(["merge", "append", "filter_rows", "remove_duplicates"]);

const pct = (v: number) => `${v < 0.01 && v > 0 ? "<1" : Math.round(v * 100)}%`;

const OPERATORS: { value: string; label: string; needs: "value" | "values" | null }[] = [
  { value: "equals", label: "equals", needs: "value" },
  { value: "not_equals", label: "does not equal", needs: "value" },
  { value: "greater_than", label: "is greater than", needs: "value" },
  { value: "less_than", label: "is less than", needs: "value" },
  { value: "at_least", label: "is at least", needs: "value" },
  { value: "at_most", label: "is at most", needs: "value" },
  { value: "in", label: "is one of (comma separated)", needs: "values" },
  { value: "is_not_blank", label: "is not blank", needs: null },
  { value: "is_blank", label: "is blank", needs: null },
];

/** A column's actions. Each one adds a checked step, the same steps the AI uses. */
function ColumnMenu(props: {
  menu: { column: string; type: LogicalType; x: number; y: number };
  onClose: () => void;
  onAdd: (op: string, params: Record<string, unknown>) => Promise<boolean>;
}) {
  const { menu, onClose, onAdd } = props;
  const { column, type } = menu;
  const [panel, setPanel] = useState<"main" | "type" | "case" | "keep" | "rename" | "replace" | "fill">("main");
  const [operator, setOperator] = useState("equals");
  const [first, setFirst] = useState("");
  const [second, setSecond] = useState("");
  const text = type === "text";

  const run = async (op: string, params: Record<string, unknown>) => {
    onClose();
    await onAdd(op, params);
  };

  const field = (label: string, value: string, set: (v: string) => void, placeholder = "") => (
    <label className="pop-field">
      <span>{label}</span>
      <input value={value} onChange={(e) => set(e.target.value)} placeholder={placeholder} />
    </label>
  );

  const form = (title: string, body: React.ReactNode, submit: () => void, ready: boolean) => (
    <form
      className="pop-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) submit();
      }}
    >
      <div className="pop-title">
        <button type="button" className="link small" onClick={() => setPanel("main")}>
          ‹ Back
        </button>
        <strong>{title}</strong>
      </div>
      {body}
      <button type="submit" disabled={!ready}>
        Add step
      </button>
    </form>
  );

  const item = (label: string, onClick: () => void, opts: { sub?: boolean; danger?: boolean; disabled?: boolean } = {}) => (
    <button role="menuitem" className={`${opts.sub ? "has-sub" : ""} ${opts.danger ? "danger" : ""}`} onClick={onClick} disabled={opts.disabled}>
      {label}
    </button>
  );

  const operatorNeeds = OPERATORS.find((o) => o.value === operator)?.needs ?? null;

  return (
    <PopMenu x={menu.x} y={menu.y} onClose={onClose} label={`Column ${column}`}>
      {panel === "main" && (
        <>
          <div className="pop-head">
            <TypeTag type={type} /> {column}
          </div>
          {item("Change type", () => setPanel("type"), { sub: true })}
          {item("Rename", () => {
            setFirst(column);
            setPanel("rename");
          })}
          {item("Replace values", () => setPanel("replace"), { disabled: !text })}
          {item("Fill blanks", () => setPanel("fill"))}
          {item("Treat placeholders as blank", () => void run("blanks_to_null", { columns: [column] }), { disabled: !text })}
          {item("Trim spaces", () => void run("trim", { columns: [column] }), { disabled: !text })}
          {item("Change case", () => setPanel("case"), { sub: true, disabled: !text })}
          <div className="menu-separator" role="separator" />
          {item("Keep rows where", () => setPanel("keep"), { sub: true })}
          {item("Remove duplicates by this column", () => void run("remove_duplicates", { columns: [column] }))}
          <div className="menu-separator" role="separator" />
          {item("Remove column", () => void run("remove_columns", { columns: [column] }), { danger: true })}
        </>
      )}
      {panel === "type" && (
        <>
          <div className="pop-title">
            <button className="link small" onClick={() => setPanel("main")}>
              ‹ Back
            </button>
            <strong>Change {column} to</strong>
          </div>
          {item("Text", () => void run("change_type", { column, to: "text" }))}
          {item("Whole number", () => void run("change_type", { column, to: "integer" }))}
          {item("Decimal number", () => void run("change_type", { column, to: "decimal" }))}
          {item("Date, day first (31/12/2024)", () => void run("change_type", { column, to: "date", date_order: "dmy" }))}
          {item("Date, month first (12/31/2024)", () => void run("change_type", { column, to: "date", date_order: "mdy" }))}
          {item("True or false", () => void run("change_type", { column, to: "boolean" }))}
        </>
      )}
      {panel === "case" && (
        <>
          <div className="pop-title">
            <button className="link small" onClick={() => setPanel("main")}>
              ‹ Back
            </button>
            <strong>Change case</strong>
          </div>
          {item("UPPER CASE", () => void run("change_case", { columns: [column], case: "upper" }))}
          {item("lower case", () => void run("change_case", { columns: [column], case: "lower" }))}
          {item("Title Case", () => void run("change_case", { columns: [column], case: "title" }))}
        </>
      )}
      {panel === "rename" && form("Rename column", field("New name", first, setFirst), () => void run("rename_column", { column, to: first.trim() }), !!first.trim() && first.trim() !== column)}
      {panel === "fill" && form("Fill blanks", field("Fill with", first, setFirst, text ? "Unknown" : "0"), () => void run("fill_blanks", { column, value: first }), first !== "")}
      {panel === "replace" &&
        form(
          "Replace values",
          <>
            {field("Find", first, setFirst)}
            {field("Replace with (empty makes it blank)", second, setSecond)}
          </>,
          () => void run("replace_values", { column, replacements: [{ from: first, to: second === "" ? null : second }] }),
          first !== "",
        )}
      {panel === "keep" &&
        form(
          `Keep rows where ${column}`,
          <>
            <label className="pop-field">
              <span>Condition</span>
              <select value={operator} onChange={(e) => setOperator(e.target.value)}>
                {OPERATORS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            {operatorNeeds && field(operatorNeeds === "values" ? "Values" : "Value", first, setFirst)}
          </>,
          () =>
            void run("filter_rows", {
              column,
              operator,
              ...(operatorNeeds === "value" ? { value: first } : {}),
              ...(operatorNeeds === "values" ? { values: first.split(",").map((v) => v.trim()).filter(Boolean) } : {}),
            }),
          operatorNeeds === null || first.trim() !== "",
        )}
    </PopMenu>
  );
}

/** A calculated column, like Power Query's Custom Column: worked out row by row from this table's columns. */
function AddColumnForm({ query, onAdd }: { query: QuerySummary; onAdd: (params: Record<string, unknown>) => void }) {
  const [name, setName] = useState("");
  const [expression, setExpression] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);

  function insert(text: string) {
    const el = box.current;
    const at = el?.selectionStart ?? expression.length;
    const next = expression.slice(0, at) + text + expression.slice(el?.selectionEnd ?? at);
    setExpression(next);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(at + text.length, at + text.length);
    });
  }

  return (
    <form
      className="pop-form combine-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim() && expression.trim()) onAdd({ name: name.trim(), expression: expression.trim() });
      }}
    >
      <div className="pop-head">Add a column to {query.name}</div>
      <label className="pop-field">
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Total" />
      </label>
      <label className="pop-field">
        <span>Formula, worked out for each row</span>
        <textarea ref={box} className="formula-input" rows={3} value={expression} onChange={(e) => setExpression(e.target.value)} placeholder='[Qty] * [Price]   or   IF([Miles] > 20, "Long", "Short")' spellCheck={false} />
      </label>
      <div className="column-chips" aria-label="Columns to insert">
        {query.columns.map((c) => (
          <button key={c.name} type="button" className="chip-button" onClick={() => insert(`[${c.name}]`)} title={`Insert [${c.name}]`}>
            <TypeTag type={c.type} /> {c.name}
          </button>
        ))}
      </div>
      <p className="muted small pad-x">Use + - * /, comparisons, IF, SWITCH, AND, OR and NOT. Text goes in quotes.</p>
      <button type="submit" disabled={!name.trim() || !expression.trim()}>
        Add column step
      </button>
    </form>
  );
}

/** Merge Queries: match keys with another table and bring some of its columns in. */
function MergeForm({ query, others, onAdd }: { query: QuerySummary; others: QuerySummary[]; onAdd: (params: Record<string, unknown>) => void }) {
  const usable = others.filter((q) => !q.error && q.columns.length);
  const [otherId, setOtherId] = useState(usable[0]?.id ?? "");
  const other = usable.find((q) => q.id === otherId);
  // Start from a column the two tables share by name, the usual key.
  const guess = (o?: QuerySummary) => {
    const shared = query.columns.find((c) => o?.columns.some((x) => x.name.toLowerCase() === c.name.toLowerCase()));
    return { left: shared?.name ?? query.columns[0]?.name ?? "", right: o?.columns.find((x) => x.name.toLowerCase() === shared?.name.toLowerCase())?.name ?? o?.columns[0]?.name ?? "" };
  };
  const [keys, setKeys] = useState(() => guess(other));
  const [bring, setBring] = useState<string[]>([]);
  const [how, setHow] = useState<"left" | "inner">("left");
  const choices = other?.columns.filter((c) => c.name !== keys.right) ?? [];

  if (!usable.length) return <p className="muted small pad">There is no other table to merge with.</p>;
  return (
    <form
      className="pop-form combine-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (other && bring.length) onAdd({ query: other.id, left_on: [keys.left], right_on: [keys.right], columns: bring, how });
      }}
    >
      <div className="pop-head">Merge into {query.name}</div>
      <label className="pop-field">
        <span>Table to look up from</span>
        <select
          value={otherId}
          onChange={(e) => {
            const next = usable.find((q) => q.id === e.target.value);
            setOtherId(e.target.value);
            setKeys(guess(next));
            setBring([]);
          }}
        >
          {usable.map((q) => (
            <option key={q.id} value={q.id}>
              {q.name}
            </option>
          ))}
        </select>
      </label>
      <div className="pop-pair">
        <label className="pop-field">
          <span>Match {query.name}[…]</span>
          <select value={keys.left} onChange={(e) => setKeys({ ...keys, left: e.target.value })}>
            {query.columns.map((c) => (
              <option key={c.name}>{c.name}</option>
            ))}
          </select>
        </label>
        <label className="pop-field">
          <span>with {other?.name}[…]</span>
          <select value={keys.right} onChange={(e) => setKeys({ ...keys, right: e.target.value })}>
            {other?.columns.map((c) => (
              <option key={c.name}>{c.name}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="pop-field">
        <span>Columns to bring in</span>
        <ul className="value-list">
          {choices.map((c) => (
            <li key={c.name}>
              <label>
                <input type="checkbox" checked={bring.includes(c.name)} onChange={() => setBring((b) => (b.includes(c.name) ? b.filter((x) => x !== c.name) : [...b, c.name]))} />
                <TypeTag type={c.type} />
                <span>{c.name}</span>
              </label>
            </li>
          ))}
        </ul>
      </div>
      <label className="pop-field">
        <span>Rows</span>
        <select value={how} onChange={(e) => setHow(e.target.value as "left" | "inner")}>
          <option value="left">Keep every row of {query.name} (blank where nothing matches)</option>
          <option value="inner">Keep only rows that match</option>
        </select>
      </label>
      <p className="muted small pad-x">Only when a value must live in this table. To report across tables, relate them in Model instead.</p>
      <button type="submit" disabled={!bring.length}>
        Add merge step
      </button>
    </form>
  );
}

/** Append Queries: stack other tables' rows under this one; columns are matched by name. */
function AppendForm({ query, others, onAdd }: { query: QuerySummary; others: QuerySummary[]; onAdd: (params: Record<string, unknown>) => void }) {
  const usable = others.filter((q) => !q.error);
  const [picked, setPicked] = useState<string[]>([]);
  const mine = new Set(query.columns.map((c) => c.name.toLowerCase()));
  const fit = (q: QuerySummary) => {
    const theirs = new Set(q.columns.map((c) => c.name.toLowerCase()));
    const missing = query.columns.filter((c) => !theirs.has(c.name.toLowerCase())).length;
    const extra = q.columns.filter((c) => !mine.has(c.name.toLowerCase())).length;
    return missing || extra ? `${missing} missing, ${extra} extra columns` : "same columns";
  };
  if (!usable.length) return <p className="muted small pad">There is no other table to append.</p>;
  return (
    <form
      className="pop-form combine-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (picked.length) onAdd({ queries: picked });
      }}
    >
      <div className="pop-head">Append to {query.name}</div>
      <ul className="value-list">
        {usable.map((q) => (
          <li key={q.id}>
            <label>
              <input type="checkbox" checked={picked.includes(q.id)} onChange={() => setPicked((p) => (p.includes(q.id) ? p.filter((x) => x !== q.id) : [...p, q.id]))} />
              <span>{q.name}</span>
              <small className="muted">{q.rows === null ? "" : `${q.rows.toLocaleString()} rows, `}{fit(q)}</small>
            </label>
          </li>
        ))}
      </ul>
      <p className="muted small pad-x">Columns are matched by name; a column a table lacks is left blank for its rows.</p>
      <button type="submit" disabled={!picked.length}>
        Add append step
      </button>
    </form>
  );
}

function RunResult({ run }: { run: AIRun | { error: string } }) {
  if ("error" in run) return <div className="notice bad-notice small">{run.error}</div>;
  return (
    <div className="ai-strip">
      <span className="ai-tag">AI</span>
      <span className="grow">
        {run.summary || "The AI reviewed this table."}{" "}
        <span className="muted">
          {run.added.length ? `Suggested ${run.added.length} ${run.added.length === 1 ? "step" : "steps"}; accept or reject them in Applied steps.` : "No new steps needed."}
          {run.new_tables?.length > 0 && ` Split off ${run.new_tables.join(", ")}.`}
          {run.rejected.length > 0 && ` ${run.rejected.length} refused by the checks: ${run.rejected.map((r) => r.error).join("; ")}`}
        </span>
      </span>
    </div>
  );
}
