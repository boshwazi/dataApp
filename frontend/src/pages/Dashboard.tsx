import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { EChartsCoreOption } from "echarts/core";
import { ArrowUp, Calendar, ChevronDown, FileDown, Filter, GripVertical, History, LayoutDashboard, Maximize2, Minimize2, MoreHorizontal, Pencil, Plus, Sparkles, Table2, Trash2, Undo2, X } from "lucide-react";
import {
  activityApi,
  api,
  dashboardChat,
  dashboardsApi,
  formatCompact,
  formatMeasure,
  reportApi,
  type ChartData,
  type GridBox,
  type KpiData,
  type MeasureFormat,
  type Drill,
  type PageFilter,
  type PageScope,
  type ReportField,
  type ReportPage,
  type ReportRun,
  type ReportState,
  type Visual,
  type VisualType,
} from "../api";
import { ActivityDrawer } from "../components/ActivityDrawer";
import { AskPanel } from "../components/AskPanel";
import { Chart, SERIES, TOKENS } from "../components/Chart";
import { ChatPanel, type ChatHandle } from "../components/ChatPanel";
import { EmptyState, Menu, PopMenu, TopbarActions, type MenuItem, useCrumbs, useDialogs, useShortcut, useToast } from "../components/ui";
import { useAccount } from "../components/account";
import { useTheme } from "../components/theme";
import { TypeTag } from "./Transform";

const SUGGESTIONS = ["Add a page about customers", "Swap the donut for a bar chart", "Which visual shows the biggest change?"];

type Change = (action: () => Promise<ReportState>) => Promise<ReportState | null>;

/** A dashboard: report pages on its dataset's published version. Creators edit the draft on a canvas; viewers see what is published. */
export function Dashboard() {
  const { dashboardId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  // Viewers only ever see the published version.
  const creator = useAccount().can("creator");
  const side = !creator || params.get("view") === "published" ? "published" : "draft";
  const [state, setState] = useState<ReportState | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [run, setRun] = useState<ReportRun | { error: string } | null>(null);
  const [aiOpen, setAiOpen] = useState(false);
  const [showActivity, setShowActivity] = useState(false);
  const [presenting, setPresenting] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  // Slicer choices per page. They are this viewer's own: never saved, sent with each look, Ask and chat.
  const [chosen, setChosen] = useState<Record<string, PageFilter[]>>({});
  const [drills, setDrills] = useState<Record<string, Drill[]>>({});
  const pageParam = params.get("page");
  const scope = useRef<PageScope>({ page_id: null, filters: [] });
  scope.current = { page_id: pageParam, filters: (pageParam && chosen[pageParam]) || [] };
  const drillNow = useRef<Record<string, Drill>>({});
  drillNow.current = Object.fromEntries(Object.entries(drills).filter(([, s]) => s.length).map(([id, s]) => [id, s[s.length - 1]]));
  const chosenKey = JSON.stringify([scope.current, drillNow.current]);
  const stageRef = useRef<HTMLDivElement>(null);
  const chat = useRef<ChatHandle | null>(null);
  const thread = useMemo(() => dashboardChat(dashboardId, () => scope.current), [dashboardId]);
  const dialogs = useDialogs();
  const toast = useToast();
  const navigate = useNavigate();

  useCrumbs(state && [{ label: "Dashboards", to: "/dashboards" }, { label: state.name }]);
  useShortcut("i", () => setAiOpen((o) => !o));

  const fetchState = useCallback(() => {
    const { page_id, filters } = scope.current;
    return reportApi.state(dashboardId, side, page_id ?? undefined, filters, drillNow.current);
  }, [dashboardId, side]);

  const load = useCallback(() => {
    fetchState().then(setState).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchState, chosenKey]);

  useEffect(() => {
    load();
    api.getSettings().then((s) => setHasKey(s.has_api_key)).catch(() => setHasKey(false));
  }, [load]);

  // Present: the pages fill the screen (browser full screen where allowed); Esc leaves.
  useEffect(() => {
    if (!presenting) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.fullscreenElement) setPresenting(false);
    };
    const onFull = () => {
      if (!document.fullscreenElement) setPresenting(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onFull);
    stageRef.current?.requestFullscreen?.().catch(() => {});
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onFull);
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    };
  }, [presenting]);

  const go = (next: Record<string, string>) => {
    setParams({ ...Object.fromEntries(params), ...next }, { replace: true });
  };

  const change: Change = useCallback(async (action) => {
    setError(null);
    try {
      let next = await action();
      if (scope.current.filters.length || Object.keys(drillNow.current).length) next = await fetchState(); // keep the viewer's choices
      setState(next);
      return next;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, [fetchState]);

  function choose(pageId: string, filters: PageFilter[]) {
    setChosen((current) => ({ ...current, [pageId]: filters }));
    if (pageParam !== pageId) go({ page: pageId });
  }

  /** A click on a chart: a date chart drills down; any other picks that category for the rest of the page. */
  function pick(pageId: string, visual: Visual, value: Value) {
    const group = visual.group_by[0];
    if (!group) return;
    if (visual.drill_next) {
      const stack = drills[visual.id] ?? [];
      const filters = [...(stack[stack.length - 1]?.filters ?? []), { table: group.table, column: group.column, operator: "in", values: [value] }];
      setDrills((d) => ({ ...d, [visual.id]: [...stack, { column: visual.drill_next!, filters }] }));
      return;
    }
    const current = chosen[pageId] ?? [];
    const mine = current.find((f) => f.source === visual.id);
    const rest = current.filter((f) => f.source !== visual.id);
    const same = mine && mine.values?.length === 1 && mine.values[0] === value;
    choose(pageId, same ? rest : [...rest, { table: group.table, column: group.column, operator: "in", values: [value], source: visual.id }]);
  }

  function drillUp(visualId: string) {
    setDrills((d) => ({ ...d, [visualId]: (d[visualId] ?? []).slice(0, -1) }));
  }

  async function build() {
    setBuilding(true);
    setRun(null);
    setError(null);
    try {
      const result = await reportApi.ai(dashboardId);
      setRun(result);
      setState(result);
      if (result.new_pages[0]) go({ page: result.new_pages[0] });
    } catch (e) {
      setRun({ error: (e as Error).message });
    } finally {
      setBuilding(false);
    }
  }

  async function publish() {
    setPublishing(true);
    const next = await change(() => dashboardsApi.publish(dashboardId));
    if (next) toast("Published: viewers now see this version");
    setPublishing(false);
  }

  async function rename() {
    if (!state) return;
    const name = await dialogs.prompt({ title: "Rename dashboard", label: "Name", initial: state.name, confirmLabel: "Rename" });
    if (name && name !== state.name) await change(() => dashboardsApi.rename(dashboardId, name));
  }

  async function addPage() {
    const title = await dialogs.prompt({ title: "New page", label: "Page title", confirmLabel: "Add page" });
    if (!title) return;
    const next = await change(() => reportApi.addPage(dashboardId, title));
    const added = next?.pages[next.pages.length - 1];
    if (added) go({ page: added.id });
  }

  if (!state) return <p className={error ? "bad pad" : "muted pad"}>{error ?? "Loading…"}</p>;

  const editing = side === "draft";
  const page = state.pages.find((p) => p.id === params.get("page")) ?? state.pages[0];
  const pending = state.pages.reduce((n, p) => n + (p.status === "AI" ? 1 : 0) + p.visuals.filter((v) => v.status === "AI").length, 0);
  const dataset = state.dataset;
  const visual = editing ? page?.visuals.find((v) => v.id === selected) ?? null : null;

  const moreItems: MenuItem[] = [
    ...(editing ? [{ label: "Rename", icon: <Pencil size={16} aria-hidden="true" />, onSelect: rename }] : []),
    ...(editing && dataset?.published
      ? [{ label: building ? "AI is designing…" : state.pages.length ? "Add pages with AI" : "Build report with AI", icon: <Sparkles size={16} aria-hidden="true" />, onSelect: () => hasKey && !building && void build() }]
      : []),
    ...(creator ? [{ label: "Activity", icon: <History size={16} aria-hidden="true" />, onSelect: () => setShowActivity(true) }] : []),
    ...(state.published ? [{ label: "Export PDF", icon: <FileDown size={16} aria-hidden="true" />, onSelect: () => navigate(`/dashboards/${dashboardId}/export`) }] : []),
  ];

  const pageTabs = page && (
    <nav className="page-tabs" aria-label="Report pages">
      {state.pages.map((p, i) => (
        <button key={p.id} className={p.id === page.id ? "on" : ""} onClick={() => go({ page: p.id })} aria-current={p.id === page.id ? "page" : undefined}>
          <span className="tab-number" aria-hidden="true">
            {i + 1}
          </span>
          <span className="tab-title">{p.title}</span>
        </button>
      ))}
    </nav>
  );

  return (
    <div className="workbench">
      <TopbarActions>
        {creator && (
          <nav className="stage-tabs" aria-label="Mode">
            <button className={editing ? "active" : ""} aria-pressed={editing} onClick={() => go({ view: "draft" })}>
              Edit
            </button>
            <button className={editing ? "" : "active"} aria-pressed={!editing} onClick={() => go({ view: "published" })} disabled={!state.published} title={state.published ? undefined : "Publish first"}>
              View as viewer
            </button>
          </nav>
        )}
        <span className="topbar-grow" />
        <span className={`topbar-status ${state.published ? (state.unpublished_changes ? "warn" : "ok") : ""}`}>
          {state.published ? (state.unpublished_changes ? "Draft changes" : "Published") : "Draft, not published"}
          {state.as_of ? `, data as of ${state.as_of}` : ""}
        </span>
        {editing && (
          <button className="tool" onClick={() => change(() => reportApi.undo(dashboardId))} disabled={!state.can_undo} title="Undo" aria-label="Undo">
            <Undo2 size={16} />
          </button>
        )}
        {(editing || state.published) && (
          <button className={`ai-button ${aiOpen ? "on" : ""}`} onClick={() => setAiOpen(!aiOpen)} aria-pressed={aiOpen} title="Ask AI (Ctrl I)">
            <Sparkles size={15} aria-hidden="true" /> {editing ? "Ask AI" : "Ask"} <kbd>Ctrl I</kbd>
          </button>
        )}
        {state.pages.length > 0 && (
          <button className="secondary compact" onClick={() => setPresenting(true)}>
            <Maximize2 size={15} aria-hidden="true" /> Present
          </button>
        )}
        {moreItems.length > 0 && <Menu label="More actions" icon={<MoreHorizontal size={18} />} items={moreItems} />}
        {editing && (
          <button className="primary-compact" onClick={publish} disabled={publishing || (state.published && !state.unpublished_changes) || !state.pages.length}>
            {publishing ? "Publishing…" : "Publish"}
          </button>
        )}
      </TopbarActions>

      {((dataset && !dataset.published) || !dataset || (editing && state.dataset_updated_to) || error || run) && (
        <div className="workbench-notices">
          {!dataset && <p className="bad">The dataset of this dashboard was removed.</p>}
          {dataset && !dataset.published && (
            <div className="notice small">
              This dashboard is waiting for its data. <Link to={`/datasets/${dataset.id}/get-data?for=${dashboardId}`}>Open {dataset.name}</Link>, load and model the data, then publish it.
            </div>
          )}
          {editing && state.dataset_updated_to && (
            <div className="notice stale-banner small">
              <span>
                <strong>{dataset?.name}</strong> was published again (v{state.dataset_updated_to}) since this report was last reviewed.
              </span>
              <span className="actions">
                <button
                  className="secondary"
                  onClick={() => {
                    setAiOpen(true);
                    setTimeout(() => chat.current?.send("The dataset was published again. Check every page against it and fix visuals that broke or no longer make sense."), 0);
                  }}
                >
                  Ask AI to check
                </button>
                <button className="link small" onClick={() => change(() => activityApi.dashboardReviewed(dashboardId))}>
                  Mark as reviewed
                </button>
              </span>
            </div>
          )}
          {error && (
            <p className="bad small">
              {error}{" "}
              <button className="link small" onClick={() => setError(null)}>
                Dismiss
              </button>
            </p>
          )}
          {run && <RunResult run={run} onClose={() => setRun(null)} />}
        </div>
      )}

      <div className="workbench-body">
        <div className={`editor report-editor ${editing ? "" : "viewing"}`}>
          <aside className="pane pane-left" aria-label="Pages">
            <div className="pane-head">
              <span>Pages</span>
              <span className="grow" />
              {editing && dataset?.published && (
                <button className="tool" onClick={addPage} title="Add page" aria-label="Add page">
                  <Plus size={15} />
                </button>
              )}
            </div>
            <div className="page-thumbs">
              {state.pages.map((p) => (
                <button key={p.id} className={`page-thumb ${p.id === page?.id ? "on" : ""}`} onClick={() => go({ page: p.id })} aria-current={p.id === page?.id ? "page" : undefined}>
                  <MiniPage page={p} />
                  <span className="thumb-title">
                    {p.title}
                    {editing && p.status === "AI" && <span className="badge ai">AI</span>}
                  </span>
                </button>
              ))}
            </div>
          </aside>

          <section className="pane-center report-stage" aria-label="Canvas">
            {!page ? (
              dataset?.published && (
                <EmptyState icon={<LayoutDashboard size={28} />} title={editing ? "No pages yet" : "Nothing published yet"} action={editing ? <button onClick={addPage}>Add a page</button> : undefined}>
                  {editing
                    ? "Add a page and drag fields onto it, or let the AI design a first draft from the More menu. Every visual it makes is an ordinary one you can edit."
                    : "Publish the draft to show it here."}
                </EmptyState>
              )
            ) : (
              <div ref={stageRef} className={presenting ? "presenting" : "canvas-scroll"}>
                {presenting && (
                  <div className="present-bar">
                    <strong>{state.name}</strong>
                    <span className="muted small">
                      {editing ? "Draft" : "Published"}
                      {state.as_of ? `, data as of ${state.as_of}` : ""}
                    </span>
                    <span className="grow" />
                    {pageTabs}
                    <button className="secondary" onClick={() => setPresenting(false)}>
                      <Minimize2 size={16} aria-hidden="true" /> Exit
                    </button>
                  </div>
                )}
                <ReportCanvas
                  key={page.id}
                  dashboardId={dashboardId}
                  page={page}
                  formats={state.formats}
                  fields={state.fields}
                  editing={editing && !presenting}
                  selected={selected}
                  onSelect={setSelected}
                  onChange={change}
                  chosen={chosen[page.id] ?? []}
                  onChoose={(filters) => choose(page.id, filters)}
                  onPick={(visual, value) => pick(page.id, visual, value)}
                  onDrillUp={drillUp}
                />
              </div>
            )}
          </section>

          {editing && (
            <aside className="pane pane-right" aria-label={visual ? "Visual" : "Page"}>
              {visual ? (
                <VisualPane key={visual.id} dashboardId={dashboardId} visual={visual} fields={state.fields} onChange={change} onClose={() => setSelected(null)} />
              ) : (
                <PagePane
                  dashboardId={dashboardId}
                  page={page ?? null}
                  fields={state.fields}
                  pending={pending}
                  hasKey={hasKey}
                  building={building}
                  canBuild={!!dataset?.published}
                  onBuild={build}
                  onChange={change}
                  onSelect={setSelected}
                />
              )}
            </aside>
          )}
        </div>
      </div>

      {aiOpen && editing && (
        <div className="ai-dock" role="dialog" aria-label="Report AI">
          <ChatPanel
            title="Report AI"
            thread={thread}
            suggestions={SUGGESTIONS}
            disabled={dataset?.published ? null : "Publish the dataset first; then the AI can design pages."}
            onApplied={load}
            onClose={() => setAiOpen(false)}
            handle={(h) => (chat.current = h)}
          />
        </div>
      )}
      {aiOpen && !editing && state.published && (
        <div className="ai-dock" role="dialog" aria-label="Ask">
          <AskPanel dashboardId={dashboardId} side="published" onClose={() => setAiOpen(false)} scope={() => scope.current} />
        </div>
      )}
      {showActivity && <ActivityDrawer load={() => activityApi.dashboard(dashboardId)} onClose={() => setShowActivity(false)} />}
    </div>
  );
}

function RunResult({ run, onClose }: { run: ReportRun | { error: string }; onClose: () => void }) {
  if ("error" in run) return <div className="notice bad-notice small">{run.error}</div>;
  return (
    <div className="ai-strip">
      <span className="ai-tag">AI</span>
      <span className="grow">
        {run.summary || "The AI designed the report."}{" "}
        <span className="muted">
          Added {run.added.length} {run.added.length === 1 ? "visual" : "visuals"}
          {run.refused.length > 0 && `; ${run.refused.length} refused by the checks: ${run.refused.map((r) => `${r.label}: ${r.error}`).join("; ")}`}
        </span>
      </span>
      <button className="tool" onClick={onClose} aria-label="Dismiss">
        <X size={14} />
      </button>
    </div>
  );
}

// Layout on the canvas: a 12-column grid. Visuals saved before the canvas get a place by first fit.

const COLUMNS = 12;
const ROW = 30;
const GAP = 12;

function defaultSize(v: Visual, kpis: number): { w: number; h: number } {
  if (v.type === "kpi") return { w: Math.max(2, Math.floor(COLUMNS / Math.min(Math.max(kpis, 1), 6))), h: v.insight ? 7 : 5 };
  if (v.type === "table") return { w: COLUMNS, h: 9 };
  return { w: v.width === "full" ? COLUMNS : 6, h: 8 };
}

const overlaps = (a: GridBox, b: GridBox) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

function firstFit(size: { w: number; h: number }, placed: GridBox[]): GridBox {
  for (let y = 0; ; y++) {
    for (let x = 0; x + size.w <= COLUMNS; x++) {
      const box = { x, y, ...size };
      if (!placed.some((p) => overlaps(p, box))) return box;
    }
  }
}

export function layoutOf(visuals: Visual[]): Record<string, GridBox> {
  const out: Record<string, GridBox> = {};
  const placed: GridBox[] = [];
  for (const v of visuals) if (v.layout) placed.push((out[v.id] = v.layout));
  const kpis = visuals.filter((v) => v.type === "kpi").length;
  for (const v of visuals) if (!v.layout) placed.push((out[v.id] = firstFit(defaultSize(v, kpis), placed)));
  return out;
}

/** After a move or resize, push anything it now covers down, keeping the moved visual where it was dropped. */
function settle(boxes: Record<string, GridBox>, moved: string): Record<string, GridBox> {
  const out: Record<string, GridBox> = { [moved]: boxes[moved] };
  const others = Object.entries(boxes)
    .filter(([id]) => id !== moved)
    .sort((a, b) => a[1].y - b[1].y || a[1].x - b[1].x);
  for (const [id, box] of others) {
    const next = { ...box };
    let hit = Object.values(out).find((b) => overlaps(b, next));
    while (hit) {
      next.y = hit.y + hit.h;
      hit = Object.values(out).find((b) => overlaps(b, next));
    }
    out[id] = next;
  }
  return out;
}

const same = (a: GridBox, b: GridBox) => a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;

function MiniPage({ page }: { page: ReportPage }) {
  const boxes = layoutOf(page.visuals);
  const rows = Math.max(12, ...Object.values(boxes).map((b) => b.y + b.h));
  return (
    <span className="mini-page" aria-hidden="true">
      {page.visuals.map((v) => {
        const b = boxes[v.id];
        return (
          <i
            key={v.id}
            className={v.type === "kpi" ? "kpi" : ""}
            style={{ left: `${(b.x / COLUMNS) * 100}%`, top: `${(b.y / rows) * 100}%`, width: `${(b.w / COLUMNS) * 100}%`, height: `${(b.h / rows) * 100}%` }}
          />
        );
      })}
    </span>
  );
}

type FieldDrag = { kind: "measure"; name: string } | { kind: "column"; table: string; column: string };
const FIELD_TYPE = "application/x-datawazi-field";

function readField(e: DragEvent): FieldDrag | null {
  try {
    return JSON.parse(e.dataTransfer.getData(FIELD_TYPE)) as FieldDrag;
  } catch {
    return null;
  }
}

const hasField = (e: DragEvent) => e.dataTransfer.types.includes(FIELD_TYPE);

type Drag = { id: string; mode: "move" | "resize"; startX: number; startY: number; origin: GridBox };

function ReportCanvas(props: {
  dashboardId: string;
  page: ReportPage;
  formats: Record<string, MeasureFormat>;
  fields: ReportField[];
  editing: boolean;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onChange: Change;
  chosen: PageFilter[];
  onChoose: (filters: PageFilter[]) => void;
  onPick: (visual: Visual, value: Value) => void;
  onDrillUp: (visualId: string) => void;
}) {
  const { dashboardId, page, formats, editing, selected, onSelect, onChange } = props;
  const grid = useRef<HTMLDivElement>(null);
  const saved = useMemo(() => layoutOf(page.visuals), [page.visuals]);
  const [boxes, setBoxes] = useState(saved);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [dropCell, setDropCell] = useState<{ x: number; y: number } | null>(null);
  useEffect(() => setBoxes(saved), [saved]);

  const metrics = () => {
    const width = grid.current?.clientWidth ?? 1200;
    return { col: (width - GAP * (COLUMNS - 1)) / COLUMNS + GAP, row: ROW + GAP };
  };

  function begin(e: ReactPointerEvent, id: string, mode: Drag["mode"]) {
    if (!editing || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    onSelect(id);
    setDrag({ id, mode, startX: e.clientX, startY: e.clientY, origin: boxes[id] });
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }

  function move(e: ReactPointerEvent) {
    if (!drag) return;
    const { col, row } = metrics();
    const dx = Math.round((e.clientX - drag.startX) / col);
    const dy = Math.round((e.clientY - drag.startY) / row);
    const o = drag.origin;
    const next =
      drag.mode === "move"
        ? { ...o, x: Math.min(Math.max(0, o.x + dx), COLUMNS - o.w), y: Math.max(0, o.y + dy) }
        : { ...o, w: Math.min(Math.max(2, o.w + dx), COLUMNS - o.x), h: Math.max(2, o.h + dy) };
    if (!same(next, boxes[drag.id])) setBoxes(settle({ ...boxes, [drag.id]: next }, drag.id));
  }

  async function end() {
    if (!drag) return;
    setDrag(null);
    const changed = Object.fromEntries(Object.entries(boxes).filter(([id, b]) => !page.visuals.find((v) => v.id === id)?.layout || !same(b, saved[id])));
    if (Object.keys(changed).length && !same(boxes[drag.id], drag.origin)) {
      const next = await onChange(() => reportApi.setLayout(dashboardId, page.id, boxes));
      if (!next) setBoxes(saved);
    }
  }

  function cellAt(e: DragEvent) {
    const rect = grid.current!.getBoundingClientRect();
    const { col, row } = metrics();
    return { x: Math.min(COLUMNS - 1, Math.max(0, Math.floor((e.clientX - rect.left) / col))), y: Math.max(0, Math.floor((e.clientY - rect.top) / row)) };
  }

  async function dropField(e: DragEvent) {
    e.preventDefault();
    setDropCell(null);
    const field = readField(e);
    if (!field) return;
    const cell = cellAt(e);
    const firstMeasure = props.fields.flatMap((t) => t.measures)[0];
    const body =
      field.kind === "measure"
        ? { type: "kpi" as VisualType, title: field.name, measures: [field.name] }
        : firstMeasure
          ? { type: "bar" as VisualType, title: `${firstMeasure} by ${field.column}`, measures: [firstMeasure], group_by: [{ table: field.table, column: field.column }] }
          : null;
    if (!body) return;
    const size = body.type === "kpi" ? { w: 3, h: 5 } : { w: 6, h: 8 };
    const spot = { x: Math.min(cell.x, COLUMNS - size.w), y: cell.y, ...size };
    const free = Object.values(boxes).some((b) => overlaps(b, spot)) ? firstFit(size, Object.values(boxes)) : spot;
    const next = await onChange(() => reportApi.addVisual(dashboardId, page.id, { ...body, layout: free }));
    const added = next?.pages.find((p) => p.id === page.id)?.visuals.find((v) => !boxes[v.id]);
    if (added) onSelect(added.id);
  }

  const rows = Math.max(...Object.values(boxes).map((b) => b.y + b.h), 0);

  return (
    <div className="report-page-frame">
      <header className="report-head">
        <h2>{page.title}</h2>
        {page.summary && (
          <p className="page-summary">
            <span className="badge ai">AI</span> {page.summary}
          </p>
        )}
      </header>
      <SlicerBar dashboardId={dashboardId} page={page} fields={props.fields} editing={editing} chosen={props.chosen} onChoose={props.onChoose} onChange={onChange} />
      <div
        ref={grid}
        className={`canvas-grid ${editing ? "editing" : ""} ${drag ? "dragging" : ""}`}
        style={{ gridAutoRows: ROW, gap: GAP }}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={() => (setDrag(null), setBoxes(saved))}
        onClick={(e) => e.target === e.currentTarget && onSelect(null)}
        onDragOver={(e) => {
          if (!editing || !hasField(e)) return;
          e.preventDefault();
          setDropCell(cellAt(e));
        }}
        onDragLeave={(e) => e.currentTarget === e.target && setDropCell(null)}
        onDrop={dropField}
      >
        {page.visuals.map((v) => {
          const b = boxes[v.id];
          if (!b) return null;
          return (
            <article
              key={v.id}
              className={`canvas-visual visual-${v.type} ${selected === v.id && editing ? "on" : ""} ${drag?.id === v.id ? "lifted" : ""} ${v.status === "AI" && editing ? "suggested" : ""}`}
              style={{ gridColumn: `${b.x + 1} / span ${b.w}`, gridRow: `${b.y + 1} / span ${b.h}`, order: b.y * COLUMNS + b.x }}
              onClick={(e) => {
                e.stopPropagation();
                if (editing) onSelect(v.id);
              }}
            >
              <VisualFrame visual={v} editing={editing} onGrab={(e) => begin(e, v.id, "move")} onDrillUp={() => props.onDrillUp(v.id)}>
                {v.type === "kpi" ? (
                  <KpiCard visual={v} format={formats[v.measures[0]]} />
                ) : (
                  <ChartVisual
                    visual={v}
                    formats={formats}
                    fill
                    picked={props.chosen.find((f) => f.source === v.id)?.values}
                    onPick={v.group_by.length === 1 || v.type === "table" ? (value) => props.onPick(v, value) : undefined}
                  />
                )}
              </VisualFrame>
              {editing && selected === v.id && <span className="resize-handle" onPointerDown={(e) => begin(e, v.id, "resize")} title="Drag to resize" aria-hidden="true" />}
            </article>
          );
        })}
        {editing && (
          <div className={`canvas-drop ${dropCell ? "active" : ""}`} style={{ gridColumn: "1 / -1", gridRow: `${rows + 1} / span 3` }}>
            Drop a measure for a KPI card, or a column for a chart. Drag from Fields on the right.
          </div>
        )}
        {dropCell && <div className="drop-ghost" style={{ gridColumn: `${Math.min(dropCell.x, COLUMNS - 3) + 1} / span 3`, gridRow: `${dropCell.y + 1} / span 4` }} />}
      </div>
    </div>
  );
}

function VisualFrame(props: { visual: Visual; editing: boolean; onGrab: (e: ReactPointerEvent) => void; onDrillUp: () => void; children: ReactNode }) {
  const { visual, editing } = props;
  // The AI sometimes titles a card just "KPI"; the measure name says more.
  const title = visual.type === "kpi" && /^\s*kpi\s*$/i.test(visual.title) ? visual.measures[0] ?? visual.title : visual.title;
  return (
    <>
      <header className="visual-head" onPointerDown={editing ? props.onGrab : undefined}>
        {editing && <GripVertical size={13} className="grip" aria-hidden="true" />}
        <h3>{title}</h3>
        {editing && visual.status === "AI" && <span className="badge ai">AI</span>}
        {visual.drill ? (
          <span className="drill-state">
            <span className="drill-path">{visual.drill.filters.map((f) => f.values?.join(", ")).join(" › ")}</span>
            <button className="tool" onPointerDown={(e) => e.stopPropagation()} onClick={(e) => (e.stopPropagation(), props.onDrillUp())} title="Drill up" aria-label="Drill up">
              <ArrowUp size={13} />
            </button>
          </span>
        ) : (
          visual.drill_next && <span className="drill-hint" title={`Click a point to see it by ${visual.drill_next}`}>Click to drill</span>
        )}
      </header>
      <div className="visual-body">{visual.error ? <p className="bad small">{visual.error}</p> : props.children}</div>
      {editing && visual.advice?.map((tip) => (
        <p key={tip} className="visual-tip">
          Tip: {tip}
        </p>
      ))}
      {visual.insight && (
        <p className="insight" title={visual.insight.text}>
          {visual.insight.text}
          {!visual.insight.check.verified && (
            <span className="unverified" title="These numbers do not match the data in this visual">
              {" "}⚠ Unverified number{visual.insight.check.unmatched.length > 1 ? "s" : ""}: {visual.insight.check.unmatched.join(", ")}
            </span>
          )}
        </p>
      )}
    </>
  );
}

// The Build and Format panes

const TYPES: { type: VisualType; label: string }[] = [
  { type: "kpi", label: "KPI" },
  { type: "bar", label: "Bar" },
  { type: "column", label: "Column" },
  { type: "line", label: "Line" },
  { type: "area", label: "Area" },
  { type: "stacked_bar", label: "Stacked bar" },
  { type: "stacked_column", label: "Stacked column" },
  { type: "pie", label: "Pie" },
  { type: "donut", label: "Donut" },
  { type: "scatter", label: "Scatter" },
  { type: "table", label: "Table" },
];

type Well = { key: string; label: string; kind: "measure" | "column"; max: number; index?: number };

function wellsFor(type: VisualType): Well[] {
  switch (type) {
    case "kpi":
      return [{ key: "value", label: "Value", kind: "measure", max: 1 }];
    case "stacked_bar":
    case "stacked_column":
      return [
        { key: "axis", label: "Axis", kind: "column", max: 1, index: 0 },
        { key: "legend", label: "Legend", kind: "column", max: 1, index: 1 },
        { key: "values", label: "Value", kind: "measure", max: 1 },
      ];
    case "scatter":
      return [
        { key: "axis", label: "Details", kind: "column", max: 1, index: 0 },
        { key: "values", label: "Across, then up", kind: "measure", max: 2 },
      ];
    case "table":
      return [
        { key: "rows", label: "Rows", kind: "column", max: 3 },
        { key: "values", label: "Values", kind: "measure", max: 5 },
      ];
    case "pie":
    case "donut":
      return [
        { key: "axis", label: "Slices", kind: "column", max: 1, index: 0 },
        { key: "values", label: "Value", kind: "measure", max: 1 },
      ];
    default:
      return [
        { key: "axis", label: type === "bar" ? "Y-axis" : "X-axis", kind: "column", max: 1, index: 0 },
        { key: "values", label: type === "bar" ? "X-axis values" : "Y-axis values", kind: "measure", max: type === "line" || type === "area" ? 5 : 2 },
      ];
  }
}

function tableName(fields: ReportField[], id: string) {
  return fields.find((t) => t.id === id || t.name === id)?.name ?? id;
}

function VisualPane(props: { dashboardId: string; visual: Visual; fields: ReportField[]; onChange: Change; onClose: () => void }) {
  const { dashboardId, visual, fields, onChange } = props;
  const [tab, setTab] = useState<"build" | "format">("build");
  const [title, setTitle] = useState(visual.title);
  const [over, setOver] = useState<string | null>(null);
  const update = (body: Parameters<typeof reportApi.updateVisual>[2]) => onChange(() => reportApi.updateVisual(dashboardId, visual.id, body));
  const wells = wellsFor(visual.type);

  function changeType(type: VisualType) {
    if (type === visual.type) return;
    // Keep what fits the new type, so most switches are one click.
    const measures = type === "kpi" || type === "pie" || type === "donut" || type.startsWith("stacked") ? visual.measures.slice(0, 1) : visual.measures;
    const group_by = type === "kpi" ? [] : type.startsWith("stacked") || type === "table" ? visual.group_by : visual.group_by.slice(0, 1);
    void update({ type, measures, group_by });
  }

  function put(well: Well, field: FieldDrag) {
    if (field.kind !== well.kind) return;
    if (field.kind === "measure") {
      const rest = visual.measures.filter((m) => m !== field.name);
      const measures = well.max === 1 ? [field.name] : [...rest, field.name].slice(-well.max);
      void update({ measures });
    } else {
      const ref = { table: field.table, column: field.column };
      let group_by = [...visual.group_by];
      if (well.index !== undefined) group_by[well.index] = ref;
      else group_by = [...group_by.filter((g) => !(g.table === ref.table && g.column === ref.column)), ref].slice(-well.max);
      void update({ group_by: group_by.filter(Boolean) });
    }
  }

  function remove(well: Well, i: number) {
    if (well.kind === "measure") void update({ measures: visual.measures.filter((_, j) => j !== i) });
    else void update({ group_by: visual.group_by.filter((_, j) => j !== (well.index ?? i)) });
  }

  function pills(well: Well) {
    if (well.kind === "measure") return visual.measures.map((m) => ({ label: m, tag: <span className="type-tag type-tag-measure">fx</span> }));
    const refs = well.index !== undefined ? visual.group_by.slice(well.index, well.index + 1) : visual.group_by;
    return refs.map((g) => ({ label: `${tableName(fields, g.table)}[${g.column}]`, tag: <span className="type-tag">col</span> }));
  }

  return (
    <>
      <div className="pane-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "build"} onClick={() => setTab("build")}>
          Build
        </button>
        <button role="tab" aria-selected={tab === "format"} onClick={() => setTab("format")}>
          Format
        </button>
        <span className="grow" />
        <button className="tool" onClick={props.onClose} aria-label="Close visual settings" title="Back to the page">
          <X size={14} />
        </button>
      </div>
      <div className="props">
        {visual.status === "AI" && (
          <div className="suggest-card">
            <span className="ai-tag">Suggested by AI</span>
            {visual.reason && <p>{visual.reason}</p>}
            <div className="actions">
              <button className="small-button" onClick={() => onChange(() => reportApi.approve(dashboardId, visual.id))}>
                Accept
              </button>
              <button className="secondary small-button" onClick={() => onChange(() => reportApi.removeVisual(dashboardId, visual.id))}>
                Reject
              </button>
            </div>
          </div>
        )}
        {tab === "build" ? (
          <>
            <div className="type-chips" role="radiogroup" aria-label="Visual type">
              {TYPES.map((t) => (
                <button key={t.type} role="radio" aria-checked={visual.type === t.type} className={visual.type === t.type ? "on" : ""} onClick={() => changeType(t.type)}>
                  {t.label}
                </button>
              ))}
            </div>
            {wells.map((well) => (
              <div key={well.key} className="well">
                <span className="well-label">{well.label}</span>
                <div
                  className={`well-slot ${pills(well).length ? "filled" : ""} ${over === well.key ? "target" : ""}`}
                  onDragOver={(e) => {
                    if (!hasField(e)) return;
                    e.preventDefault();
                    setOver(well.key);
                  }}
                  onDragLeave={() => setOver(null)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setOver(null);
                    const field = readField(e);
                    if (field) put(well, field);
                  }}
                >
                  {pills(well).map((p, i) => (
                    <span key={p.label} className="pill">
                      {p.tag}
                      {p.label}
                      <button className="pill-x" onClick={() => remove(well, i)} aria-label={`Remove ${p.label}`}>
                        <X size={11} />
                      </button>
                    </span>
                  ))}
                  {!pills(well).length && <span className="well-empty">Drag a {well.kind === "measure" ? "measure" : "column"} here</span>}
                </div>
              </div>
            ))}
            <FieldList fields={fields} onPick={(f) => put(wells.find((w) => w.kind === f.kind && (w.index === undefined || w.index === 0)) ?? wells[0], f)} />
          </>
        ) : (
          <>
            <label className="prop">
              <span>Title</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onBlur={() => title.trim() && title !== visual.title && void update({ title: title.trim() })}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
              />
            </label>
            {visual.type === "kpi" && (
              <label className="prop">
                <span>A rise is</span>
                <select value={visual.kpi?.direction ?? "higher_is_better"} onChange={(e) => void update({ kpi: { ...visual.kpi, direction: e.target.value as "higher_is_better" | "lower_is_better" } })}>
                  <option value="higher_is_better">Good (green)</option>
                  <option value="lower_is_better">Bad (orange), e.g. costs or returns</option>
                </select>
              </label>
            )}
            {visual.layout && (
              <p className="muted small">
                {visual.layout.w} of 12 columns wide, {visual.layout.h} rows tall. Drag the visual's title to move it and its corner to resize.
              </p>
            )}
            {visual.advice?.map((tip) => (
              <p key={tip} className="visual-tip">
                Tip: {tip}
              </p>
            ))}
            <div className="actions">
              <button className="secondary danger" onClick={() => onChange(() => reportApi.removeVisual(dashboardId, visual.id))}>
                <Trash2 size={14} aria-hidden="true" /> Remove visual
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function FieldList({ fields, onPick }: { fields: ReportField[]; onPick?: (f: FieldDrag) => void }) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const start = (e: DragEvent, field: FieldDrag) => {
    e.dataTransfer.setData(FIELD_TYPE, JSON.stringify(field));
    e.dataTransfer.effectAllowed = "copy";
  };
  return (
    <div className="field-list">
      <span className="well-label">Fields</span>
      <input className="field-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search fields" aria-label="Search fields" />
      <ul className="tree" role="tree">
        {fields.map((t) => {
          const measures = t.measures.filter((m) => !q || m.toLowerCase().includes(q));
          const columns = t.columns.filter((c) => !q || c.name.toLowerCase().includes(q));
          if (!measures.length && !columns.length) return null;
          return (
            <li key={t.id || t.name} role="treeitem" aria-expanded="true">
              <div className="tree-row table-row static">
                {t.name === "Date" ? <Calendar size={14} aria-hidden="true" /> : <Table2 size={14} aria-hidden="true" />}
                <span className="tree-name">{t.name}</span>
              </div>
              <ul role="group">
                {measures.map((m) => (
                  <li key={m} role="treeitem" className="tree-row column-row draggable" draggable onDragStart={(e) => start(e, { kind: "measure", name: m })} onDoubleClick={() => onPick?.({ kind: "measure", name: m })} title="Drag onto the canvas or a well; double-click to add">
                    <span className="type-tag type-tag-measure">fx</span>
                    <span className="tree-name">{m}</span>
                  </li>
                ))}
                {columns.map((c) => (
                  <li key={c.name} role="treeitem" className="tree-row column-row draggable" draggable onDragStart={(e) => start(e, { kind: "column", table: t.id, column: c.name })} onDoubleClick={() => onPick?.({ kind: "column", table: t.id, column: c.name })} title="Drag onto the canvas or a well; double-click to add">
                    <TypeTag type={c.type} />
                    <span className="tree-name">{c.name}</span>
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PagePane(props: {
  dashboardId: string;
  page: ReportPage | null;
  fields: ReportField[];
  pending: number;
  hasKey: boolean;
  building: boolean;
  canBuild: boolean;
  onBuild: () => void;
  onChange: Change;
  onSelect: (id: string | null) => void;
}) {
  const { dashboardId, page, onChange } = props;
  const [title, setTitle] = useState(page?.title ?? "");
  useEffect(() => setTitle(page?.title ?? ""), [page?.id, page?.title]);
  const aiVisuals = page?.visuals.filter((v) => v.status === "AI") ?? [];

  return (
    <>
      <div className="pane-head">
        <span>Page</span>
        <span className="grow" />
        <span className="pane-sub">{page?.title}</span>
      </div>
      <div className="props">
        {page && (
          <>
            <label className="prop">
              <span>Title</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onBlur={() => title.trim() && title !== page.title && void onChange(() => reportApi.renamePage(dashboardId, page.id, title.trim()))}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
              />
            </label>
            {page.status === "AI" && (
              <div className="suggest-card">
                <span className="ai-tag">Suggested by AI</span>
                <p>{page.reason || "The AI designed this page."}</p>
                <div className="actions">
                  <button className="small-button" onClick={() => onChange(() => reportApi.approve(dashboardId, page.id))}>
                    Accept page
                  </button>
                </div>
              </div>
            )}
            {aiVisuals.length > 0 && (
              <div className="pending-list">
                <div className="pending-head">
                  <strong>AI visuals waiting ({aiVisuals.length})</strong>
                  <button className="link small" onClick={() => onChange(() => reportApi.approve(dashboardId))}>
                    Accept all
                  </button>
                </div>
                {aiVisuals.map((v) => (
                  <div key={v.id} className="pending-item">
                    <button className="link" onClick={() => props.onSelect(v.id)}>
                      {v.title}
                    </button>
                  </div>
                ))}
              </div>
            )}
            <p className="muted small">Click a visual to change its fields and format. Drag a field onto the canvas to add a visual, or onto the slicer bar to add a slicer.</p>
            <PageFilters dashboardId={dashboardId} page={page} fields={props.fields} onChange={onChange} />
            <FieldList fields={props.fields} />
            <div className="actions">
              <button className="secondary danger" onClick={() => onChange(() => reportApi.removePage(dashboardId, page.id))}>
                <Trash2 size={14} aria-hidden="true" /> Remove page
              </button>
            </div>
          </>
        )}
        {props.canBuild && (
          <div className="suggest-card">
            <span className="ai-tag">AI</span>
            <p>Let the AI draft pages: KPI cards with comparisons, sorted bar charts, trends and detail tables. Each visual is an ordinary one you edit.</p>
            <button className="ai-button" onClick={props.onBuild} disabled={!props.hasKey || props.building} title={props.hasKey ? undefined : "Add an API key in Settings first"}>
              <Sparkles size={14} aria-hidden="true" /> {props.building ? "AI is designing…" : page ? "Add pages with AI" : "Build report with AI"}
            </button>
          </div>
        )}
      </div>
    </>
  );
}

// Slicers and page filters

type Value = string | number | boolean;

const sameColumn = (a: { table: string; column: string }, b: { table: string; column: string }) => a.table === b.table && a.column === b.column;

function valuesText(values: Value[] | undefined, max = 2) {
  if (!values?.length) return "All";
  const shown = values.slice(0, max).map(String).join(", ");
  return values.length > max ? `${shown} +${values.length - max}` : shown;
}

function filterText(f: PageFilter) {
  const verb = { in: "is", not_in: "is not", equals: "is", not_equals: "is not" }[f.operator] ?? f.operator.replace(/_/g, " ");
  const value = f.values ? valuesText(f.values, 3) : f.value ?? "";
  return `${f.column} ${verb} ${value}`.trim();
}

/** Tick the values to keep. The list comes from the dataset's published version. */
function ValuePicker(props: { dashboardId: string; table: string; column: string; initial: Value[]; onApply: (values: Value[]) => void }) {
  const [values, setValues] = useState<Value[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Value[]>(props.initial);
  const [query, setQuery] = useState("");

  useEffect(() => {
    reportApi
      .values(props.dashboardId, props.table, props.column)
      .then((r) => {
        setValues(r.values);
        setTruncated(r.truncated);
      })
      .catch((e) => setError((e as Error).message));
  }, [props.dashboardId, props.table, props.column]);

  const q = query.trim().toLowerCase();
  const shown = (values ?? []).filter((v) => !q || String(v).toLowerCase().includes(q));
  const toggle = (v: Value) => setPicked((cur) => (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]));

  return (
    <div className="value-picker">
      <div className="pop-head">{props.column}</div>
      {error && <p className="bad small pad">{error}</p>}
      {!values && !error && <p className="muted small pad">Loading values…</p>}
      {values && (
        <>
          {values.length > 8 && <input className="field-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search values" aria-label="Search values" />}
          <ul className="value-list" role="group" aria-label={`Values of ${props.column}`}>
            {shown.map((v) => (
              <li key={String(v)}>
                <label>
                  <input type="checkbox" checked={picked.includes(v)} onChange={() => toggle(v)} />
                  <span>{String(v)}</span>
                </label>
              </li>
            ))}
          </ul>
          {truncated && <p className="muted small pad">Showing the first {values.length} values; search to find others.</p>}
        </>
      )}
      <div className="value-actions">
        <button className="secondary small-button" onClick={() => setPicked([])} disabled={!picked.length}>
          Clear
        </button>
        <button className="small-button" onClick={() => props.onApply(picked)}>
          Apply
        </button>
      </div>
    </div>
  );
}

function SlicerBar(props: {
  dashboardId: string;
  page: ReportPage;
  fields: ReportField[];
  editing: boolean;
  chosen: PageFilter[];
  onChoose: (filters: PageFilter[]) => void;
  onChange: Change;
}) {
  const { dashboardId, page, editing, chosen, onChoose, onChange } = props;
  const [open, setOpen] = useState<{ index: number; x: number; y: number } | null>(null);
  const [over, setOver] = useState(false);
  const slicers = page.slicers ?? [];
  const fixed = page.filters ?? [];
  if (!slicers.length && !fixed.length && !chosen.length && !editing) return null;

  const setSlicers = (next: { table: string; column: string }[]) => onChange(() => reportApi.setSlicers(dashboardId, page.id, next));

  function apply(slicer: { table: string; column: string }, values: Value[]) {
    const rest = chosen.filter((f) => !sameColumn(f, slicer));
    onChoose(values.length ? [...rest, { ...slicer, operator: "in", values }] : rest);
    setOpen(null);
  }

  return (
    <div
      className={`slicer-bar ${over ? "target" : ""}`}
      aria-label="Slicers and filters"
      onDragOver={(e) => {
        if (!editing || !hasField(e)) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const field = readField(e);
        if (field?.kind === "column" && !slicers.some((s) => sameColumn(s, field))) void setSlicers([...slicers, { table: field.table, column: field.column }]);
      }}
    >
      {fixed.map((f) => (
        <span key={`${f.table}${f.column}`} className="filter-chip" title="A page filter: it applies for everyone who opens this page">
          <Filter size={12} aria-hidden="true" /> {filterText(f)}
        </span>
      ))}
      {slicers.map((s, i) => {
        const current = chosen.find((f) => sameColumn(f, s));
        return (
          <span key={`${s.table}${s.column}`} className={`slicer ${current ? "on" : ""}`}>
            <button
              className="slicer-button"
              aria-haspopup="menu"
              onClick={(e) => {
                const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
                setOpen({ index: i, x: box.left, y: box.bottom + 4 });
              }}
            >
              <span className="slicer-label">{s.column}</span>
              <span className="slicer-value">{valuesText(current?.values)}</span>
              <ChevronDown size={13} aria-hidden="true" />
            </button>
            {editing && (
              <button className="pill-x" aria-label={`Remove the ${s.column} slicer`} title="Remove slicer" onClick={() => {
                onChoose(chosen.filter((f) => !sameColumn(f, s)));
                void setSlicers(slicers.filter((_, j) => j !== i));
              }}>
                <X size={11} />
              </button>
            )}
          </span>
        );
      })}
      {chosen
        .filter((f) => f.source && !slicers.some((sl) => sameColumn(sl, f)))
        .map((f) => (
          <span key={`${f.source}${f.column}`} className="filter-chip picked" title="Picked by clicking a visual">
            {filterText(f)}
            <button className="pill-x" aria-label={`Remove ${filterText(f)}`} onClick={() => onChoose(chosen.filter((x) => x !== f))}>
              <X size={11} />
            </button>
          </span>
        ))}
      {chosen.length > 0 && (
        <>
          <button className="link small" onClick={() => onChoose([])}>
            Clear selection
          </button>
          <span className="slicer-note">Insights are hidden for a selection: they were written about the whole page.</span>
        </>
      )}
      {editing && !slicers.length && <span className="slicer-note">Drop a column here to add a slicer</span>}
      {open && slicers[open.index] && (
        <PopMenu x={open.x} y={open.y} onClose={() => setOpen(null)} label={`${slicers[open.index].column} slicer`}>
          <ValuePicker
            dashboardId={dashboardId}
            table={slicers[open.index].table}
            column={slicers[open.index].column}
            initial={chosen.find((f) => sameColumn(f, slicers[open.index]))?.values ?? []}
            onApply={(values) => apply(slicers[open.index], values)}
          />
        </PopMenu>
      )}
    </div>
  );
}

/** The Page pane's filters: slicers offered to viewers, and fixed filters that apply for everyone. */
function PageFilters({ dashboardId, page, fields, onChange }: { dashboardId: string; page: ReportPage; fields: ReportField[]; onChange: Change }) {
  const [picking, setPicking] = useState<{ table: string; column: string; x: number; y: number } | null>(null);
  const columns = fields.flatMap((t) => t.columns.map((c) => ({ table: t.id, column: c.name, label: `${t.name}[${c.name}]` })));
  const slicers = page.slicers ?? [];
  const fixed = page.filters ?? [];
  const keyOf = (c: { table: string; column: string }) => `${c.table}\u0000${c.column}`;
  const byKey = new Map(columns.map((c) => [keyOf(c), c]));

  return (
    <div className="page-filters">
      <span className="well-label">Slicers</span>
      <p className="muted small">Viewers pick values; each person's choice is their own and nothing is saved.</p>
      <div className="well-slot filled">
        {slicers.map((s, i) => (
          <span key={keyOf(s)} className="pill">
            {byKey.get(keyOf(s))?.label ?? s.column}
            <button className="pill-x" aria-label={`Remove the ${s.column} slicer`} onClick={() => onChange(() => reportApi.setSlicers(dashboardId, page.id, slicers.filter((_, j) => j !== i)))}>
              <X size={11} />
            </button>
          </span>
        ))}
        <select
          className="add-select"
          value=""
          aria-label="Add a slicer"
          disabled={slicers.length >= 4}
          onChange={(e) => {
            const c = byKey.get(e.target.value);
            if (c) void onChange(() => reportApi.setSlicers(dashboardId, page.id, [...slicers, { table: c.table, column: c.column }]));
          }}
        >
          <option value="">{slicers.length >= 4 ? "Four slicers at most" : "+ Add a slicer"}</option>
          {columns.filter((c) => !slicers.some((s) => sameColumn(s, c))).map((c) => (
            <option key={keyOf(c)} value={keyOf(c)}>
              {c.label}
            </option>
          ))}
        </select>
      </div>

      <span className="well-label">Page filters</span>
      <p className="muted small">Always on, for everyone who opens this page.</p>
      <div className="well-slot filled">
        {fixed.map((f, i) => (
          <span key={keyOf(f)} className="pill">
            {filterText(f)}
            <button className="pill-x" aria-label={`Remove the filter on ${f.column}`} onClick={() => onChange(() => reportApi.setFilters(dashboardId, page.id, fixed.filter((_, j) => j !== i)))}>
              <X size={11} />
            </button>
          </span>
        ))}
        <select
          className="add-select"
          value=""
          aria-label="Add a page filter"
          onChange={(e) => {
            const c = byKey.get(e.target.value);
            const box = e.currentTarget.getBoundingClientRect();
            if (c) setPicking({ table: c.table, column: c.column, x: box.left, y: box.bottom + 4 });
          }}
        >
          <option value="">+ Add a filter</option>
          {columns.filter((c) => !fixed.some((f) => sameColumn(f, c))).map((c) => (
            <option key={keyOf(c)} value={keyOf(c)}>
              {c.label}
            </option>
          ))}
        </select>
      </div>
      {picking && (
        <PopMenu x={picking.x} y={picking.y} onClose={() => setPicking(null)} label={`Filter on ${picking.column}`}>
          <ValuePicker
            dashboardId={dashboardId}
            table={picking.table}
            column={picking.column}
            initial={[]}
            onApply={(values) => {
              const target = picking;
              setPicking(null);
              if (values.length) void onChange(() => reportApi.setFilters(dashboardId, page.id, [...fixed, { table: target.table, column: target.column, operator: "in", values }]));
            }}
          />
        </PopMenu>
      )}
    </div>
  );
}

// KPI card: label + period, value, change line (arrow + sign + amount + % + comparison), sparkline, top driver.

export function KpiCard({ visual, format }: { visual: Visual; format: MeasureFormat | undefined }) {
  const data = visual.data as KpiData;
  const lowerIsBetter = visual.kpi?.direction === "lower_is_better";
  const good = data.delta !== null && (lowerIsBetter ? data.delta <= 0 : data.delta >= 0);
  const sign = (v: number) => (v >= 0 ? "+" : "−");
  const change =
    data.delta !== null
      ? `${data.delta >= 0 ? "▲" : "▼"} ${sign(data.delta)}${formatCompact(Math.abs(data.delta), format)}${
          data.delta_pct !== null ? ` (${sign(data.delta_pct)}${Math.abs(data.delta_pct * 100).toFixed(1)}%)` : ""
        } ${data.comparison}`
      : null;
  const label = `${visual.title}: ${formatCompact(data.value, format)}${change ? `, ${change}` : ""}`;
  return (
    <div className="kpi" aria-label={label}>
      <div className="kpi-period">{data.period}</div>
      <div className="kpi-value">{formatCompact(data.value, format)}</div>
      {change ? (
        <div className="kpi-change" style={{ color: good ? "var(--ok)" : "var(--warn)" }}>
          {change}
        </div>
      ) : (
        <div className="kpi-note" title={data.note ?? undefined}>
          No comparison
        </div>
      )}
      {data.sparkline.length > 1 && <Sparkline points={data.sparkline.map((p) => p.value)} months={data.sparkline.length} />}
      {data.driver && (
        <div className="kpi-driver">
          Top driver: {data.driver.name} {sign(data.driver.delta)}
          {formatCompact(Math.abs(data.driver.delta), format)}
        </div>
      )}
    </div>
  );
}

function Sparkline({ points, months }: { points: number[]; months: number }) {
  const width = 120;
  const height = 28;
  const low = Math.min(...points);
  const range = Math.max(...points) - low || 1;
  const xy = points.map((v, i) => [Math.round((i / (points.length - 1)) * width), Math.round(height - ((v - low) / range) * height)]);
  const last = xy[xy.length - 1];
  return (
    <div className="kpi-spark">
      <svg viewBox={`-3 -3 ${width + 6} ${height + 6}`} width={width} height={height} aria-hidden="true">
        <polyline points={xy.map((p) => p.join(",")).join(" ")} fill="none" style={{ stroke: "var(--chart-accent)" }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={last[0]} cy={last[1]} r={3} style={{ fill: "var(--chart-accent)" }} />
      </svg>
      <span>{months} months</span>
    </div>
  );
}

// Charts

function axisValue(format: MeasureFormat | undefined) {
  return (v: number) => formatCompact(v, format);
}

/** A chart; `fill` makes it take its box on the canvas instead of a height of its own. */
export function ChartVisual(props: {
  visual: Visual;
  formats: Record<string, MeasureFormat>;
  fill?: boolean;
  picked?: Value[];
  onPick?: (value: Value) => void;
}) {
  const { visual, formats, fill = false, picked } = props;
  const data = visual.data as ChartData;
  const groups = visual.group_by.length;
  const pickedKey = JSON.stringify(picked ?? null);
  const theme = useTheme(); // chart colours come from the theme
  const option = useMemo(
    () => highlight(chartOption(visual, data, formats), visual, data, picked),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [visual, data, formats, pickedKey, theme],
  );
  const onPick = props.onPick;
  const pickName = onPick
    ? (name: string) => {
        const row = data.rows.find((r) => (r[0] === null ? "(blank)" : String(r[0])) === name);
        if (row && row[0] !== null) onPick(row[0] as Value);
      }
    : undefined;

  if (visual.type === "table") return <DetailTable visual={visual} data={data} formats={formats} picked={picked} onPick={onPick} />;
  const rows = data.rows.length;
  const bars = visual.type === "stacked_bar" ? new Set(data.rows.map((r) => r[0])).size : rows;
  const height = visual.type === "bar" || visual.type === "stacked_bar" ? Math.max(160, bars * 32 + 56) : 260;
  return (
    <>
      {option &&
        (fill ? (
          <div className="chart-fill">
            <Chart option={option} height="100%" label={`${visual.title} chart`} onPick={pickName} />
          </div>
        ) : (
          <Chart option={option} height={height} label={`${visual.title} chart`} onPick={pickName} />
        ))}
      <Footnotes data={data} groups={groups} />
    </>
  );
}

/** The category picked in this chart stays bright; the others fade, as in Power BI. */
function highlight(option: EChartsCoreOption | null, visual: Visual, data: ChartData, picked: Value[] | undefined) {
  if (!option || !picked?.length || visual.group_by.length !== 1) return option;
  const on = new Set(picked.map(String));
  const labels = data.rows.map((r) => (r[0] === null ? "(blank)" : String(r[0])));
  const series = (option as { series?: { type: string; data: unknown[] }[] }).series ?? [];
  return {
    ...option,
    series: series.map((s) => ({
      ...s,
      data: s.data.map((point, i) => {
        const label = s.type === "pie" ? (point as { name: string }).name : visual.type === "bar" ? [...labels].reverse()[i] : labels[i];
        const base = typeof point === "object" && point !== null ? point : { value: point };
        return on.has(label) ? base : { ...(base as object), itemStyle: { ...((base as { itemStyle?: object }).itemStyle ?? {}), opacity: 0.3 } };
      }),
    })),
  };
}

function Footnotes({ data, groups }: { data: ChartData; groups: number }) {
  return (
    <p className="visual-foot">
      {data.truncated && <span>Top {data.rows.length} of {data.total_rows}. </span>}
      {data.check && groups === 1 && (
        <span className={data.check.ok ? "check-ok" : "check-bad"}>
          {data.check.ok ? "✓ Parts add up to the total" : "⚠ Parts do not add up to the total"}
        </span>
      )}
    </p>
  );
}

function chartOption(visual: Visual, data: ChartData, formats: Record<string, MeasureFormat>): EChartsCoreOption | null {
  if (visual.type === "table") return null;
  const groups = visual.group_by.length;
  const labels = data.rows.map((r) => (r[0] === null ? "(blank)" : String(r[0])));
  const first = formats[visual.measures[0]];
  const single = visual.measures.length === 1;
  const colour = (i: number) => (single ? TOKENS.accent : SERIES[i % SERIES.length]);
  const tooltip = {
    trigger: ["donut", "pie", "scatter"].includes(visual.type) ? "item" : "axis",
    valueFormatter: (v: number) => formatCompact(v, first),
  };
  const legend = single ? undefined : { top: 0, textStyle: { color: TOKENS.muted } };
  const valueAxis = {
    type: "value",
    axisLabel: { color: TOKENS.muted, formatter: axisValue(first) },
    splitLine: { lineStyle: { color: TOKENS.grid } },
  };
  const categoryAxis = (reverse: boolean) => ({
    type: "category",
    data: reverse ? [...labels].reverse() : labels,
    axisLabel: { color: TOKENS.muted, width: 140, overflow: "truncate" },
    axisLine: { lineStyle: { color: TOKENS.grid } },
    axisTick: { show: false },
  });
  const series = (type: string, reverse = false) =>
    visual.measures.map((name, i) => {
      const values = data.rows.map((r) => r[groups + i] as number | null);
      return {
        name,
        type,
        data: reverse ? [...values].reverse() : values,
        itemStyle: { color: colour(i) },
        lineStyle: { color: colour(i), width: 2 },
        barMaxWidth: 28,
        showSymbol: false,
        label:
          type === "bar" && single
            ? { show: true, position: reverse ? "right" : "top", color: TOKENS.text, formatter: (p: { value: number }) => formatCompact(p.value, formats[name]) }
            : undefined,
      };
    });

  if (visual.type === "bar") {
    return {
      tooltip,
      legend,
      grid: { left: 8, right: 64, top: single ? 8 : 28, bottom: 8, containLabel: true },
      xAxis: { ...valueAxis, splitLine: { show: false }, axisLabel: { show: false } },
      yAxis: categoryAxis(true),
      series: series("bar", true),
    };
  }
  if (visual.type === "stacked_bar" || visual.type === "stacked_column") {
    // Rows are (category, stack, value); one series per stack, in the order they first appear.
    const horizontal = visual.type === "stacked_bar";
    const name = (v: unknown) => (v === null ? "(blank)" : String(v));
    const categories = [...new Set(data.rows.map((r) => name(r[0])))];
    const stacks = [...new Set(data.rows.map((r) => name(r[1])))];
    const value = new Map(data.rows.map((r) => [`${name(r[0])}\u0000${name(r[1])}`, r[2] as number | null]));
    const ordered = horizontal ? [...categories].reverse() : categories;
    const axis = { ...categoryAxis(false), data: ordered };
    return {
      tooltip,
      legend: { top: 0, textStyle: { color: TOKENS.muted }, type: "scroll" },
      grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
      xAxis: horizontal ? valueAxis : axis,
      yAxis: horizontal ? axis : valueAxis,
      series: stacks.map((stack, i) => ({
        name: stack,
        type: "bar",
        stack: "total",
        barMaxWidth: 28,
        itemStyle: { color: SERIES[i % SERIES.length] },
        data: ordered.map((c) => value.get(`${c}\u0000${stack}`) ?? null),
      })),
    };
  }
  if (visual.type === "scatter") {
    const [across, up] = visual.measures;
    return {
      tooltip: {
        trigger: "item",
        formatter: (p: { data: [number, number, string] }) =>
          `${p.data[2]}<br/>${across}: ${formatCompact(p.data[0], formats[across])}<br/>${up}: ${formatCompact(p.data[1], formats[up])}`,
      },
      grid: { left: 8, right: 24, top: 16, bottom: 28, containLabel: true },
      xAxis: { ...valueAxis, name: across, nameLocation: "middle", nameGap: 26, axisLabel: { color: TOKENS.muted, formatter: axisValue(formats[across]) } },
      yAxis: { ...valueAxis, name: up, axisLabel: { color: TOKENS.muted, formatter: axisValue(formats[up]) } },
      series: [
        {
          type: "scatter",
          symbolSize: 10,
          itemStyle: { color: TOKENS.accent, opacity: 0.8 },
          data: data.rows.map((r) => [r[1], r[2], r[0] === null ? "(blank)" : String(r[0])]),
        },
      ],
    };
  }
  if (visual.type === "donut" || visual.type === "pie") {
    return {
      tooltip,
      series: [
        {
          type: "pie",
          radius: visual.type === "pie" ? ["0%", "72%"] : ["48%", "72%"],
          data: data.rows.map((r, i) => ({
            name: r[0] === null ? "(blank)" : String(r[0]),
            value: r[1],
            itemStyle: { color: SERIES[i % SERIES.length] },
          })),
          label: { formatter: "{b}\n{d}%", color: TOKENS.text },
        },
      ],
    };
  }
  return {
    tooltip,
    legend,
    grid: { left: 8, right: 16, top: single ? 16 : 32, bottom: 8, containLabel: true },
    xAxis: categoryAxis(false),
    yAxis: valueAxis,
    series: series(visual.type === "line" || visual.type === "area" ? "line" : "bar").map((s, i) =>
      visual.type === "area" ? { ...s, areaStyle: { color: colour(i), opacity: 0.18 } } : s,
    ),
  };
}

function DetailTable(props: { visual: Visual; data: ChartData; formats: Record<string, MeasureFormat>; picked?: Value[]; onPick?: (value: Value) => void }) {
  const { visual, data, formats, picked, onPick } = props;
  const groups = visual.group_by.length;
  const key = data.rows.map((r) => Math.abs((r[groups] as number) ?? 0));
  const max = Math.max(...key, 0) || 1;
  return (
    <>
      <div className="table-wrap report-table">
        <table className="grid">
          <thead>
            <tr>
              {data.columns.map((c, i) => (
                <th key={c} className={i >= groups ? "num" : ""}>
                  {i < groups ? c.replace(/^.*\[(.*)\]$/, "$1") : c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, r) => (
              <tr
                key={r}
                className={`${onPick ? "pickable" : ""} ${picked?.length ? (picked.includes(row[0] as Value) ? "picked" : "faded") : ""}`}
                onClick={onPick && row[0] !== null ? () => onPick(row[0] as Value) : undefined}
              >
                {row.map((value, i) =>
                  i < groups ? (
                    <td key={i}>{value === null ? <span className="null">(blank)</span> : String(value)}</td>
                  ) : (
                    <td key={i} className="num">
                      {i === groups && (
                        <span className="data-bar" style={{ width: `${(Math.abs((value as number) ?? 0) / max) * 100}%` }} />
                      )}
                      <span className="data-value">
                        {formatMeasure(value as number | null, formats[visual.measures[i - groups]] ?? { type: "number", decimals: 2 })}
                      </span>
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Footnotes data={data} groups={groups} />
    </>
  );
}
