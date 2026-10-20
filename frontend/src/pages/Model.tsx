import { useEffect, useState } from "react";
import {
  api,
  formatMeasure,
  modelApi,
  type ModelRun,
  type ModelState,
  type ModelTable,
  type Relationship,
} from "../api";
import { useDataset } from "./Dataset";

export function Model() {
  const { project, refresh } = useDataset();
  const [state, setState] = useState<ModelState | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [run, setRun] = useState<ModelRun | { error: string } | null>(null);

  useEffect(() => {
    modelApi.state(project.id).then(setState).catch((e) => setError(e.message));
    api.getSettings().then((s) => setHasKey(s.has_api_key)).catch(() => setHasKey(false));
  }, [project.id]);

  async function change(action: () => Promise<ModelState>) {
    setError(null);
    try {
      setState(await action());
      void refresh(); // keeps the publish status in the header current
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function build() {
    setBuilding(true);
    setRun(null);
    setError(null);
    try {
      const result = await modelApi.ai(project.id);
      setRun(result);
      setState(result);
      void refresh();
    } catch (e) {
      setRun({ error: (e as Error).message });
    } finally {
      setBuilding(false);
    }
  }

  if (!state) return <p className={error ? "bad" : "muted"}>{error ?? "Loading…"}</p>;

  if (state.tables.length === 0) {
    return (
      <div className="empty-state">
        <h2>Model</h2>
        <p className="muted">Load and clean data first. The model is built from the tables on the Transform tab.</p>
      </div>
    );
  }

  const pending =
    state.relationships.filter((r) => r.status === "AI").length +
    state.measures.filter((m) => m.status === "AI").length +
    (state.date_table?.status === "AI" ? 1 : 0);

  return (
    <div className="model">
      <div className="toolbar">
        <button onClick={build} disabled={!hasKey || building}>
          {building ? "AI is building the model…" : "Build model with AI"}
        </button>
        <button className="secondary" onClick={() => change(() => modelApi.approve(project.id))} disabled={pending === 0}>
          {pending ? `Approve all AI changes (${pending})` : "All approved"}
        </button>
        <button className="secondary" onClick={() => change(() => modelApi.undo(project.id))} disabled={!state.can_undo}>
          Undo last change
        </button>
        {!hasKey && <span className="muted small">Add an API key in Settings so the AI can build the model.</span>}
      </div>
      {error && <p className="bad">{error}</p>}
      {run && <RunResult run={run} />}

      <Diagram tables={state.tables} relationships={state.relationships} />

      <section>
        <h3 className="section-title">Relationships</h3>
        {state.relationships.length === 0 ? (
          <p className="muted small">No relationships yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>From (many)</th>
                  <th>To (one)</th>
                  <th className="num">Match</th>
                  <th>Notes</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {state.relationships.map((r) => (
                  <RelationshipRow
                    key={r.id}
                    rel={r}
                    onApprove={() => change(() => modelApi.approve(project.id, r.id))}
                    onRemove={() => change(() => modelApi.removeRelationship(project.id, r.id))}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h3 className="section-title">Date table</h3>
        {state.date_table ? (
          <div className="ai-card">
            {state.date_table.status === "AI" && <span className="badge ai" title="Suggested by the AI, waiting for your review">AI</span>}
            <div className="grow">
              {state.date_table.error ? (
                <p className="bad small">{state.date_table.error}</p>
              ) : (
                <p>
                  <strong>Date</strong> from {state.date_table.first} to {state.date_table.last}, with Year, Quarter, Month
                  and Day of Week columns.
                </p>
              )}
              {state.date_table.reason && <p className="muted small">{state.date_table.reason}</p>}
            </div>
            <span className="step-tools">
              {state.date_table.status === "AI" && (
                <button className="link small" onClick={() => change(() => modelApi.approve(project.id, "date"))}>
                  Approve
                </button>
              )}
              <button className="icon danger" title="Remove the date table" onClick={() => change(() => modelApi.removeDateTable(project.id))}>
                ×
              </button>
            </span>
          </div>
        ) : (
          <p className="muted small">No date table. The AI adds one when your data has dates.</p>
        )}
      </section>

      <section>
        <h3 className="section-title">Measures</h3>
        {state.measures.length === 0 ? (
          <p className="muted small">No measures yet.</p>
        ) : (
          <div className="measure-grid">
            {state.measures.map((m) => (
              <div key={m.id} className={`measure ${m.error ? "broken" : ""}`}>
                <div className="measure-head">
                  <span className="measure-name">{m.name}</span>
                  {m.status === "AI" && <span className="badge ai" title="Suggested by the AI, waiting for your review">AI</span>}
                </div>
                <div className="measure-value">{m.error ? "—" : formatMeasure(m.value, m.format)}</div>
                <code className="measure-def">{m.label}</code>
                {m.error && <p className="bad small">{m.error}</p>}
                {(m.description || m.reason) && <p className="muted small">{m.description || m.reason}</p>}
                <div className="measure-tools">
                  {m.status === "AI" && (
                    <button className="link small" onClick={() => change(() => modelApi.approve(project.id, m.id))}>
                      Approve
                    </button>
                  )}
                  <button className="link small danger" onClick={() => change(() => modelApi.removeMeasure(project.id, m.id))}>
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function RunResult({ run }: { run: ModelRun | { error: string } }) {
  if ("error" in run) return <div className="notice bad-notice small">{run.error}</div>;
  return (
    <div className="ai-card">
      <span className="badge ai">AI</span>
      <div>
        <p>{run.summary || "The AI reviewed the model."}</p>
        <p className="muted small">
          {run.added.length ? `Added ${run.added.length} ${run.added.length === 1 ? "item" : "items"}.` : "Nothing new to add."}
          {run.refused.length > 0 && ` ${run.refused.length} refused by the checks:`}
        </p>
        {run.refused.map((r, i) => (
          <p key={i} className="issue small">
            {r.label}: {r.error}
          </p>
        ))}
      </div>
    </div>
  );
}

function RelationshipRow({ rel, onApprove, onRemove }: { rel: Relationship; onApprove: () => void; onRemove: () => void }) {
  const rate = rel.checks?.match_rate;
  return (
    <tr className={rel.active ? "" : "inactive"}>
      <td>
        <strong>{rel.from_name}</strong>[{rel.from_column}]
      </td>
      <td>
        <strong>{rel.to_name}</strong>[{rel.to_column}]
      </td>
      <td className="num">{rate === undefined ? "" : `${Math.round(rate * 100)}%`}</td>
      <td className="small">
        {rel.error && <div className="bad">{rel.error}</div>}
        {!rel.active && <div className="muted">Inactive: another path already connects these tables.</div>}
        {rel.checks?.unmatched_examples?.length > 0 && (
          <div className="issue">
            {rel.checks.keyed_rows - rel.checks.matched_rows} rows have no match (e.g. {rel.checks.unmatched_examples.join(", ")})
          </div>
        )}
        {rel.checks?.cardinality === "one_to_one" && <div className="muted">One to one</div>}
        {rel.reason && <div className="muted">{rel.reason}</div>}
      </td>
      <td>
        <span className="step-tools">
          {rel.status === "AI" && <span className="badge ai" title="Suggested by the AI, waiting for your review">AI</span>}
          {rel.status === "AI" && (
            <button className="link small" onClick={onApprove}>
              Approve
            </button>
          )}
          <button className="icon danger" title="Remove relationship" onClick={onRemove}>
            ×
          </button>
        </span>
      </td>
    </tr>
  );
}

// Diagram: facts in the middle column, dimensions on either side, the date table on the right.

const BOX_WIDTH = 190;
const HEADER = 30;
const ROW = 19;
const MAX_ROWS = 8;
const GAP_X = 90;
const GAP_Y = 26;
const PAD = 16;

type Placed = { table: ModelTable; x: number; y: number; rows: string[]; height: number };

function layout(tables: ModelTable[]): { placed: Map<string, Placed>; width: number; height: number } {
  const facts = tables.filter((t) => t.kind === "fact" || t.kind === "table");
  const dims = tables.filter((t) => t.kind === "dimension");
  const dates = tables.filter((t) => t.kind === "date");
  const columns: ModelTable[][] = [[], facts, []];
  dims.forEach((t, i) => columns[i % 2 === 0 ? 0 : 2].push(t));
  columns[2].push(...dates);
  const used = columns.filter((c) => c.length > 0);
  const placed = new Map<string, Placed>();
  let height = 0;
  used.forEach((column, ci) => {
    let y = PAD;
    column.forEach((table) => {
      // Keys first, then the rest; long tables are cut with a "+ n more" row.
      const ordered = [...table.columns.filter((c) => c.key), ...table.columns.filter((c) => !c.key)].map((c) => c.name);
      const rows = ordered.length > MAX_ROWS ? [...ordered.slice(0, MAX_ROWS - 1), `+ ${ordered.length - MAX_ROWS + 1} more`] : ordered;
      const boxHeight = HEADER + rows.length * ROW + 8;
      placed.set(table.id, { table, x: PAD + ci * (BOX_WIDTH + GAP_X), y, rows, height: boxHeight });
      y += boxHeight + GAP_Y;
    });
    height = Math.max(height, y);
  });
  return { placed, width: PAD * 2 + used.length * BOX_WIDTH + (used.length - 1) * GAP_X, height: height + PAD - GAP_Y };
}

function Diagram({ tables, relationships }: { tables: ModelTable[]; relationships: Relationship[] }) {
  const { placed, width, height } = layout(tables);

  function anchor(tableId: string, column: string, towards: number) {
    const box = placed.get(tableId);
    if (!box) return null;
    const index = box.rows.indexOf(column);
    const y = box.y + HEADER + (index >= 0 ? index : 0) * ROW + ROW / 2 + 2;
    const right = towards > box.x + BOX_WIDTH / 2;
    return { x: right ? box.x + BOX_WIDTH : box.x, y, right };
  }

  return (
    <div className="diagram">
      <svg viewBox={`0 0 ${width} ${height}`} width={width} role="img" aria-label="Model diagram">
        {relationships.map((rel) => {
          const toBox = placed.get(rel.to_table);
          const fromBox = placed.get(rel.from_table);
          if (!toBox || !fromBox) return null;
          const a = anchor(rel.from_table, rel.from_column, toBox.x + BOX_WIDTH / 2);
          const b = anchor(rel.to_table, rel.to_column, fromBox.x + BOX_WIDTH / 2);
          if (!a || !b) return null;
          const bend = Math.max(30, Math.abs(b.x - a.x) / 2);
          const path = `M ${a.x} ${a.y} C ${a.x + (a.right ? bend : -bend)} ${a.y}, ${b.x + (b.right ? bend : -bend)} ${b.y}, ${b.x} ${b.y}`;
          return (
            <g key={rel.id} className={`link ${rel.active ? "" : "inactive"} ${rel.error ? "broken" : ""}`}>
              <path d={path} />
              <text x={a.x + (a.right ? 6 : -12)} y={a.y - 4}>
                *
              </text>
              <text x={b.x + (b.right ? 6 : -12)} y={b.y - 4}>
                1
              </text>
            </g>
          );
        })}
        {[...placed.values()].map(({ table, x, y, rows, height: h }) => (
          <g key={table.id} className={`box kind-${table.kind}`}>
            <rect x={x} y={y} width={BOX_WIDTH} height={h} rx={8} />
            <rect className="box-head" x={x} y={y} width={BOX_WIDTH} height={HEADER} rx={8} />
            <rect className="box-head" x={x} y={y + HEADER - 8} width={BOX_WIDTH} height={8} />
            <text className="box-title" x={x + 10} y={y + 20}>
              {table.name}
            </text>
            <text className="box-meta" x={x + BOX_WIDTH - 10} y={y + 20} textAnchor="end">
              {table.kind === "table" ? "" : table.kind} · {table.rows.toLocaleString()}
            </text>
            {rows.map((name, i) => {
              const column = table.columns.find((c) => c.name === name);
              return (
                <text
                  key={name}
                  className={`box-col ${column?.key ? "key" : ""} ${column?.hidden ? "hidden-col" : ""}`}
                  x={x + 10}
                  y={y + HEADER + i * ROW + 15}
                >
                  {column?.key ? "⚷ " : ""}
                  {name.length > 24 ? `${name.slice(0, 23)}…` : name}
                </text>
              );
            })}
          </g>
        ))}
      </svg>
    </div>
  );
}
