import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, type Lineage as LineageData, type LineageNode } from "../api";
import { useDataset } from "./Dataset";

const COLUMNS = ["Sources", "Tables", "Measures", "Dashboard visuals"];

type Line = { from: string; to: string; kind: string; d: string };

/** Where every number comes from: click anything to light up what feeds it and what it feeds. */
export function Lineage() {
  const { project } = useDataset();
  const [graph, setGraph] = useState<LineageData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const board = useRef<HTMLDivElement>(null);
  const cards = useRef(new Map<string, HTMLElement>());

  useEffect(() => {
    api.lineage(project.id).then(setGraph).catch((e) => setError(e.message));
  }, [project.id]);

  // Everything upstream and downstream of the focused item.
  const lit = useMemo(() => {
    if (!graph || !focus) return null;
    const up = new Map<string, string[]>();
    const down = new Map<string, string[]>();
    for (const e of graph.edges) {
      up.set(e.to, [...(up.get(e.to) ?? []), e.from]);
      down.set(e.from, [...(down.get(e.from) ?? []), e.to]);
    }
    const seen = new Set([focus]);
    const walk = (start: string, links: Map<string, string[]>) => {
      const queue = [start];
      while (queue.length) {
        for (const next of links.get(queue.shift()!) ?? []) {
          if (!seen.has(next)) {
            seen.add(next);
            queue.push(next);
          }
        }
      }
    };
    walk(focus, up);
    walk(focus, down);
    return seen;
  }, [graph, focus]);

  // Lines are drawn from the cards' positions once they are laid out (and again on resize).
  useLayoutEffect(() => {
    if (!graph || !board.current) return;
    const draw = () => {
      const box = board.current!.getBoundingClientRect();
      const next: Line[] = [];
      for (const e of graph.edges) {
        const a = cards.current.get(e.from)?.getBoundingClientRect();
        const b = cards.current.get(e.to)?.getBoundingClientRect();
        if (!a || !b) continue;
        const x1 = a.right - box.left;
        const y1 = a.top + a.height / 2 - box.top;
        const x2 = b.left - box.left;
        const y2 = b.top + b.height / 2 - box.top;
        const bend = Math.max(24, (x2 - x1) / 2);
        next.push({ ...e, d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}` });
      }
      setLines(next);
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(board.current);
    return () => observer.disconnect();
  }, [graph]);

  if (!graph) return <p className={error ? "bad" : "muted"}>{error ?? "Loading…"}</p>;
  if (graph.nodes.length === 0) return <p className="muted">Load some data to see its lineage.</p>;

  const byColumn = COLUMNS.map((_, i) => graph.nodes.filter((n) => n.column === i));
  const dim = (id: string) => lit !== null && !lit.has(id);

  return (
    <div className="lineage">
      <header className="page-header">
        <div className="page-header-text">
          <h1>Lineage</h1>
          <p className="muted">
            From the data you loaded to every number on a dashboard. Click an item to see what feeds it and what it feeds;
            click again to clear.
          </p>
        </div>
      </header>
      <div className="lineage-board" ref={board}>
        <svg className="lineage-lines" aria-hidden="true">
          {lines.map((l, i) => (
            <path
              key={i}
              d={l.d}
              className={`lineage-line kind-${l.kind} ${lit && (!lit.has(l.from) || !lit.has(l.to)) ? "dim" : ""} ${lit && lit.has(l.from) && lit.has(l.to) ? "lit" : ""}`}
            />
          ))}
        </svg>
        {byColumn.map((nodes, i) => (
          <div key={COLUMNS[i]} className="lineage-column">
            <h3 className="section-title">{COLUMNS[i]}</h3>
            {nodes.length === 0 && <p className="muted small">None yet.</p>}
            {nodes.map((node) => (
              <Card
                key={node.id}
                node={node}
                dim={dim(node.id)}
                focused={focus === node.id}
                onClick={() => setFocus(focus === node.id ? null : node.id)}
                register={(el) => (el ? cards.current.set(node.id, el) : cards.current.delete(node.id))}
              />
            ))}
          </div>
        ))}
      </div>
      <p className="muted small lineage-key">
        <span className="key solid" /> uses <span className="key dashed" /> split by, lookup or merge
      </p>
    </div>
  );
}

function Card(props: { node: LineageNode; dim: boolean; focused: boolean; onClick: () => void; register: (el: HTMLElement | null) => void }) {
  const { node } = props;
  return (
    <button
      ref={props.register}
      className={`lineage-card kind-${node.kind} ${props.dim ? "dim" : ""} ${props.focused ? "focused" : ""}`}
      onClick={props.onClick}
      aria-pressed={props.focused}
    >
      <span className="lineage-label">{node.label}</span>
      <span className="lineage-sub">{node.sub}</span>
    </button>
  );
}
