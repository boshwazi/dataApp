import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Check, Pause, Play, Sparkles, UploadCloud } from "lucide-react";

/**
 * Autopilot in action, as a short muted loop drawn in the page (sharper and far lighter than a video file).
 * Four scenes of 4 seconds: the file arrives, the AI cleans it, the tables are related, the dashboard is built and
 * checked. A clock and a timeline run underneath, compressing the real 3 min 41 s run on the sample files.
 * It pauses when scrolled away and with the pause button. With reduced motion it still plays, as fades (see CSS).
 */

const SCENES = ["Get data", "Clean", "Model", "Report"] as const;
const SCENE_MS = 4000;
const RUN_SECONDS = 221; // 03:41, the sample run

const k = (n: number) => ({ "--k": n }) as CSSProperties;

function Scene({ index }: { index: number }) {
  if (index === 0)
    return (
      <div className="apl-scene apl-get">
        <div className="apl-drop">
          <UploadCloud size={22} aria-hidden="true" />
          <span>Drop files here</span>
        </div>
        <div className="apl-file" style={k(0)}><i className="csv">CSV</i><span>sales.csv</span><small>244 rows</small><b className="apl-bar" /></div>
        <div className="apl-file" style={k(1)}><i className="xls">XLS</i><span>customers.xlsx</span><small>40 rows</small><b className="apl-bar" /></div>
      </div>
    );
  if (index === 1)
    return (
      <div className="apl-scene apl-clean">
        <div className="apl-grid">
          {[["26/01/2024", "2024-01-26"], ["RM 79.90", "79.90"], ["(blank)", "Unknown"], ["04 Aug 2024", "2024-08-04"]].map(([from, to], i) => (
            <div key={from} className="apl-cell" style={k(i)}><s>{from}</s><span>{to}</span></div>
          ))}
        </div>
        <div className="apl-steps">
          <p className="apl-ai"><Sparkles size={13} aria-hidden="true" /> AI cleaning</p>
          {["Dates read day first", "RM taken out of prices", "Blanks marked Unknown", "3 repeated rows removed"].map((s, i) => (
            <p key={s} className="apl-step" style={k(i)}><Check size={12} strokeWidth={3} aria-hidden="true" /> {s}</p>
          ))}
        </div>
      </div>
    );
  if (index === 2)
    return (
      <div className="apl-scene apl-model">
        <svg viewBox="0 0 340 170" aria-hidden="true">
          <path className="apl-link" style={k(0)} d="M104 46 C150 46 150 85 196 85" />
          <path className="apl-link" style={k(1)} d="M104 124 C150 124 150 85 196 85" />
          <path className="apl-link" style={k(2)} d="M276 85 L290 85" />
          <g className="apl-table" style={k(0)}><rect x="6" y="26" width="98" height="40" rx="8" /><text x="55" y="50">Customers</text></g>
          <g className="apl-table" style={k(1)}><rect x="6" y="104" width="98" height="40" rx="8" /><text x="55" y="128">Products</text></g>
          <g className="apl-table fact" style={k(0)}><rect x="196" y="62" width="80" height="46" rx="8" /><text x="236" y="89">Sales</text></g>
          <g className="apl-table" style={k(2)}><rect x="290" y="65" width="48" height="40" rx="8" /><text x="314" y="89">Date</text></g>
        </svg>
        <p className="apl-note" style={k(3)}>4 tables, 3 relationships, 11 measures</p>
      </div>
    );
  return (
    <div className="apl-scene apl-report">
      <div className="apl-kpis">
        <div className="apl-kpi" style={k(0)}><small>Revenue</small><strong>RM 153.9K</strong></div>
        <div className="apl-kpi" style={k(1)}><small>Orders</small><strong>241</strong></div>
      </div>
      <div className="apl-bars">
        {[["Central", 46.4], ["Southern", 36.2], ["East Coast", 25.8], ["Unknown", 16.0], ["East Malaysia", 15.4]].map(([name, v], i) => (
          <div key={name} className="apl-bar-row" style={k(i)}>
            <span>{name}</span>
            <b style={{ width: `${(Number(v) / 46.4) * 100}%` }} className={name === "Unknown" ? "other" : ""} />
          </div>
        ))}
      </div>
      <p className="apl-checked" style={k(5)}><Check size={13} strokeWidth={3} aria-hidden="true" /> Every number checked against the data</p>
    </div>
  );
}

export function AutopilotLoop() {
  const [scene, setScene] = useState(0);
  const [leaving, setLeaving] = useState<number | null>(null);
  const current = useRef(0);
  const [playing, setPlaying] = useState(true);
  const [visible, setVisible] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const clock = useRef<HTMLSpanElement>(null);
  const fill = useRef<HTMLDivElement>(null);
  const started = useRef(performance.now() - scene * SCENE_MS);
  const pausedAt = useRef<number | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { threshold: 0.25 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // One clock drives the scene, the timeline and the run time; paused or off screen it holds where it is.
  useEffect(() => {
    if (!playing || !visible) return;
    const loop = SCENES.length * SCENE_MS;
    let held = performance.now();
    started.current += held - (pausedAt.current ?? held);
    pausedAt.current = null;
    let frame = 0;
    const tick = (now: number) => {
      held = now;
      const t = (now - started.current) % loop;
      const index = Math.floor(t / SCENE_MS);
      if (index !== current.current) {
        setLeaving(current.current);
        current.current = index;
        setScene(index);
      }
      const seconds = Math.round((t / loop) * RUN_SECONDS);
      if (clock.current) clock.current.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
      if (fill.current) fill.current.style.transform = `scaleX(${t / loop})`;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      pausedAt.current = held;
    };
  }, [playing, visible]);

  return (
    <figure className="apl-player" ref={box} aria-label="Autopilot on the sample files: sales.csv and customers.xlsx are uploaded, the AI fixes dates, prices, blanks and repeated rows, relates the tables and adds 11 measures, then builds a dashboard of revenue by region with every number checked. 3 minutes 41 seconds in all.">
      <div className="apl-head">
        <span className="apl-badge"><Sparkles size={13} aria-hidden="true" /> Autopilot</span>
        <span className="apl-title">Sales 2024</span>
        <button type="button" className="apl-toggle" onClick={() => setPlaying(!playing)} aria-label={playing ? "Pause the animation" : "Play the animation"}>
          {playing ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
        </button>
      </div>
      <div className="apl-screen" aria-hidden="true">
        {/* the outgoing scene keeps its element (so it doesn't replay) and fades out over the incoming one */}
        {(leaving === null || leaving === scene ? [scene] : [leaving, scene]).map((i) => (
          <div
            key={i}
            className={i === scene ? "apl-layer" : "apl-layer leaving"}
            onAnimationEnd={(e) => e.target === e.currentTarget && i !== scene && setLeaving(null)}
          >
            <Scene index={i} />
          </div>
        ))}
      </div>
      <div className="apl-foot" aria-hidden="true">
        <ol className="apl-stages">
          {SCENES.map((name, i) => (
            <li key={name} className={i === scene ? "on" : i < scene ? "done" : ""}>{name}</li>
          ))}
        </ol>
        <span className="apl-clock" ref={clock}>00:00</span>
        <div className="apl-track"><div className="apl-fill" ref={fill} /></div>
      </div>
    </figure>
  );
}
