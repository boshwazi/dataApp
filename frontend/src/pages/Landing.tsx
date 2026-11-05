import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Check, Eye, Play, RotateCcw, ShieldCheck, Undo2 } from "lucide-react";
import { BrandMark } from "../components/ui";
import "./landing.css";

/**
 * The page for visitors who aren't signed in (at /, and at /welcome for anyone).
 * Few words, four things to try: the hero turns a raw file into an insight, the steps swap the real product as you
 * scroll, Autopilot runs, and Ask answers. Every number is real: samples/sales.csv after cleaning has 241 orders and
 * RM 153,948.70 of revenue (the app's own result), split by region, product and month exactly as shown below.
 * Each demo plays once by itself; with reduced motion it plays as plain fades, with nothing sliding or growing.
 */

/** Calls `onEnter` once, the first time the element is at least `amount` visible. */
function useOnceVisible<T extends HTMLElement>(onEnter: () => void, amount = 0.4) {
  const ref = useRef<T>(null);
  const latest = useRef(onEnter);
  latest.current = onEnter;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          latest.current();
          io.disconnect();
        }
      },
      { threshold: amount },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [amount]);
  return ref;
}

/* ---------------------------------------------------------------------------------------------------------------
   Hero: raw → clean → insight, on the real file
   --------------------------------------------------------------------------------------------------------------- */

type Cell = string | { raw: string; clean: string };
const ROWS: { id: string; date: Cell; price: Cell; region: Cell; repeat?: boolean }[] = [
  { id: "SO-10001", date: { raw: "26/01/2024", clean: "2024-01-26" }, price: "18.50", region: "Central" },
  { id: "SO-10005", date: "2024-04-14", price: { raw: "RM 79.90", clean: "79.90" }, region: "East Malaysia" },
  { id: "SO-10011", date: { raw: "04 Aug 2024", clean: "2024-08-04" }, price: "18.50", region: "East Coast" },
  { id: "SO-10012", date: "2024-05-26", price: { raw: "RM1,250.00", clean: "1,250.00" }, region: "East Malaysia" },
  { id: "SO-10017", date: "2024-06-05", price: { raw: "RM 12.90", clean: "12.90" }, region: { raw: "", clean: "Unknown" } },
  { id: "SO-10017", date: "2024-06-05", price: "RM 12.90", region: "", repeat: true },
  { id: "SO-10030", date: "2024-06-07", price: "1,250.00", region: { raw: "N/A", clean: "Unknown" } },
];

const FIXES = ["Dates read day first", "RM taken out of prices", "Blank regions marked Unknown", "3 repeated orders removed"];

const REGIONS: [string, number][] = [
  ["Central", 46381.8],
  ["Southern", 36160.9],
  ["East Coast", 25782.9],
  ["Unknown", 16028.0],
  ["East Malaysia", 15382.6],
  ["Northern", 14212.5],
];

const STAGES = ["Raw", "Clean", "Insight"] as const;

function value(cell: Cell, clean: boolean) {
  if (typeof cell === "string") return <span>{cell}</span>;
  return (
    <span className={`lp-swap${clean ? " clean" : ""}`}>
      <span className="was">{cell.raw || "(blank)"}</span>
      <span className="now">{cell.clean}</span>
    </span>
  );
}

/** Raw, then Clean, then Insight (held a little longer), then round again. */
const LOOP = { clean: 1600, insight: 4400, again: 9400 };

/**
 * Loops while it's on screen (paused while scrolled away, starting again from Raw when it's back). Picking a stage
 * stops the loop on that view, so nothing changes under someone who's reading; the restart button starts it again.
 */
function HeroDemo() {
  const [stage, setStage] = useState(0);
  const timers = useRef<number[]>([]);
  const looping = useRef(true);
  const box = useRef<HTMLElement>(null);

  const clear = () => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  };
  const cycle = () => {
    clear();
    setStage(0);
    timers.current = [
      window.setTimeout(() => setStage(1), LOOP.clean),
      window.setTimeout(() => setStage(2), LOOP.insight),
      window.setTimeout(cycle, LOOP.again),
    ];
  };
  const restart = () => {
    looping.current = true;
    cycle();
  };
  const pick = (i: number) => {
    looping.current = false;
    clear();
    setStage(i);
  };

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    // Start at once (the hero is on screen at load); the observer only pauses it off screen and resumes it.
    cycle();
    const io = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) clear();
      else if (looping.current && timers.current.length === 0) cycle();
    });
    io.observe(el);
    return () => {
      io.disconnect();
      clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clean = stage >= 1;
  const most = REGIONS[0][1];
  return (
    <figure ref={box} className={`lp-demo stage-${stage}`} aria-label="A raw sales file being cleaned and turned into revenue by region.">
      <div className="lp-demo-bar">
        <span className="lp-demo-file">sales.csv</span>
        <div className="lp-demo-stages" role="tablist" aria-label="Stage">
          {STAGES.map((s, i) => (
            <button
              key={s}
              type="button"
              role="tab"
              aria-selected={stage === i}
              onClick={() => pick(i)}
            >
              {s}
            </button>
          ))}
        </div>
        <button type="button" className="lp-demo-replay" onClick={restart} aria-label="Start again from Raw" title="Start again from Raw">
          <RotateCcw size={14} aria-hidden="true" />
        </button>
      </div>

      <div className="lp-demo-body">
        <div className="lp-demo-table" aria-hidden={stage === 2}>
          <table>
            <thead>
              <tr><th>Order</th><th>Date</th><th>Price</th><th>Region</th></tr>
            </thead>
            <tbody>
              {ROWS.map((r, i) => (
                <tr key={i} className={r.repeat ? `repeat${clean ? " gone" : ""}` : ""}>
                  <td>{r.id}</td>
                  <td className={typeof r.date === "string" ? "" : "fix"}>{value(r.date, clean)}</td>
                  <td className={typeof r.price === "string" || r.repeat ? "" : "fix"}>{value(r.price, clean)}</td>
                  <td className={typeof r.region === "string" && !r.repeat ? "" : "fix"}>{r.repeat ? <span className="was-blank">(blank)</span> : value(r.region, clean)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="lp-demo-status">
            {clean ? (
              <>
                <span className="lp-ai">AI</span>
                <span>{FIXES.length} fixes waiting for you</span>
                <span className="lp-demo-fixes">{FIXES.join(" · ")}</span>
              </>
            ) : (
              <>
                <span className="lp-issue">7 issues</span>
                <span>244 rows, 3 date formats, prices stored as text</span>
              </>
            )}
          </div>
        </div>

        <div className="lp-demo-insight" aria-hidden={stage !== 2}>
          <div className="lp-demo-kpi">
            <small>Revenue, 241 orders</small>
            <strong>RM 153,948.70</strong>
            <span><Check size={13} strokeWidth={2.5} aria-hidden="true" /> Checked against the file</span>
          </div>
          <ol className="lp-bars">
            {REGIONS.map(([name, v], i) => (
              <li key={name} style={{ "--w": (v / most).toFixed(3), "--i": i } as CSSProperties}>
                <span className="name">{name}</span>
                <span className="lp-bar"><i /></span>
                <span className="val">{(v / 1000).toFixed(1)}K</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </figure>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
   How it works: a pinned window that follows the step you're reading
   --------------------------------------------------------------------------------------------------------------- */

const STEPS = [
  { id: "get", title: "Bring it as it is.", text: "CSV, Excel, a shared folder or a Power BI file. No tidying first.", image: "/landing/getdata.png", alt: "The Get Data screen profiling three tables." },
  { id: "clean", title: "Watch it get cleaned.", text: "Every fix is listed with its reason. Approve it, or undo it.", image: "/landing/clean.png", alt: "The Transform screen with eight applied steps and one AI step waiting for review." },
  { id: "model", title: "Tables, joined for you.", text: "Relationships, a date table and measures, each formula shown.", image: "/landing/model.png", alt: "The Model screen with four related tables and measures waiting for review." },
  { id: "report", title: "A dashboard that explains itself.", text: "Each card says what changed and why, checked against the numbers.", image: "/landing/report.png", alt: "A sales dashboard with KPI cards, sparklines and a monthly revenue chart." },
];

function Steps() {
  const [active, setActive] = useState(0);
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const items = [...(list.current?.querySelectorAll<HTMLElement>("li") ?? [])];
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.i));
      },
      { rootMargin: "-45% 0px -45% 0px" },
    );
    items.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
  return (
    <section id="how" className="lp-steps-section">
      <div className="lp-wrap lp-steps-grid">
        <div className="lp-steps-sticky">
          <div className="lp-window">
            <div className="lp-chrome" aria-hidden="true"><i /><i /><i /><span>datawazi · Sample sales review</span></div>
            <div className="lp-shots">
              {STEPS.map((s, i) => (
                <img key={s.id} src={s.image} alt={i === active ? s.alt : ""} aria-hidden={i !== active} className={i === active ? "on" : ""} width={1440} height={900} loading={i === 0 ? "eager" : "lazy"} decoding="async" />
              ))}
            </div>
          </div>
          <div className="lp-steps-progress" aria-hidden="true">
            {STEPS.map((s, i) => <i key={s.id} className={i <= active ? "on" : ""} />)}
          </div>
        </div>
        <ol className="lp-steps-list" ref={list}>
          {STEPS.map((s, i) => (
            <li key={s.id} data-i={i} className={i === active ? "on" : ""}>
              <span className="n">{i + 1}</span>
              <h3>{s.title}</h3>
              <p>{s.text}</p>
              <img src={s.image} alt={s.alt} className="lp-steps-inline" width={1440} height={900} loading="lazy" decoding="async" />
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
   Autopilot: a run that plays when it scrolls into view (and again on demand)
   --------------------------------------------------------------------------------------------------------------- */

const RUN: { at: number; ai?: boolean; text: string; note: string }[] = [
  { at: 2, text: "Read sales, customers, products", note: "293 rows" },
  { at: 19, text: "Dates read day first", note: "3 formats" },
  { at: 31, text: "RM taken out of Unit Price", note: "decimal" },
  { at: 44, ai: true, text: "Remove 3 repeated orders", note: "for review" },
  { at: 52, ai: true, text: "Mark 16 blank regions Unknown", note: "for review" },
  { at: 72, text: "Relate sales to customers, products", note: "100% match" },
  { at: 100, text: "Revenue over 241 orders", note: "RM 153,948.70" },
  { at: 158, text: "Dashboard, 3 pages", note: "totals add up" },
];
const RUN_SECONDS = 161;
const RUN_MS = 6000;
const clock = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function Autopilot() {
  const [run, setRun] = useState(0); // bumps to restart the CSS
  const [done, setDone] = useState(false);
  const time = useRef<HTMLSpanElement>(null);
  const frame = useRef(0);

  const start = () => {
    cancelAnimationFrame(frame.current);
    setDone(false);
    setRun((n) => n + 1);
    const t0 = performance.now();
    const tick = (now: number) => {
      const k = Math.min(1, (now - t0) / RUN_MS);
      if (time.current) time.current.textContent = clock(k * RUN_SECONDS);
      if (k < 1) frame.current = requestAnimationFrame(tick);
      else setDone(true);
    };
    frame.current = requestAnimationFrame(tick);
  };
  useEffect(() => () => cancelAnimationFrame(frame.current), []);
  const box = useOnceVisible<HTMLDivElement>(start, 0.45);

  return (
    <section id="autopilot" className="lp-auto-section">
      <div className="lp-wrap lp-auto-grid">
        <div>
          <h2>Upload a file. Get a checked dashboard in under three minutes.</h2>
          <p className="lp-sub">Autopilot does every step, then stops so you can review it before anyone sees it.</p>
          <button type="button" className="lp-btn lp-btn-primary lp-btn-lg" onClick={start}>
            <Play size={15} aria-hidden="true" /> {run > 1 || done ? "Run it again" : "Run Autopilot"}
          </button>
          <p className="lp-fine">A real run on our messy sample files. Times depend on your data.</p>
        </div>
        <div ref={box} className={`lp-run${run ? " running" : ""}${done ? " done" : ""}`} key={run} style={{ "--run": `${RUN_MS}ms` } as CSSProperties}>
          <div className="lp-run-top">
            <span>Autopilot · sales.csv</span>
            <span ref={time} className="lp-run-clock">{done ? clock(RUN_SECONDS) : "00:00"}</span>
          </div>
          <div className="lp-run-progress" aria-hidden="true"><i /></div>
          <ol>
            {RUN.map((r) => (
              <li key={r.text} style={{ "--d": `${(r.at / RUN_SECONDS) * RUN_MS}ms` } as CSSProperties}>
                <span className={r.ai ? "mark ai" : "mark"}>{r.ai ? "✦" : "✓"}</span>
                <span>{r.text}</span>
                <span className="note">{r.note}</span>
              </li>
            ))}
          </ol>
          <div className="lp-run-stop">
            <span><b>Stopped for your review.</b> 8 changes, nothing published.</span>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
   Ask: three questions, answered from the same data
   --------------------------------------------------------------------------------------------------------------- */

const QUESTIONS: { q: string; answer: ReactNode; bars: [string, number][]; how: string[] }[] = [
  {
    q: "Which region sold the most?",
    answer: <><b>Central</b>, with RM 46,381.80. That's 30% of revenue.</>,
    bars: REGIONS.slice(0, 4),
    how: ["Revenue = SUM(Qty × Unit Price)", "Split by Region", "241 orders in sales"],
  },
  {
    q: "What's our best-selling product?",
    answer: <><b>Wok 36cm</b>, at RM 108,750. About 71% of all revenue.</>,
    bars: [["Wok 36cm", 108750], ["Rice Cooker", 14553], ["Electric Kettle", 11745.3]],
    how: ["Revenue = SUM(Qty × Unit Price)", "Split by product name, from products", "Related on Product Code"],
  },
  {
    q: "Bulan mana jualan paling tinggi?",
    answer: <><b>Mei 2024</b>, dengan RM 19,985.40.</>,
    bars: [["Mei 2024", 19985.4], ["Feb 2024", 19536.5], ["Sep 2024", 17312.2]],
    how: ["Hasil = SUM(Qty × Unit Price)", "Dipecahkan mengikut bulan", "Jadual tarikh dibina oleh AI"],
  },
];

function Ask() {
  const [current, setCurrent] = useState(0);
  const shown = QUESTIONS[current];
  const most = shown.bars[0][1];
  return (
    <section id="ask" className="lp-ask-section">
      <div className="lp-wrap lp-ask-grid">
        <div>
          <h2>Ask in plain words. In English or BM.</h2>
          <div className="lp-ask-chips" role="tablist" aria-label="Example questions">
            {QUESTIONS.map((x, i) => (
              <button key={x.q} type="button" role="tab" aria-selected={i === current} onClick={() => setCurrent(i)}>
                {x.q}
              </button>
            ))}
          </div>
        </div>
        <div className="lp-ask-panel" role="tabpanel" aria-live="polite">
          <div className="lp-ask-q">{shown.q}</div>
          <div className="lp-ask-a" key={current}>
            <p>{shown.answer}</p>
            <ol className="lp-bars small">
              {shown.bars.map(([name, v], i) => (
                <li key={name} style={{ "--w": (v / most).toFixed(3), "--i": i } as CSSProperties}>
                  <span className="name">{name}</span>
                  <span className="lp-bar"><i /></span>
                  <span className="val">{(v / 1000).toFixed(1)}K</span>
                </li>
              ))}
            </ol>
            <details className="lp-how">
              <summary>How this was calculated</summary>
              <ul>{shown.how.map((h) => <li key={h}>{h}</li>)}</ul>
            </details>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
   Trust, Power BI, pricing, questions
   --------------------------------------------------------------------------------------------------------------- */

const TRUST = [
  { icon: Undo2, title: "You approve every change", text: "Nothing goes live until you say so. Undo anything." },
  { icon: ShieldCheck, title: "Every number is checked", text: "Answers are matched to the data. Mismatches are flagged." },
  { icon: Eye, title: "The AI sees summaries", text: "Column profiles and a few sample rows. Never the whole table." },
];

const CURRENCIES = { USD: "US$", MYR: "RM" } as const;
type Currency = keyof typeof CURRENCIES;

function defaultCurrency(): Currency {
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language];
  return languages.some((l) => /-MY$/i.test(l) || /^ms\b/i.test(l)) ? "MYR" : "USD";
}

const PLANS = [
  { name: "Free", free: true, rows: ["1 creator, 3 viewers", "2 datasets", "Manual refresh", "Monthly AI credits"] },
  { name: "Pro", free: false, rows: ["10 viewers per creator", "20 datasets", "Daily refresh", "Share links and PDF"] },
  { name: "Team", free: false, rows: ["Unlimited viewers", "Unlimited datasets", "Hourly refresh", "Row-level security, audit log"] },
];

function Pricing() {
  const [currency, setCurrency] = useState<Currency>(defaultCurrency);
  return (
    <section id="pricing" className="lp-pricing">
      <div className="lp-wrap">
        <div className="lp-pricing-head">
          <h2>Start free.</h2>
          <div className="lp-seg" role="group" aria-label="Currency">
            {(Object.keys(CURRENCIES) as Currency[]).map((c) => (
              <button key={c} type="button" aria-pressed={currency === c} onClick={() => setCurrency(c)}>
                {c === "USD" ? "US$" : "RM"}
              </button>
            ))}
          </div>
        </div>
        <div className="lp-plans">
          {PLANS.map((plan) => (
            <div key={plan.name} className={`lp-plan${plan.free ? " free" : ""}`}>
              <h3>{plan.name}</h3>
              <p className="lp-price">{plan.free ? `${CURRENCIES[currency]}${currency === "MYR" ? " " : ""}0` : "At launch"}</p>
              <ul>
                {plan.rows.map((row) => (
                  <li key={row}><Check size={14} aria-hidden="true" /> {row}</li>
                ))}
              </ul>
              {plan.free && <Link to="/sign-in" className="lp-btn lp-btn-primary">Start free</Link>}
            </div>
          ))}
        </div>
        <p className="lp-fine">Paid plans open at launch, priced from what the AI really costs to run.</p>
      </div>
    </section>
  );
}

const FAQ = [
  ["What if the AI gets something wrong?", "Every step waits for your approval and can be undone. Numbers the AI writes are checked against the data, and anything that doesn't match is flagged."],
  ["What does the AI see of my data?", "Column summaries, up to 5 sample rows per table and the results of the queries it runs. Your full data stays in your workspace."],
  ["Do I need Power BI?", "No. datawazi does the whole job in the browser. If you already have Power BI files, import a .pbix or .pbip and carry on here."],
  ["How do you handle privacy law?", "We're completing our duties under Malaysia's PDPA (privacy notice, data protection officer and breach procedure) before public launch."],
];

export function Landing() {
  return (
    <div className="landing">
      <a className="skip-link" href="#lp-main">Skip to content</a>
      <header className="lp-header">
        <div className="lp-wrap lp-header-row">
          <Link to="/welcome" className="lp-brand" aria-label="datawazi home">
            <BrandMark size={26} />
            <span>datawazi</span>
          </Link>
          <nav aria-label="Main" className="lp-nav">
            <a href="#how">How it works</a>
            <a href="#ask">Ask</a>
            <a href="#powerbi">Power&nbsp;BI</a>
            <a href="#pricing">Pricing</a>
          </nav>
          <div className="lp-header-actions">
            <Link to="/sign-in" className="lp-link">Sign in</Link>
            <Link to="/sign-in" className="lp-btn lp-btn-light">Start free</Link>
          </div>
        </div>
      </header>

      <main id="lp-main">
        <section className="lp-hero">
          <div className="lp-wrap lp-hero-grid">
            <div className="lp-hero-copy">
              <h1>From raw data to <em>real insights</em>.</h1>
              <p className="lp-lede">Drop in a messy spreadsheet. The AI cleans, models and charts it, and shows you every change.</p>
              <div className="lp-ctas">
                <Link to="/sign-in" className="lp-btn lp-btn-light lp-btn-lg">Start free</Link>
                <a href="#how" className="lp-btn lp-btn-ghost lp-btn-lg">See how it works</a>
              </div>
            </div>
            <HeroDemo />
          </div>
        </section>

        <div className="lp-sources">
          <div className="lp-wrap lp-sources-row">
            <span>Works with</span>
            <ul>
              <li>CSV</li><li>Excel</li><li>OneDrive</li><li>SharePoint</li><li>Google Drive</li><li>Power&nbsp;BI</li>
            </ul>
          </div>
        </div>

        <Steps />
        <Autopilot />
        <Ask />

        <section className="lp-trust">
          <div className="lp-wrap">
            <h2>Numbers you can stand behind.</h2>
            <ul className="lp-trust-grid">
              {TRUST.map(({ icon: Icon, title, text }) => (
                <li key={title}>
                  <Icon size={20} aria-hidden="true" />
                  <h3>{title}</h3>
                  <p>{text}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="powerbi" className="lp-pbi">
          <div className="lp-wrap lp-pbi-row">
            <div className="lp-pbi-file" aria-hidden="true">
              <span className="ext">.pbix</span>
              <span className="arrow" />
              <BrandMark size={34} />
            </div>
            <div>
              <h2>Already on Power&nbsp;BI? Bring it with you.</h2>
              <p className="lp-sub">Tables, relationships and measures come across. Then ask it anything.</p>
            </div>
          </div>
        </section>

        <Pricing />

        <section className="lp-faq-section">
          <div className="lp-wrap lp-faq-grid">
            <h2>Questions</h2>
            <div className="lp-faq">
              {FAQ.map(([q, a]) => (
                <details key={q}>
                  <summary>{q}</summary>
                  <p>{a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-final">
          <div className="lp-wrap lp-final-row">
            <h2>Your next report, checked and ready by lunch.</h2>
            <Link to="/sign-in" className="lp-btn lp-btn-light lp-btn-lg">Start free</Link>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-wrap lp-footer-row">
          <div className="lp-brand">
            <BrandMark size={20} />
            <span>datawazi</span>
          </div>
          <span>English and Bahasa Malaysia</span>
          <span>Privacy notice and terms are published before launch.</span>
          <Link to="/sign-in" className="lp-link">Sign in</Link>
        </div>
      </footer>
    </div>
  );
}
