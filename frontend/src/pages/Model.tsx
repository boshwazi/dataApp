import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { Calendar, Check, ChevronDown, ChevronRight, Eye, EyeOff, Plus, Sparkles, Table2, Trash2, Undo2, X } from "lucide-react";
import {
  api,
  formatMeasure,
  modelApi,
  type Aggregation,
  type TimeCalc,
  type Measure,
  type MeasureBody,
  type MeasureCheck,
  type MeasureFilter,
  type MeasureFormat,
  type ModelRun,
  type ModelState,
  type ModelTable,
  type Relationship,
  type RelationshipCheck,
} from "../api";
import { useToast } from "../components/ui";
import { useDataset } from "./Dataset";
import { TypeTag } from "./Transform";

type Selection = { kind: "measure"; id: string } | { kind: "new" } | { kind: "rel"; id: string } | { kind: "table"; id: string } | null;

/** What the formula bar and properties pane edit: a measure as text plus its settings. */
type Draft = {
  id?: string;
  name: string;
  text: string;
  table: string | null;
  format: MeasureFormat;
  description: string;
  folder: string;
  filters: Measure["filters"];
  useRelationship: string | null;
};

// The formula bar writes aggregates the Power BI way: SUM([Qty] * [Price]), COUNTROWS(), DISTINCTCOUNT([Customer]).
const FUNCTIONS: Record<Aggregation, string> = {
  sum: "SUM",
  average: "AVERAGE",
  count: "COUNT",
  count_distinct: "DISTINCTCOUNT",
  min: "MIN",
  max: "MAX",
};
const BY_FUNCTION: Record<string, Aggregation> = { SUM: "sum", AVERAGE: "average", AVG: "average", COUNT: "count", COUNTROWS: "count", DISTINCTCOUNT: "count_distinct", MIN: "min", MAX: "max" };

// Time intelligence over another measure: YTD([Revenue]), LASTYEAR([Revenue]), ROLLING([Revenue], 3).
const TIME_FUNCTIONS: Record<string, TimeCalc> = { YTD: "ytd", QTD: "qtd", MTD: "mtd", LASTYEAR: "prior_year", LASTYEARYTD: "prior_year_ytd", ROLLING: "rolling" };
const TIME_NAMES = Object.fromEntries(Object.entries(TIME_FUNCTIONS).map(([fn, calc]) => [calc, fn])) as Record<TimeCalc, string>;

type Parsed = Pick<MeasureBody, "kind" | "aggregation" | "expression" | "base" | "calc" | "months">;

function measureText(m: Pick<Measure, "kind" | "aggregation" | "expression" | "base" | "calc" | "months">): string {
  if (m.kind === "formula") return m.expression ?? "";
  if (m.kind === "time" && m.calc) return `${TIME_NAMES[m.calc]}([${m.base ?? ""}]${m.calc === "rolling" ? `, ${m.months ?? 3}` : ""})`;
  if (m.aggregation === "count" && !m.expression) return "COUNTROWS()";
  return `${FUNCTIONS[m.aggregation ?? "sum"]}(${m.expression ?? ""})`;
}

function parseText(text: string): Parsed {
  // The closing bracket is optional so the bar knows what you mean while you are still typing.
  const match = /^\s*([A-Za-z]+)\s*\(([\s\S]*?)\)?\s*$/.exec(text);
  const calc = match && TIME_FUNCTIONS[match[1].toUpperCase()];
  if (match && calc) {
    const inner = /^\s*\[?([^\],]*)\]?\s*(?:,\s*(\d+))?/.exec(match[2]);
    return { kind: "time", aggregation: null, expression: null, base: inner?.[1].trim() ?? "", calc, months: inner?.[2] ? Number(inner[2]) : calc === "rolling" ? 3 : null };
  }
  const fn = match && BY_FUNCTION[match[1].toUpperCase()];
  if (match && fn) {
    const inner = match[2].trim();
    return { kind: "aggregate", aggregation: fn, expression: inner || null };
  }
  return { kind: "formula", aggregation: null, expression: text.trim() };
}

function bodyOf(draft: Draft): MeasureBody {
  const parsed = parseText(draft.text);
  return {
    name: draft.name.trim(),
    ...parsed,
    table: parsed.kind === "aggregate" ? draft.table : null,
    filters: parsed.kind === "aggregate" ? draft.filters : [],
    base: parsed.kind === "time" ? parsed.base : null,
    calc: parsed.kind === "time" ? parsed.calc : null,
    months: parsed.kind === "time" ? parsed.months : null,
    use_relationship: parsed.kind === "aggregate" ? draft.useRelationship : null,
    format: draft.format,
    description: draft.description,
    folder: draft.folder,
  };
}

function draftOf(m: Measure): Draft {
  return { id: m.id, name: m.name, text: measureText(m), table: m.table, format: m.format, description: m.description, folder: m.folder ?? "", filters: m.filters ?? [], useRelationship: m.use_relationship ?? null };
}

export function Model() {
  const { project, refresh } = useDataset();
  const [state, setState] = useState<ModelState | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [run, setRun] = useState<ModelRun | { error: string } | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => {
    modelApi.state(project.id).then(setState).catch((e) => setError(e.message));
    api.getSettings().then((s) => setHasKey(s.has_api_key)).catch(() => setHasKey(false));
  }, [project.id]);

  const change = useCallback(
    async (action: () => Promise<ModelState>) => {
      setError(null);
      try {
        const next = await action();
        setState(next);
        void refresh(); // keeps the publish status in the top bar current
        return next;
      } catch (e) {
        setError((e as Error).message);
        return null;
      }
    },
    [refresh],
  );

  function selectMeasure(m: Measure) {
    setSelection({ kind: "measure", id: m.id });
    setDraft(draftOf(m));
  }

  function newMeasure(tableId?: string) {
    const home = tableId ?? state?.tables.find((t) => t.kind === "fact")?.id ?? state?.tables[0]?.id ?? null;
    setSelection({ kind: "new" });
    setDraft({ name: "New measure", text: "SUM()", table: home, format: { type: "number", decimals: 0 }, description: "", folder: "", filters: [], useRelationship: null });
  }

  async function save() {
    if (!draft) return;
    const body = bodyOf(draft);
    const next = await change(() => (draft.id ? modelApi.updateMeasure(project.id, draft.id, body) : modelApi.addMeasure(project.id, body)));
    if (next) {
      const saved = next.measures.find((m) => m.name === body.name);
      if (saved) selectMeasure(saved);
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

  if (!state) return <p className={error ? "bad pad" : "muted pad"}>{error ?? "Loading…"}</p>;

  if (state.tables.length === 0) {
    return (
      <div className="empty-state">
        <h2>Model</h2>
        <p className="muted">Load and clean data first. The model is built from the tables on the Transform tab.</p>
      </div>
    );
  }

  const tableName = new Map(state.tables.map((t) => [t.id, t.name]));
  const loose = state.measures.filter((m) => !m.table || !tableName.has(m.table));
  const selectedMeasure = selection?.kind === "measure" ? state.measures.find((m) => m.id === selection.id) ?? null : null;
  const editing = selection?.kind === "new" || !!selectedMeasure;
  const toggle = (id: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="editor model-editor">
      <aside className="pane pane-left" aria-label="Fields">
        <div className="pane-head">
          <span>Data</span>
          <span className="grow" />
          <button className="tool labelled" onClick={() => newMeasure()} title="New measure">
            <Plus size={14} aria-hidden="true" /> Measure
          </button>
        </div>
        <ul className="tree" role="tree" aria-label="Tables, measures and columns">
          {loose.length > 0 && (
            <li role="treeitem" aria-expanded="true">
              <div className="tree-row table-row static">
                <span className="tree-name">Measures</span>
              </div>
              <ul role="group">
                {loose.map((m) => (
                  <MeasureLeaf key={m.id} measure={m} on={selectedMeasure?.id === m.id} onSelect={() => selectMeasure(m)} />
                ))}
              </ul>
            </li>
          )}
          {state.tables.map((t) => {
            const measures = state.measures.filter((m) => m.table === t.id);
            const expanded = open.has(t.id) || (selectedMeasure?.table === t.id) || (selection?.kind === "table" && selection.id === t.id);
            return (
              <li key={t.id} role="treeitem" aria-expanded={expanded}>
                <button
                  className={`tree-row table-row ${selection?.kind === "table" && selection.id === t.id ? "on" : ""}`}
                  onClick={() => {
                    toggle(t.id);
                    setSelection({ kind: "table", id: t.id });
                  }}
                >
                  {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                  {t.kind === "date" ? <Calendar size={14} aria-hidden="true" /> : <Table2 size={14} aria-hidden="true" />}
                  <span className="tree-name">{t.name}</span>
                  <span className="tree-count">{t.rows.toLocaleString()}</span>
                </button>
                {expanded && (
                  <ul role="group">
                    {measures.map((m) => (
                      <MeasureLeaf key={m.id} measure={m} on={selectedMeasure?.id === m.id} onSelect={() => selectMeasure(m)} />
                    ))}
                    {t.columns.map((c) => (
                      <li key={c.name} role="treeitem" className={`tree-row column-row ${c.hidden ? "is-hidden" : ""}`}>
                        <TypeTag type={c.type} />
                        <span className="tree-name">{c.name}</span>
                        <button
                          className="tool tree-action"
                          title={c.hidden ? "Show in reports" : "Hide from reports"}
                          aria-label={c.hidden ? `Show ${c.name}` : `Hide ${c.name}`}
                          onClick={() => change(() => modelApi.setHidden(project.id, t.id, c.name, !c.hidden))}
                        >
                          {c.hidden ? <EyeOff size={13} /> : <Eye size={13} />}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </aside>

      <section className="pane-center" aria-label="Model diagram">
        {editing && draft ? (
          <FormulaBar
            key={draft.id ?? "new"}
            projectId={project.id}
            draft={draft}
            state={state}
            onDraft={setDraft}
            onSave={save}
            onCancel={() => {
              if (selectedMeasure) setDraft(draftOf(selectedMeasure));
              else {
                setSelection(null);
                setDraft(null);
              }
            }}
          />
        ) : (
          <div className="formula-bar idle">
            <span className="formula-label">fx</span>
            <span className="muted small">Choose a measure to edit its formula, or add a new one.</span>
            <span className="grow" />
            <button className="tool labelled" onClick={() => newMeasure()}>
              <Plus size={14} aria-hidden="true" /> New measure
            </button>
          </div>
        )}
        {error && <p className="bad small editor-messages">{error}</p>}
        <Diagram
          projectId={project.id}
          state={state}
          selection={selection}
          onSelect={setSelection}
          onChange={change}
        />
        <footer className="editor-status">
          <span className={state.relationships.some((r) => r.error) || state.measures.some((m) => m.error) ? "bad" : "ok"}>
            ● {state.relationships.some((r) => r.error) || state.measures.some((m) => m.error) ? "Something in the model is broken" : "Model valid"}
          </span>
          <span>
            {state.tables.length} tables, {state.relationships.length} relationships, {state.measures.length} measures
          </span>
          <span className="grow" />
          <span>Drag a column onto another table's column to relate them</span>
        </footer>
      </section>

      <aside className="pane pane-right" aria-label="Properties">
        <Properties
          projectId={project.id}
          state={state}
          selection={selection}
          draft={draft}
          measure={selectedMeasure}
          hasKey={hasKey}
          building={building}
          run={run}
          onDraft={setDraft}
          onSave={save}
          onBuild={build}
          onNewMeasure={newMeasure}
          onSelect={(s) => {
            setSelection(s);
            if (s?.kind === "measure") {
              const m = state.measures.find((x) => x.id === s.id);
              if (m) setDraft(draftOf(m));
            }
          }}
          onChange={async (action) => {
            const next = await change(action);
            if (next && selection?.kind === "measure" && !next.measures.some((m) => m.id === selection.id)) setSelection(null);
            if (next && selection?.kind === "rel" && !next.relationships.some((r) => r.id === selection.id)) setSelection(null);
          }}
        />
      </aside>
    </div>
  );
}

function MeasureLeaf({ measure, on, onSelect }: { measure: Measure; on: boolean; onSelect: () => void }) {
  return (
    <li role="treeitem" aria-selected={on}>
      <button className={`tree-row column-row measure-row ${on ? "on" : ""} ${measure.error ? "broken" : ""}`} onClick={onSelect}>
        <span className="type-tag type-tag-measure">fx</span>
        <span className="tree-name">{measure.name}</span>
        {measure.status === "AI" && <span className="badge ai">AI</span>}
        {measure.status !== "AI" && measure.by === "autopilot" && <span className="badge autopilot-badge" title="Added by Autopilot">Autopilot</span>}
        {measure.inherited && <span className="badge" title="From the dataset this one extends; change it there">Inherited</span>}
      </button>
    </li>
  );
}

// Formula bar

type Suggestion = { label: string; insert: string; kind: "measure" | "column" | "function" };

function FormulaBar(props: {
  projectId: string;
  draft: Draft;
  state: ModelState;
  onDraft: (d: Draft) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const { projectId, draft, state, onDraft } = props;
  const input = useRef<HTMLInputElement>(null);
  const measureCanvas = useRef<HTMLCanvasElement | null>(null);
  const [check, setCheck] = useState<MeasureCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [caret, setCaret] = useState(draft.text.length);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);

  // Live check, a moment after typing stops. Nothing is saved.
  useEffect(() => {
    const abort = new AbortController();
    setChecking(true);
    const timer = setTimeout(() => {
      modelApi
        .checkMeasure(projectId, { ...bodyOf(draft), id: draft.id }, abort.signal)
        .then(setCheck)
        .catch((e) => !abort.signal.aborted && setCheck({ ok: false, error: (e as Error).message }))
        .finally(() => !abort.signal.aborted && setChecking(false));
    }, 350);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [projectId, draft]);

  const parsed = parseText(draft.text);
  const home = state.tables.find((t) => t.id === draft.table);
  const open = draft.text.lastIndexOf("[", caret - 1);
  const closed = draft.text.lastIndexOf("]", caret - 1);
  const typing = open > closed ? draft.text.slice(open + 1, caret) : null;
  const word = typing === null ? /([A-Za-z]+)$/.exec(draft.text.slice(0, caret))?.[1] ?? null : null;

  const suggestions = useMemo<Suggestion[]>(() => {
    if (typing !== null) {
      const q = typing.toLowerCase();
      const columns =
        parsed.kind === "aggregate" && home
          ? home.columns.map((c) => ({ label: c.name, insert: c.name, kind: "column" as const }))
          : [];
      // A time function works over a measure that isn't itself a time measure.
      const measures =
        parsed.kind === "formula" || parsed.kind === "time"
          ? state.measures
              .filter((m) => m.id !== draft.id && (parsed.kind === "formula" || m.kind !== "time"))
              .map((m) => ({ label: m.name, insert: m.name, kind: "measure" as const }))
          : [];
      return [...measures, ...columns].filter((s) => s.label.toLowerCase().includes(q)).slice(0, 8);
    }
    if (word && word.length >= 2 && open === -1 && !draft.text.includes("(")) {
      const q = word.toUpperCase();
      return [...Object.keys(BY_FUNCTION), ...Object.keys(TIME_FUNCTIONS), "IF", "SWITCH"]
        .filter((f) => f.startsWith(q) && f !== "AVG")
        .map((f) => ({ label: `${f}( )`, insert: f, kind: "function" as const }));
    }
    return [];
  }, [typing, word, parsed.kind, home, state.measures, draft.id, draft.text, open]);

  const showList = suggestions.length > 0 && !dismissed;

  function pick(s: Suggestion) {
    let text: string;
    let at: number;
    if (s.kind === "function") {
      const start = caret - (word?.length ?? 0);
      text = `${draft.text.slice(0, start)}${s.insert}(${draft.text.slice(caret)}`;
      at = start + s.insert.length + 1;
      if (!text.includes(")")) text += ")";
    } else {
      const rest = draft.text.slice(caret);
      const after = rest.startsWith("]") ? rest.slice(1) : rest;
      text = `${draft.text.slice(0, open)}[${s.insert}]${after}`;
      if ((parsed.kind === "aggregate" || parsed.kind === "time") && !text.includes(")")) text += parsed.calc === "rolling" ? ", 3)" : ")";
      at = open + s.insert.length + 2;
    }
    onDraft({ ...draft, text });
    requestAnimationFrame(() => {
      input.current?.setSelectionRange(at, at);
      setCaret(at);
    });
  }

  function onKey(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (showList && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      setActive((a) => (a + (e.key === "ArrowDown" ? 1 : suggestions.length - 1)) % suggestions.length);
    } else if (showList && (e.key === "Enter" || e.key === "Tab")) {
      e.preventDefault();
      pick(suggestions[Math.min(active, suggestions.length - 1)]);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (check?.ok) props.onSave();
    } else if (e.key === "Escape") {
      if (showList) setDismissed(true);
      else props.onCancel();
    }
  }

  // Place the list under the bracket being typed (the bar uses a monospace font).
  const left = useMemo(() => {
    const canvas = (measureCanvas.current ??= document.createElement("canvas"));
    const ctx = canvas.getContext("2d");
    if (!ctx || !input.current) return 0;
    ctx.font = getComputedStyle(input.current).font;
    const start = typing !== null ? open : caret - (word?.length ?? 0);
    return Math.max(0, ctx.measureText(draft.text.slice(0, Math.max(0, start))).width - input.current.scrollLeft);
  }, [draft.text, typing, open, caret, word]);

  const format = check?.ok ? formatMeasure(check.value, draft.format) : null;

  return (
    <div className="formula-bar">
      <label className="formula-name">
        <span className="sr-only">Measure name</span>
        <input value={draft.name} onChange={(e) => onDraft({ ...draft, name: e.target.value })} aria-label="Measure name" />
        <span aria-hidden="true">=</span>
      </label>
      <div className="formula-code">
        <input
          ref={input}
          value={draft.text}
          spellCheck={false}
          aria-label="Formula"
          aria-autocomplete="list"
          aria-expanded={showList}
          aria-controls="formula-suggestions"
          onChange={(e) => {
            onDraft({ ...draft, text: e.target.value });
            setCaret(e.target.selectionStart ?? e.target.value.length);
            setActive(0);
            setDismissed(false);
          }}
          onSelect={(e) => setCaret((e.target as HTMLInputElement).selectionStart ?? 0)}
          onKeyDown={onKey}
          onBlur={() => setTimeout(() => setDismissed(true), 120)}
          onFocus={() => setDismissed(false)}
        />
        {showList && (
          <ul id="formula-suggestions" className="formula-suggestions" role="listbox" style={{ left: left + 8 }}>
            {suggestions.map((s, i) => (
              <li
                key={s.kind + s.label}
                role="option"
                aria-selected={i === active}
                className={i === active ? "on" : ""}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(s);
                }}
                onMouseEnter={() => setActive(i)}
              >
                <span className={`type-tag ${s.kind === "measure" ? "type-tag-measure" : ""}`}>{s.kind === "measure" ? "fx" : s.kind === "function" ? "f" : "col"}</span>
                <span>{s.kind === "function" ? s.label : `[${s.label}]`}</span>
                <small>{s.kind}</small>
              </li>
            ))}
          </ul>
        )}
      </div>
      <span className={`formula-check ${check ? (check.ok ? "ok" : "bad") : ""}`} role="status" title={check && !check.ok ? check.error : undefined}>
        {checking && !check ? (
          "Checking…"
        ) : check?.ok ? (
          <>
            <Check size={14} aria-hidden="true" /> Valid. Total {format}
          </>
        ) : check ? (
          <>
            <X size={14} aria-hidden="true" /> {check.error}
          </>
        ) : null}
      </span>
      <button className="tool" onClick={props.onCancel} title="Cancel (Esc)" aria-label="Cancel">
        <X size={15} />
      </button>
      <button className="tool save" onClick={props.onSave} disabled={!check?.ok} title="Save (Enter)" aria-label="Save measure">
        <Check size={15} />
      </button>
    </div>
  );
}

// Diagram

const CARD_W = 208;
const HEAD_H = 34;
const ROW_H = 22;
const MAX_ROWS = 9;
const GAP_X = 110;
const GAP_Y = 30;
const PAD = 28;

type Box = { x: number; y: number };

function shownColumns(table: ModelTable) {
  const ordered = [...table.columns.filter((c) => c.key), ...table.columns.filter((c) => !c.key)];
  return ordered.length > MAX_ROWS ? { rows: ordered.slice(0, MAX_ROWS - 1), more: ordered.length - MAX_ROWS + 1 } : { rows: ordered, more: 0 };
}

function cardHeight(table: ModelTable) {
  const { rows, more } = shownColumns(table);
  return HEAD_H + (rows.length + (more ? 1 : 0)) * ROW_H + 8;
}

/** Facts in the middle, dimensions either side, the date table on the right; saved positions win. */
function autoLayout(tables: ModelTable[]): Record<string, Box> {
  const facts = tables.filter((t) => t.kind === "fact" || t.kind === "table");
  const dims = tables.filter((t) => t.kind === "dimension");
  const dates = tables.filter((t) => t.kind === "date");
  const columns: ModelTable[][] = [[], facts, []];
  dims.forEach((t, i) => columns[i % 2 === 0 ? 2 : 0].push(t));
  columns[2].push(...dates);
  const used = columns.filter((c) => c.length);
  const out: Record<string, Box> = {};
  used.forEach((column, ci) => {
    let y = PAD;
    column.forEach((t) => {
      out[t.id] = { x: PAD + ci * (CARD_W + GAP_X), y };
      y += cardHeight(t) + GAP_Y;
    });
  });
  return out;
}

type Link = { from: { table: string; column: string }; x: number; y: number; over: { table: string; column: string } | null };

function Diagram(props: {
  projectId: string;
  state: ModelState;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onChange: (action: () => Promise<ModelState>) => Promise<ModelState | null>;
}) {
  const { projectId, state, selection, onSelect, onChange } = props;
  const toast = useToast();
  const canvas = useRef<HTMLDivElement>(null);
  const [positions, setPositions] = useState<Record<string, Box>>(() => ({ ...autoLayout(state.tables), ...state.layout }));
  const [moving, setMoving] = useState<{ id: string; dx: number; dy: number } | null>(null);
  const [link, setLink] = useState<Link | null>(null);
  const [checks, setChecks] = useState<Record<string, RelationshipCheck | "pending">>({});

  useEffect(() => {
    setPositions((current) => {
      const auto = autoLayout(state.tables);
      const next: Record<string, Box> = {};
      for (const t of state.tables) next[t.id] = current[t.id] ?? state.layout[t.id] ?? auto[t.id];
      return next;
    });
  }, [state.tables, state.layout]);

  const point = (e: { clientX: number; clientY: number }) => {
    const box = canvas.current!.getBoundingClientRect();
    return { x: e.clientX - box.left + canvas.current!.scrollLeft, y: e.clientY - box.top + canvas.current!.scrollTop };
  };

  function startMove(e: ReactPointerEvent, id: string) {
    if (e.button !== 0) return;
    const p = point(e);
    setMoving({ id, dx: p.x - positions[id].x, dy: p.y - positions[id].y });
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }

  function startLink(e: ReactPointerEvent, table: string, column: string) {
    if (e.button !== 0) return;
    e.stopPropagation();
    const p = point(e);
    setLink({ from: { table, column }, x: p.x, y: p.y, over: null });
    canvas.current?.setPointerCapture(e.pointerId);
  }

  const key = (a: { table: string; column: string }, b: { table: string; column: string }) => `${a.table}\u0000${a.column}\u0000${b.table}\u0000${b.column}`;

  function onMove(e: ReactPointerEvent) {
    if (moving) {
      const p = point(e);
      setPositions((cur) => ({ ...cur, [moving.id]: { x: Math.max(0, p.x - moving.dx), y: Math.max(0, p.y - moving.dy) } }));
    } else if (link) {
      const p = point(e);
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>("[data-column]");
      const over = el && el.dataset.table !== link.from.table ? { table: el.dataset.table!, column: el.dataset.column! } : null;
      setLink({ ...link, x: p.x, y: p.y, over });
      if (over) {
        const k = key(link.from, over);
        if (!(k in checks)) {
          setChecks((c) => ({ ...c, [k]: "pending" }));
          modelApi
            .checkRelationship(projectId, { from_table: link.from.table, from_column: link.from.column, to_table: over.table, to_column: over.column })
            .then((r) => setChecks((c) => ({ ...c, [k]: r })))
            .catch((err) => setChecks((c) => ({ ...c, [k]: { ok: false, error: (err as Error).message } })));
        }
      }
    }
  }

  async function onUp() {
    if (moving) {
      setMoving(null);
      void modelApi.setLayout(projectId, positions).catch(() => {}); // only the arrangement; the model is unchanged
    } else if (link) {
      const target = link.over;
      setLink(null);
      if (!target) return;
      const result = checks[key(link.from, target)];
      if (result && result !== "pending" && !result.ok) {
        toast(result.error, "error");
        return;
      }
      const next = await onChange(() =>
        modelApi.addRelationship(projectId, { from_table: link.from.table, from_column: link.from.column, to_table: target.table, to_column: target.column }),
      );
      if (next) {
        const added = next.relationships[next.relationships.length - 1];
        if (added) onSelect({ kind: "rel", id: added.id });
        setChecks({});
      }
    }
  }

  const tables = new Map(state.tables.map((t) => [t.id, t]));
  const measuresIn = (id: string) => state.measures.filter((m) => m.table === id).length;

  function anchor(tableId: string, column: string, towardsX: number) {
    const t = tables.get(tableId);
    const p = positions[tableId];
    if (!t || !p) return null;
    const { rows } = shownColumns(t);
    const i = rows.findIndex((c) => c.name === column);
    const y = p.y + (i >= 0 ? HEAD_H + i * ROW_H + ROW_H / 2 + 4 : HEAD_H / 2);
    const right = towardsX > p.x + CARD_W / 2;
    return { x: right ? p.x + CARD_W : p.x, y, right };
  }

  const width = Math.max(...Object.values(positions).map((p) => p.x + CARD_W + PAD), 600);
  const height = Math.max(...state.tables.map((t) => (positions[t.id]?.y ?? 0) + cardHeight(t) + PAD), 400);
  const hint = link?.over ? checks[key(link.from, link.over)] : null;

  return (
    <div className="diagram-canvas" ref={canvas} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={() => (setMoving(null), setLink(null))}>
      <div className="diagram-plane" style={{ width, height }}>
        <svg className="diagram-links" width={width} height={height} aria-hidden="true">
          {state.relationships.map((rel: Relationship) => {
            const to = positions[rel.to_table];
            const from = positions[rel.from_table];
            if (!to || !from) return null;
            const a = anchor(rel.from_table, rel.from_column, to.x + CARD_W / 2);
            const b = anchor(rel.to_table, rel.to_column, from.x + CARD_W / 2);
            if (!a || !b) return null;
            const bend = Math.max(40, Math.abs(b.x - a.x) / 2);
            const d = `M ${a.x} ${a.y} C ${a.x + (a.right ? bend : -bend)} ${a.y}, ${b.x + (b.right ? bend : -bend)} ${b.y}, ${b.x} ${b.y}`;
            const on = selection?.kind === "rel" && selection.id === rel.id;
            return (
              <g key={rel.id} className={`rel ${rel.active ? "" : "inactive"} ${rel.error ? "broken" : ""} ${rel.status === "AI" ? "suggested" : ""} ${on ? "on" : ""}`}>
                <path d={d} className="rel-line" />
                <path d={d} className="rel-hit" onClick={() => onSelect({ kind: "rel", id: rel.id })}>
                  <title>
                    {rel.from_name}[{rel.from_column}] → {rel.to_name}[{rel.to_column}]
                  </title>
                </path>
                <text x={a.x + (a.right ? 6 : -14)} y={a.y - 5}>
                  *
                </text>
                <text x={b.x + (b.right ? 6 : -12)} y={b.y - 5}>
                  1
                </text>
              </g>
            );
          })}
          {link && (
            <line
              className="rel-drag"
              x1={(positions[link.from.table]?.x ?? 0) + CARD_W}
              y1={anchor(link.from.table, link.from.column, 1e9)?.y ?? 0}
              x2={link.x}
              y2={link.y}
            />
          )}
        </svg>

        {state.tables.map((t) => {
          const p = positions[t.id];
          if (!p) return null;
          const { rows, more } = shownColumns(t);
          const on = selection?.kind === "table" && selection.id === t.id;
          const n = measuresIn(t.id);
          return (
            <div key={t.id} className={`table-card kind-${t.kind} ${on ? "on" : ""} ${moving?.id === t.id ? "moving" : ""}`} style={{ left: p.x, top: p.y, width: CARD_W }}>
              <header onPointerDown={(e) => startMove(e, t.id)} onClick={() => onSelect({ kind: "table", id: t.id })} title="Drag to move">
                {t.kind === "date" ? <Calendar size={14} aria-hidden="true" /> : <Table2 size={14} aria-hidden="true" />}
                <span className="card-name">{t.name}</span>
                <span className="card-meta">
                  {t.kind === "table" ? "" : t.kind === "date" ? "date table, " : `${t.kind}, `}
                  {t.rows.toLocaleString()}
                </span>
              </header>
              <ul>
                {rows.map((c) => {
                  const isSource = link?.from.table === t.id && link.from.column === c.name;
                  const isOver = link?.over?.table === t.id && link.over.column === c.name;
                  const verdict = isOver && hint && hint !== "pending" ? (hint.ok ? "drop-ok" : "drop-bad") : isOver ? "drop-wait" : "";
                  return (
                    <li
                      key={c.name}
                      data-table={t.id}
                      data-column={c.name}
                      className={`${c.key ? "is-key" : ""} ${c.hidden ? "is-hidden" : ""} ${isSource ? "dragging" : ""} ${verdict}`}
                      onPointerDown={(e) => startLink(e, t.id, c.name)}
                      title="Drag onto a column of another table to relate them"
                    >
                      <TypeTag type={c.type} />
                      <span>{c.name}</span>
                    </li>
                  );
                })}
                {more > 0 && <li className="more">+ {more} more columns</li>}
              </ul>
              {n > 0 && <footer className="card-measures">fx {n} {n === 1 ? "measure" : "measures"}</footer>}
            </div>
          );
        })}

        {link?.over && hint && (
          <div className="link-hint" style={{ left: link.x + 16, top: link.y + 12 }} role="status">
            {hint === "pending" ? (
              "Checking…"
            ) : hint.ok ? (
              <>
                <strong>
                  {tables.get(hint.from_table)?.name}[{hint.from_column}] → {tables.get(hint.to_table)?.name}[{hint.to_column}]
                </strong>
                <span className="ok-line">✓ Each {hint.to_column} appears once in {tables.get(hint.to_table)?.name}</span>
                <span className="ok-line">✓ {Math.round(hint.checks.match_rate * 1000) / 10}% of rows match</span>
                {!hint.active && <span>Kept inactive: another path already joins these tables.</span>}
                <span>Release to create the relationship.</span>
              </>
            ) : (
              <span className="bad-line">✗ {hint.error}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// Measure filters: only rows where a column, on this table or a related one, meets a condition.

const OPERATORS: { value: string; label: string; needs: "value" | "values" | null }[] = [
  { value: "equals", label: "is", needs: "value" },
  { value: "not_equals", label: "is not", needs: "value" },
  { value: "in", label: "is one of", needs: "values" },
  { value: "not_in", label: "is none of", needs: "values" },
  { value: "greater_than", label: "is more than", needs: "value" },
  { value: "less_than", label: "is less than", needs: "value" },
  { value: "at_least", label: "is at least", needs: "value" },
  { value: "at_most", label: "is at most", needs: "value" },
  { value: "is_blank", label: "is blank", needs: null },
  { value: "is_not_blank", label: "is not blank", needs: null },
];

function FilterEditor(props: { tables: ModelTable[]; home: string | null; filters: MeasureFilter[]; onChange: (f: MeasureFilter[]) => void }) {
  const { tables, home, filters, onChange } = props;
  const [column, setColumn] = useState("");
  const [operator, setOperator] = useState("equals");
  const [value, setValue] = useState("");
  const name = (id?: string) => tables.find((t) => t.id === (id ?? home))?.name ?? id ?? "";
  const options = tables.flatMap((t) => t.columns.map((c) => ({ key: `${t.id}\u0000${c.name}`, label: `${t.name}[${c.name}]`, table: t.id, column: c.name, type: c.type })));
  const picked = options.find((o) => o.key === column);
  const needs = OPERATORS.find((o) => o.value === operator)?.needs ?? null;
  const typed = (v: string) => (picked && (picked.type === "integer" || picked.type === "decimal") && v.trim() !== "" && !Number.isNaN(Number(v)) ? Number(v) : v.trim());

  function add() {
    if (!picked) return;
    const filter: MeasureFilter = { column: picked.column, operator, ...(picked.table !== home ? { table: picked.table } : {}) };
    if (needs === "value") filter.value = typed(value);
    if (needs === "values") filter.values = value.split(",").map(typed).filter((v) => v !== "");
    onChange([...filters, filter]);
    setValue("");
  }

  return (
    <div className="prop filter-editor">
      <span>Only rows where</span>
      {filters.length === 0 && <p className="muted small">Every row counts.</p>}
      <ul>
        {filters.map((f, i) => (
          <li key={i} className="pill">
            {name(f.table)}[{f.column}] {OPERATORS.find((o) => o.value === f.operator)?.label ?? f.operator}{" "}
            {f.values ? f.values.join(", ") : f.value === undefined ? "" : String(f.value)}
            <button className="pill-x" aria-label="Remove filter" onClick={() => onChange(filters.filter((_, j) => j !== i))}>
              <X size={11} />
            </button>
          </li>
        ))}
      </ul>
      <div className="filter-add">
        <select value={column} onChange={(e) => setColumn(e.target.value)} aria-label="Filter column">
          <option value="">Add a condition…</option>
          {options.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
        {picked && (
          <>
            <select value={operator} onChange={(e) => setOperator(e.target.value)} aria-label="Condition">
              {OPERATORS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            {needs && (
              <input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && add()}
                placeholder={needs === "values" ? "Values, comma separated" : "Value"}
                aria-label="Value"
              />
            )}
            <button className="secondary small-button" onClick={add} disabled={!!needs && !value.trim()}>
              Add
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// Properties

const FORMAT_LABEL: Record<MeasureFormat["type"], string> = { number: "Number", integer: "Whole number", currency: "Currency", percent: "Percent" };

function Properties(props: {
  projectId: string;
  state: ModelState;
  selection: Selection;
  draft: Draft | null;
  measure: Measure | null;
  hasKey: boolean;
  building: boolean;
  run: ModelRun | { error: string } | null;
  onDraft: (d: Draft) => void;
  onSave: () => void;
  onBuild: () => void;
  onNewMeasure: (tableId?: string) => void;
  onSelect: (s: Selection) => void;
  onChange: (action: () => Promise<ModelState>) => Promise<void>;
}) {
  const { projectId, state, selection, draft, measure, onDraft, onChange } = props;
  const names = new Map(state.tables.map((t) => [t.id, t.name]));

  if ((selection?.kind === "measure" || selection?.kind === "new") && draft) {
    const parsed = parseText(draft.text);
    const dirty = !measure || JSON.stringify(bodyOf(draft)) !== JSON.stringify(bodyOf(draftOf(measure)));
    return (
      <>
        <div className="pane-head">
          <span>{measure ? "Measure" : "New measure"}</span>
          <span className="grow" />
          <span className="pane-sub">{draft.name}</span>
        </div>
        <div className="props">
          {measure?.status === "AI" && (
            <div className="suggest-card">
              <span className="ai-tag">Suggested by AI</span>
              {measure.reason && <p>{measure.reason}</p>}
              <div className="actions">
                <button className="small-button" onClick={() => onChange(() => modelApi.approve(projectId, measure.id))}>
                  Accept
                </button>
                <button className="secondary small-button" onClick={() => onChange(() => modelApi.removeMeasure(projectId, measure.id))}>
                  Reject
                </button>
              </div>
            </div>
          )}
          {measure?.error && <p className="bad small">{measure.error}</p>}
          <label className="prop">
            <span>Name</span>
            <input value={draft.name} onChange={(e) => onDraft({ ...draft, name: e.target.value })} />
          </label>
          <div className="prop-row">
            <label className="prop">
              <span>Home table</span>
              <select value={draft.table ?? ""} onChange={(e) => onDraft({ ...draft, table: e.target.value || null })} disabled={parsed.kind !== "aggregate"}>
                {parsed.kind !== "aggregate" && <option value="">{parsed.kind === "time" ? "(follows its base measure)" : "(a formula)"}</option>}
                {state.tables.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="prop">
              <span>Folder</span>
              <input value={draft.folder} onChange={(e) => onDraft({ ...draft, folder: e.target.value })} placeholder="None" />
            </label>
          </div>
          <div className="prop-row">
            <label className="prop">
              <span>Format</span>
              <select value={draft.format.type} onChange={(e) => onDraft({ ...draft, format: { ...draft.format, type: e.target.value as MeasureFormat["type"] } })}>
                {Object.entries(FORMAT_LABEL).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label className="prop">
              <span>Decimals</span>
              <select
                value={draft.format.decimals ?? 0}
                onChange={(e) => onDraft({ ...draft, format: { ...draft.format, decimals: Number(e.target.value) } })}
                disabled={draft.format.type === "integer"}
              >
                {[0, 1, 2, 3, 4].map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </label>
          </div>
          {draft.format.type === "currency" && (
            <label className="prop">
              <span>Currency symbol</span>
              <input value={draft.format.currency ?? "RM"} onChange={(e) => onDraft({ ...draft, format: { ...draft.format, currency: e.target.value } })} />
            </label>
          )}
          <label className="prop">
            <span>Description</span>
            <textarea rows={3} value={draft.description} onChange={(e) => onDraft({ ...draft, description: e.target.value })} placeholder="What it means, in plain words" />
          </label>
          {parsed.kind === "aggregate" && (
            <FilterEditor tables={state.tables} home={draft.table} filters={draft.filters} onChange={(filters) => onDraft({ ...draft, filters })} />
          )}
          {parsed.kind === "aggregate" && state.relationships.some((r) => !r.active || r.id === draft.useRelationship) && (
            <label className="prop">
              <span>Relationship to use</span>
              <select value={draft.useRelationship ?? ""} onChange={(e) => onDraft({ ...draft, useRelationship: e.target.value || null })}>
                <option value="">The active one (usual)</option>
                {state.relationships
                  .filter((r) => !r.active || r.id === draft.useRelationship)
                  .map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.from_name}[{r.from_column}] → {r.to_name}[{r.to_column}]
                    </option>
                  ))}
              </select>
            </label>
          )}
          <p className="muted small">
            Write <code>SUM([Column])</code>, <code>AVERAGE( )</code>, <code>COUNTROWS()</code>, <code>DISTINCTCOUNT( )</code>, <code>MIN( )</code> or <code>MAX( )</code> over the home table's columns, or a formula over other measures such as <code>[Revenue] / [Orders]</code>. Over time: <code>YTD([Revenue])</code>, <code>QTD( )</code>, <code>MTD( )</code>, <code>LASTYEAR([Revenue])</code>, <code>LASTYEARYTD( )</code> or <code>ROLLING([Revenue], 3)</code>; these need the date table. Conditions: <code>IF([Qty] &gt;= 10, [Qty] * [Price], 0)</code>, <code>SWITCH([Region], "North", 1, 0)</code>, with <code>AND</code>, <code>OR</code> and <code>NOT</code>.
          </p>
          <div className="actions">
            <button onClick={props.onSave} disabled={!dirty}>
              {measure ? "Save changes" : "Add measure"}
            </button>
            {measure && (
              <button className="secondary danger" onClick={() => onChange(() => modelApi.removeMeasure(projectId, measure.id))}>
                <Trash2 size={14} aria-hidden="true" /> Remove
              </button>
            )}
          </div>
        </div>
      </>
    );
  }

  if (selection?.kind === "rel") {
    const rel = state.relationships.find((r) => r.id === selection.id);
    if (rel) {
      const c = rel.checks;
      return (
        <>
          <div className="pane-head">
            <span>Relationship</span>
          </div>
          <div className="props">
            {rel.status === "AI" && (
              <div className="suggest-card">
                <span className="ai-tag">Suggested by AI</span>
                {rel.reason && <p>{rel.reason}</p>}
                <div className="actions">
                  <button className="small-button" onClick={() => onChange(() => modelApi.approve(projectId, rel.id))}>
                    Accept
                  </button>
                  <button className="secondary small-button" onClick={() => onChange(() => modelApi.removeRelationship(projectId, rel.id))}>
                    Reject
                  </button>
                </div>
              </div>
            )}
            {rel.error && <p className="bad small">{rel.error}</p>}
            <dl className="facts">
              <dt>Many side</dt>
              <dd>
                {rel.from_name}[{rel.from_column}]
              </dd>
              <dt>One side</dt>
              <dd>
                {rel.to_name}[{rel.to_column}]
              </dd>
              {c && (
                <>
                  <dt>Rows that match</dt>
                  <dd>
                    {Math.round(c.match_rate * 1000) / 10}% ({c.matched_rows.toLocaleString()} of {c.keyed_rows.toLocaleString()})
                  </dd>
                  <dt>Kind</dt>
                  <dd>{c.cardinality === "one_to_one" ? "One to one" : "Many to one"}</dd>
                  {c.blank_keys > 0 && (
                    <>
                      <dt>Blank keys</dt>
                      <dd>{c.blank_keys.toLocaleString()}</dd>
                    </>
                  )}
                </>
              )}
              <dt>Active</dt>
              <dd>{rel.active ? "Yes" : "No, another path already joins these tables"}</dd>
            </dl>
            {c?.unmatched_examples?.length > 0 && <p className="small issue">No match for {c.unmatched_examples.join(", ")}</p>}
            <div className="actions">
              <button className="secondary danger" onClick={() => onChange(() => modelApi.removeRelationship(projectId, rel.id))}>
                <Trash2 size={14} aria-hidden="true" /> Remove relationship
              </button>
            </div>
          </div>
        </>
      );
    }
  }

  if (selection?.kind === "table") {
    const t = state.tables.find((x) => x.id === selection.id);
    if (t) {
      const isDate = t.kind === "date";
      return (
        <>
          <div className="pane-head">
            <span>Table</span>
            <span className="grow" />
            <span className="pane-sub">{t.name}</span>
          </div>
          <div className="props">
            <dl className="facts">
              <dt>Role</dt>
              <dd>{isDate ? "Date table" : t.kind === "fact" ? "Fact (the many side)" : t.kind === "dimension" ? "Dimension (the one side)" : "Not related yet"}</dd>
              <dt>Rows</dt>
              <dd>{t.rows.toLocaleString()}</dd>
              <dt>Columns</dt>
              <dd>
                {t.columns.length}
                {t.columns.some((c) => c.hidden) && `, ${t.columns.filter((c) => c.hidden).length} hidden`}
              </dd>
              {isDate && state.date_table && (
                <>
                  <dt>Covers</dt>
                  <dd>
                    {state.date_table.first} to {state.date_table.last}
                  </dd>
                </>
              )}
            </dl>
            <div className="actions">
              {!isDate && (
                <button className="secondary" onClick={() => props.onNewMeasure(t.id)}>
                  <Plus size={14} aria-hidden="true" /> New measure here
                </button>
              )}
              {isDate && (
                <button className="secondary danger" onClick={() => onChange(() => modelApi.removeDateTable(projectId))}>
                  <Trash2 size={14} aria-hidden="true" /> Remove date table
                </button>
              )}
            </div>
          </div>
        </>
      );
    }
  }

  // Nothing selected: the model at a glance, and what the AI suggests.
  const pending = [
    ...state.relationships.filter((r) => r.status === "AI").map((r) => ({ id: r.id, kind: "rel" as const, label: `${r.from_name}[${r.from_column}] → ${r.to_name}[${r.to_column}]`, reason: r.reason })),
    ...state.measures.filter((m) => m.status === "AI").map((m) => ({ id: m.id, kind: "measure" as const, label: m.name, reason: m.reason || m.label })),
    ...(state.date_table?.status === "AI" ? [{ id: "date", kind: "date" as const, label: "Date table", reason: state.date_table.reason }] : []),
  ];
  const run = props.run;

  return (
    <>
      <div className="pane-head">
        <span>Model</span>
      </div>
      <div className="props">
        <p className="muted small">Select a table, relationship or measure to see its properties.</p>
        <div className="actions">
          <button className="secondary" onClick={() => onChange(() => modelApi.undo(projectId))} disabled={!state.can_undo}>
            <Undo2 size={14} aria-hidden="true" /> Undo
          </button>
          {!state.date_table && (
            <button className="secondary" onClick={() => onChange(() => modelApi.setDateTable(projectId))}>
              <Calendar size={14} aria-hidden="true" /> Add a date table
            </button>
          )}
        </div>
        <div className="suggest-card">
          <span className="ai-tag">AI</span>
          <p>Let the AI propose relationships, a date table and measures. You accept or reject each one.</p>
          <button className="ai-button" onClick={props.onBuild} disabled={!props.hasKey || props.building} title={props.hasKey ? undefined : "Add an API key in Settings first"}>
            <Sparkles size={14} aria-hidden="true" /> {props.building ? "AI is working…" : "Suggest with AI"}
          </button>
          {run && "error" in run && <p className="bad small">{run.error}</p>}
          {run && !("error" in run) && (
            <p className="small">
              {run.summary} {run.refused.length > 0 && <span className="issue">{run.refused.length} refused by the checks: {run.refused.map((r) => `${r.label}: ${r.error}`).join("; ")}</span>}
            </p>
          )}
        </div>
        {pending.length > 0 && (
          <div className="pending-list">
            <div className="pending-head">
              <strong>Waiting for you ({pending.length})</strong>
              <button className="link small" onClick={() => onChange(() => modelApi.approve(projectId))}>
                Accept all
              </button>
            </div>
            {pending.map((p) => (
              <div key={p.kind + p.id} className="pending-item">
                <button className="link" onClick={() => p.kind !== "date" && props.onSelect(p.kind === "rel" ? { kind: "rel", id: p.id } : { kind: "measure", id: p.id })}>
                  {p.label}
                </button>
                {p.reason && <span className="muted small">{p.reason}</span>}
                <span className="actions">
                  <button className="small-button" onClick={() => onChange(() => modelApi.approve(projectId, p.id))}>
                    Accept
                  </button>
                  <button
                    className="secondary small-button"
                    onClick={() =>
                      onChange(() =>
                        p.kind === "rel" ? modelApi.removeRelationship(projectId, p.id) : p.kind === "measure" ? modelApi.removeMeasure(projectId, p.id) : modelApi.removeDateTable(projectId),
                      )
                    }
                  >
                    Reject
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
        <dl className="facts">
          <dt>Tables</dt>
          <dd>{state.tables.length}</dd>
          <dt>Relationships</dt>
          <dd>{state.relationships.length}</dd>
          <dt>Measures</dt>
          <dd>{state.measures.length}</dd>
          {names.size > 0 && state.date_table && (
            <>
              <dt>Dates</dt>
              <dd>
                {state.date_table.first} to {state.date_table.last}
              </dd>
            </>
          )}
        </dl>
      </div>
    </>
  );
}
