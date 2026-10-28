import type { GridBox, VisualType } from "../api";

/** The canvas layout (a 12-column grid) and a small drawing of a page from it: used by the dashboard editor's
    page thumbnails and by Home's dashboard cards. Visuals saved before the canvas get a place by first fit. */

/** What a layout needs to know about a visual. */
export type LaidOut = { id: string; type: VisualType; layout?: GridBox | null; width?: string | null; insight?: unknown };

export const COLUMNS = 12;

function defaultSize(v: LaidOut, kpis: number): { w: number; h: number } {
  if (v.type === "kpi") return { w: Math.max(2, Math.floor(COLUMNS / Math.min(Math.max(kpis, 1), 6))), h: v.insight ? 7 : 5 };
  if (v.type === "table") return { w: COLUMNS, h: 9 };
  return { w: v.width === "full" ? COLUMNS : 6, h: 8 };
}

export const overlaps = (a: GridBox, b: GridBox) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

export function firstFit(size: { w: number; h: number }, placed: GridBox[]): GridBox {
  for (let y = 0; ; y++) {
    for (let x = 0; x + size.w <= COLUMNS; x++) {
      const box = { x, y, ...size };
      if (!placed.some((p) => overlaps(p, box))) return box;
    }
  }
}

export function layoutOf(visuals: LaidOut[]): Record<string, GridBox> {
  const out: Record<string, GridBox> = {};
  const placed: GridBox[] = [];
  for (const v of visuals) if (v.layout) placed.push((out[v.id] = v.layout));
  const kpis = visuals.filter((v) => v.type === "kpi").length;
  for (const v of visuals) if (!v.layout) placed.push((out[v.id] = firstFit(defaultSize(v, kpis), placed)));
  return out;
}

// A tiny drawing of each visual's kind, so a page's thumbnail reads as its layout, not as a loading placeholder.
function MiniGlyph({ type }: { type: VisualType }) {
  const shape =
    type === "kpi" ? <path d="M3 9h10M3 5h6" /> :
    type === "line" || type === "area" ? <polyline points="2,12 6,7 9,9 14,3" /> :
    type === "pie" || type === "donut" ? <circle cx="8" cy="8" r="4.5" /> :
    type === "table" ? <path d="M2 4h12M2 8h12M2 12h12" /> :
    type === "scatter" ? <path d="M4 11h.01M7 6h.01M11 8h.01M12 3h.01" /> :
    type === "bar" || type === "stacked_bar" ? <path d="M2 4h11M2 8h7M2 12h9" /> :
    <path d="M4 14V8M8 14V3M12 14V6" />;
  return (
    <svg viewBox="0 0 16 16" preserveAspectRatio="xMidYMid meet">
      {shape}
    </svg>
  );
}

export function MiniPage({ visuals }: { visuals: LaidOut[] }) {
  const boxes = layoutOf(visuals);
  const rows = Math.max(12, ...Object.values(boxes).map((b) => b.y + b.h));
  return (
    <span className="mini-page" aria-hidden="true">
      {visuals.map((v) => {
        const b = boxes[v.id];
        return (
          <i
            key={v.id}
            className={v.type === "kpi" ? "kpi" : ""}
            style={{ left: `${(b.x / COLUMNS) * 100}%`, top: `${(b.y / rows) * 100}%`, width: `${(b.w / COLUMNS) * 100}%`, height: `${(b.h / rows) * 100}%` }}
          >
            <MiniGlyph type={v.type} />
          </i>
        );
      })}
    </span>
  );
}
