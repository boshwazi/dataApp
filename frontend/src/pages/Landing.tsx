import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { Check, Eye, FileDown, FileUp, Lock, ShieldCheck, Undo2 } from "lucide-react";
import { BrandMark } from "../components/ui";
import "./landing.css";

/**
 * The page for visitors who aren't signed in (at /, and at /welcome for anyone). Dark ("Night") whatever the app's
 * theme: one light falls on the product, and the product does the talking.
 * The hero replays a real cleaning of samples/sales.csv: the rows, the fixes and the totals are what datawazi
 * produced on that file (153,948.70 over 241 orders, checked against the file in the Gate 1 dry run).
 * The product tour shows screenshots of the app on the same data (public/landing/*.webp).
 */

type Fix = { from: string; to: string; at: number };
type Cell = string | Fix;

const ROWS: { id: string; date: Cell; qty: number; price: Cell; region: Cell }[] = [
  { id: "SO-10001", date: { from: "26/01/2024", to: "2024-01-26", at: 0.6 }, qty: 2, price: "18.50", region: "Central" },
  { id: "SO-10005", date: "2024-04-14", qty: 3, price: { from: "RM 79.90", to: "79.90", at: 1.3 }, region: "East Malaysia" },
  { id: "SO-10011", date: { from: "04 Aug 2024", to: "2024-08-04", at: 0.8 }, qty: 3, price: "18.50", region: "East Coast" },
  { id: "SO-10012", date: "2024-05-26", qty: 2, price: { from: "RM1,250.00", to: "1,250.00", at: 1.5 }, region: "East Malaysia" },
  {
    id: "SO-10017", date: "2024-06-05", qty: 1, price: { from: "RM 12.90", to: "12.90", at: 1.7 },
    region: { from: "(blank)", to: "Unknown", at: 2.2 },
  },
  { id: "SO-10030", date: "2024-06-07", qty: 1, price: "1,250.00", region: { from: "N/A", to: "Unknown", at: 2.4 } },
];

const STEPS: [string, number][] = [
  ["Dates read day first", 1.0],
  ["RM taken out of prices", 1.9],
  ["Blank regions marked Unknown", 2.7],
  ["3 repeated rows removed", 3.1],
];

function delay(seconds: number): CSSProperties {
  return { "--d": `${seconds}s` } as CSSProperties;
}

function Value({ cell }: { cell: Cell }) {
  if (typeof cell === "string") return <>{cell}</>;
  return (
    <span className="lp-fix" style={delay(cell.at)}>
      <span className="lp-old">{cell.from}</span>
      <span className="lp-new">{cell.to}</span>
    </span>
  );
}

/** The product window: messy cells fix themselves while the AI's steps tick in beside them, waiting for approval. */
function CleaningDemo() {
  const [run, setRun] = useState(0);
  return (
    <figure
      className="lp-window"
      aria-label="Demo: datawazi cleans a messy sales file. Dates are read day first, RM is taken out of prices, blank regions are marked Unknown and 3 repeated rows are removed, leaving 241 orders and RM 153,948.70 of revenue, checked against the file."
    >
      <div className="lp-chrome">
        <i /><i /><i />
        <span>Sales 2024 / Transform</span>
        <button type="button" className="lp-replay" onClick={() => setRun((n) => n + 1)}>Play again</button>
      </div>
      <div className="lp-app" key={run} aria-hidden="true">
        <div className="lp-side">
          <small>Sales 2024</small>
          <div>Get data</div>
          <div className="on">Transform</div>
          <div>Model</div>
          <div>Report</div>
          <small>Workspace</small>
          <div>Dashboards</div>
          <div>Members</div>
        </div>
        <div className="lp-main">
          <h3>sales.csv <span>244 rows, 7 columns</span></h3>
          <div className="lp-table-scroll">
            <table className="lp-table">
              <thead>
                <tr><th>Order</th><th>Date</th><th>Qty</th><th>Price</th><th>Region</th></tr>
              </thead>
              <tbody>
                {ROWS.map((r) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td><Value cell={r.date} /></td>
                    <td>{r.qty}</td>
                    <td><Value cell={r.price} /></td>
                    <td><Value cell={r.region} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="lp-totals"><span>After cleaning</span><b>241 orders</b><b>RM 153,948.70</b><span className="ok">Checked against the file</span></p>
        </div>
        <div className="lp-ai">
          <div className="lp-ai-head"><span className="lp-pulse" />AI suggestions</div>
          {STEPS.map(([text, at]) => (
            <div key={text} className="lp-ai-step" style={delay(at)}>
              <Check size={14} strokeWidth={3} /> {text}
            </div>
          ))}
          <div className="lp-approve"><span>Approve all</span><span>Review</span></div>
        </div>
      </div>
    </figure>
  );
}

const FEATURES = [
  {
    title: "Clean", wide: true,
    text: "Dates, prices, blanks and repeats fixed, each step listed with the AI's reason. Undo any of them.",
    visual: (
      <div className="lp-v-clean">
        <div><s>26/01/2024</s><span>2024-01-26</span></div>
        <div><s>RM1,250.00</s><span>1,250.00</span></div>
        <div><s>N/A</s><span>Unknown</span></div>
      </div>
    ),
  },
  {
    title: "Model", wide: true,
    text: "Tables related, a date table added and measures written, with every formula shown.",
    visual: <ModelDiagram />,
  },
  {
    title: "Report",
    text: "Cards show the change, what drove it, and a summary checked against the numbers.",
    visual: (
      <div className="lp-v-kpi">
        <small>Revenue by region, top</small>
        <strong>RM 46.4K</strong>
        <span className="chg">Central, about 30% of revenue</span>
        <svg viewBox="0 0 280 44" preserveAspectRatio="none" aria-hidden="true"><path d="M2 36 L28 30 L54 33 L80 24 L106 27 L132 18 L158 22 L184 14 L210 17 L236 9 L262 12 L278 4" /></svg>
      </div>
    ),
  },
  {
    title: "Ask",
    text: "Ask in plain English or Bahasa Malaysia and get a chart back.",
    visual: (
      <div className="lp-v-ask">
        <div className="q">Which region sold the most?</div>
        <div className="a"><b>Central</b>, at RM 46.4K, about 30% of revenue.</div>
      </div>
    ),
  },
  {
    title: "Power BI",
    text: "Import a .pbix, or export a Power BI project that opens in Desktop.",
    visual: (
      <div className="lp-v-pbi">
        <div><span>✓</span> Sales.SemanticModel</div>
        <div><span>✓</span> Sales.Report</div>
        <div><span>✓</span> Sales.pbip</div>
      </div>
    ),
  },
];

function ModelDiagram({ draw = false }: { draw?: boolean }) {
  return (
    <svg className={`lp-model${draw ? " draw" : ""}`} viewBox="0 0 300 130" aria-hidden="true">
      <path className="lp-link" d="M88 40 C130 40 130 65 170 65" />
      <path className="lp-link" d="M88 100 C130 100 130 65 170 65" style={{ "--k": 1 } as CSSProperties} />
      <path className="lp-link" d="M248 65 L258 65" style={{ "--k": 2 } as CSSProperties} />
      <g>
        <rect x="10" y="22" width="78" height="36" rx="7" /><text x="22" y="44">Customers</text>
        <rect x="10" y="82" width="78" height="36" rx="7" /><text x="25" y="104">Products</text>
        <rect className="fact" x="170" y="44" width="78" height="42" rx="7" /><text x="191" y="69">Sales</text>
        <rect x="258" y="50" width="38" height="30" rx="7" /><text x="264" y="69">Date</text>
      </g>
    </svg>
  );
}

const HOW = [
  { title: "Get data", text: "Upload CSV or Excel, link a folder in OneDrive, SharePoint or Google Drive, or bring a Power BI file. Bring it as it is; cleaning comes next." },
  { title: "Clean", text: "The AI reads dates day first, takes RM out of prices, marks blanks and removes repeats. Each fix is listed with its reason for you to approve." },
  { title: "Model", text: "Tables are related, a date table is added and measures are written. Every formula is shown before anything uses it." },
  { title: "Report", text: "A dashboard you can edit and ask questions of, with each number checked against the data. Share it, or get it by email each week." },
];

/** The picture beside each step; it plays when its step arrives. */
function HowPicture({ step }: { step: number }) {
  const k = (n: number) => ({ "--k": n }) as CSSProperties;
  if (step === 0)
    return (
      <div className="lp-f-list">
        <div style={k(0)}><i className="ft csv">CSV</i><span>sales.csv</span><small>244 rows</small></div>
        <div style={k(1)}><i className="ft csv">CSV</i><span>products.csv</span><small>9 rows</small></div>
        <div style={k(2)}><i className="ft xls">XLS</i><span>customers.xlsx</span><small>40 rows</small></div>
        <p className="lp-f-note" style={k(3)}>3 files, 293 rows read</p>
      </div>
    );
  if (step === 1)
    return (
      <div className="lp-f-list lp-f-clean">
        {[["26/01/2024", "2024-01-26"], ["RM1,250.00", "1,250.00"], ["N/A", "Unknown"], ["3 repeated rows", "Removed"]].map(([from, to], i) => (
          <div key={from} style={k(i)}><span className="old">{from}</span><span className="new">{to}</span></div>
        ))}
        <p className="lp-f-note" style={k(4)}>8 steps ready for your review</p>
      </div>
    );
  if (step === 2)
    return (
      <div className="lp-f-model">
        <ModelDiagram draw />
        <p className="lp-f-note">4 tables, 3 relationships, 11 measures</p>
      </div>
    );
  return (
    <div className="lp-v-kpi lp-f-kpi">
      <small>Revenue, after cleaning</small>
      <strong>RM 153,948.70</strong>
      <span className="chg">241 orders across 6 regions</span>
      <svg viewBox="0 0 280 44" preserveAspectRatio="none" aria-hidden="true"><path pathLength={1} d="M2 36 L28 30 L54 33 L80 24 L106 27 L132 18 L158 22 L184 14 L210 17 L236 9 L262 12 L278 4" /></svg>
      <p className="lp-f-check"><Check size={11} strokeWidth={3} /> Summary checked against the data</p>
    </div>
  );
}

/**
 * How it works, as a scroll story. The scroll position drives it: a rail beside the step numbers fills, the step
 * nearest the reading line is lit, and the picture changes once past the halfway point between two steps (so it never
 * flickers at a boundary). On a phone the picture is pinned under the header and the steps scroll beneath it.
 */
function HowItWorks() {
  const box = useRef<HTMLDivElement>(null);
  const rail = useRef<HTMLDivElement>(null);
  const fill = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  const activeRef = useRef(-1);

  useEffect(() => {
    const steps = [...(box.current?.querySelectorAll<HTMLElement>(".lp-flow-step") ?? [])];
    const nums = steps.map((s) => s.querySelector<HTMLElement>(".lp-flow-num")!);
    const narrow = window.matchMedia("(max-width: 900px)");
    let ticking = false;

    const anchors = () => nums.map((n) => { const r = n.getBoundingClientRect(); return r.top + r.height / 2; });
    const readingLine = () => {
      if (!narrow.matches || !frame.current) return window.innerHeight / 2;
      const below = frame.current.getBoundingClientRect().bottom;
      return below + (window.innerHeight - below) * 0.4;
    };
    const layoutRail = () => {
      if (!box.current || !rail.current) return;
      const top = box.current.getBoundingClientRect().top, ys = anchors();
      rail.current.style.top = `${ys[0] - top}px`;
      rail.current.style.height = `${ys[ys.length - 1] - ys[0]}px`;
    };
    const update = () => {
      ticking = false;
      const line = readingLine(), centers = anchors(), last = centers.length - 1;
      let pos = 0;
      if (line >= centers[last]) pos = last;
      else if (line > centers[0]) {
        const k = centers.findIndex((c, j) => line >= c && line < centers[j + 1]);
        pos = k + (line - centers[k]) / (centers[k + 1] - centers[k]);
      }
      if (fill.current) fill.current.style.transform = `scaleY(${pos / last})`;
      steps.forEach((s, k) => (s.style.opacity = (1 - Math.min(1, Math.abs(pos - k)) * 0.65).toFixed(3)));
      if (activeRef.current < 0 || Math.abs(pos - activeRef.current) > 0.6) {
        activeRef.current = Math.round(pos);
        setActive(activeRef.current);
      }
    };
    const request = () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(update);
      }
    };
    const relayout = () => {
      layoutRail();
      request();
    };
    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", relayout);
    narrow.addEventListener("change", relayout);
    document.fonts?.ready.then(relayout);
    relayout();
    return () => {
      window.removeEventListener("scroll", request);
      window.removeEventListener("resize", relayout);
      narrow.removeEventListener("change", relayout);
    };
  }, []);

  function goTo(step: HTMLElement | null) {
    const num = step?.querySelector(".lp-flow-num");
    if (!num) return;
    const r = num.getBoundingClientRect();
    const line = window.matchMedia("(max-width: 900px)").matches && frame.current
      ? frame.current.getBoundingClientRect().bottom + (window.innerHeight - frame.current.getBoundingClientRect().bottom) * 0.4
      : window.innerHeight / 2;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollBy({ top: r.top + r.height / 2 - line, behavior: reduce ? "auto" : "smooth" });
  }

  return (
    <section id="how" className="lp-section">
      <div className="lp-wrap">
        <p className="lp-kicker">How it works</p>
        <h2>Four steps, one dataset you can trust.</h2>
        <div className="lp-flow">
          <div className="lp-flow-steps" ref={box}>
            <div className="lp-flow-rail" ref={rail} aria-hidden="true"><div className="lp-flow-fill" ref={fill} /></div>
            <ol>
            {HOW.map((step, i) => (
              <li key={step.title} className={`lp-flow-step${i === active ? " current" : ""}${i < active ? " passed" : ""}`}>
                <div className="lp-flow-body">
                  <button type="button" className="lp-flow-num" aria-label={`Go to step ${i + 1}, ${step.title}`} onClick={(e) => goTo(e.currentTarget.closest("li"))}>
                    {i + 1}
                  </button>
                  <h3>{step.title}</h3>
                  <p>{step.text}</p>
                </div>
              </li>
            ))}
            </ol>
          </div>
          <div className="lp-flow-stage">
            <div className="lp-flow-frame" ref={frame} aria-hidden="true">
              {HOW.map((step, i) => (
                <div key={step.title} className={`lp-flow-pane${i === active ? " on" : ""}${i < active ? " before" : ""}`}>
                  <HowPicture step={i} />
                </div>
              ))}
              <div className="lp-flow-count">Step {active + 1} of 4: {HOW[active].title}</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

const TOUR = [
  {
    id: "clean", label: "Clean", image: "/landing/clean.webp",
    text: "Every cleaning step is listed with the AI's reason. Compare the rows before and after, and undo any step.",
    alt: "The Transform screen: a sales table with eight applied cleaning steps, each with a short reason.",
  },
  {
    id: "model", label: "Model", image: "/landing/model.webp",
    text: "Tables are related and a date table is added for you. Every measure shows its formula.",
    alt: "The Model screen: sales, customers, products and date tables joined by relationships.",
  },
  {
    id: "report", label: "Report", image: "/landing/report.webp",
    text: "Each card shows the change, what drove it and a short summary the AI checked against the numbers.",
    alt: "A sales dashboard: revenue, orders and big-line revenue cards with changes, drivers and a monthly chart.",
  },
];

function Tour() {
  const [current, setCurrent] = useState(TOUR[0].id);
  const shown = TOUR.find((t) => t.id === current)!;
  return (
    <section id="tour" className="lp-section">
      <div className="lp-wrap">
        <div className="lp-head-row">
          <div>
            <p className="lp-kicker">The product</p>
            <h2>The real product, on real data.</h2>
          </div>
          <div className="lp-tabs" role="tablist" aria-label="Product tour">
            {TOUR.map((t) => (
              <button key={t.id} type="button" role="tab" id={`lp-tab-${t.id}`} aria-selected={t.id === current} aria-controls="lp-tour-panel" onClick={() => setCurrent(t.id)}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div id="lp-tour-panel" role="tabpanel" aria-labelledby={`lp-tab-${shown.id}`} className="lp-tour">
          <img src={shown.image} alt={shown.alt} width={1440} height={900} loading="lazy" decoding="async" />
          <p>{shown.text}</p>
        </div>
      </div>
    </section>
  );
}

const BEFORE_AFTER = [
  ["Month-end means copying exports into a spreadsheet, again.", "Refresh pulls in the new data and checks it before anyone sees it."],
  ["A new report waits in someone's queue for a week.", "Ask in plain language and get a chart back, with the numbers checked."],
  ["Nobody is sure which version of the numbers is right.", "One published dataset, every formula shown, and a history you can roll back."],
];

const CURRENCIES = { USD: "US$", MYR: "RM" } as const;
type Currency = keyof typeof CURRENCIES;

function defaultCurrency(): Currency {
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language];
  return languages.some((l) => /-MY$/i.test(l) || /^ms\b/i.test(l)) ? "MYR" : "USD";
}

const PLANS = [
  { name: "Free", free: true, rows: ["1 creator", "3 viewers", "2 datasets", "Manual refresh", "Small monthly AI credits"] },
  { name: "Pro", free: false, rows: ["1 creator per seat", "10 viewers", "20 datasets", "Daily refresh", "More AI credits, share links and PDF"] },
  {
    name: "Team", free: false,
    rows: ["Creators per seat", "Unlimited viewers", "Unlimited datasets", "Hourly refresh", "Pooled AI credits, row-level security, audit log"],
  },
];

function Pricing() {
  const [currency, setCurrency] = useState<Currency>(defaultCurrency);
  return (
    <section id="pricing" className="lp-section">
      <div className="lp-wrap">
        <div className="lp-head-row">
          <div>
            <p className="lp-kicker">Pricing</p>
            <h2>Start free.</h2>
            <p className="lp-sub">Paid plans open at launch, priced from what the AI really costs to run.</p>
          </div>
          <div className="lp-tabs" role="group" aria-label="Currency">
            {(Object.keys(CURRENCIES) as Currency[]).map((c) => (
              <button key={c} type="button" aria-pressed={currency === c} onClick={() => setCurrency(c)}>
                {c === "USD" ? "US$" : "RM"}
              </button>
            ))}
          </div>
        </div>
        <div className="lp-plans">
          {PLANS.map((plan) => (
            <div key={plan.name} className="lp-panel lp-plan">
              <h3>{plan.name}</h3>
              <p className="lp-price">{plan.free ? `${CURRENCIES[currency]}${currency === "MYR" ? " " : ""}0` : "Price set at launch"}</p>
              <ul>
                {plan.rows.map((row) => (
                  <li key={row}><Check size={15} aria-hidden="true" /> {row}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const FAQ = [
  {
    q: "How long does it take?",
    a: "In our tests with a messy sales file, Autopilot went from upload to a checked dashboard in under four minutes. You can also go step by step and approve each change.",
  },
  {
    q: "What if the AI gets something wrong?",
    a: "Every step waits for your approval and can be undone. Numbers the AI writes in summaries and answers are checked against the data, and anything that doesn't match is flagged.",
  },
  {
    q: "What does the AI see of my data?",
    a: "Column summaries, up to 5 sample rows per table and the results of the queries it runs. Your full data stays in your workspace, and the AI never sees rows a viewer isn't allowed to.",
  },
  {
    q: "Do I need Power BI?",
    a: "No. datawazi does the whole job in the browser. If your team uses Power BI, you can import a .pbix file or export a Power BI project.",
  },
  {
    q: "Where is my data kept?",
    a: "In Singapore, in your own workspace. Only people you invite can see it.",
  },
  {
    q: "How do you handle privacy law?",
    a: "We're completing our duties under Malaysia's PDPA (privacy notice, data protection officer and breach procedure) before public launch, and will add other regions' requirements as we grow.",
  },
  {
    q: "Which languages does it speak?",
    a: "English, and Bahasa Malaysia for the AI's answers and the main screens.",
  },
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
            <a href="#product">Product</a>
            <a href="#powerbi">Power BI</a>
            <a href="#pricing">Pricing</a>
          </nav>
          <div className="lp-header-actions">
            <Link to="/sign-in" className="lp-link">Sign in</Link>
            <Link to="/sign-in" className="lp-btn lp-btn-primary">Start free</Link>
          </div>
        </div>
      </header>

      <main id="lp-main">
        <section className="lp-hero">
          <div className="lp-beam" aria-hidden="true" />
          <div className="lp-wrap">
            <a className="lp-note" href="#how"><b>Autopilot</b> Upload to checked dashboard in under four minutes</a>
            <h1>From messy data to checked dashboards.</h1>
            <p className="lp-lede">For teams without a data analyst. The AI cleans, models and charts your data, and shows every change for you to approve.</p>
            <div className="lp-ctas">
              <Link to="/sign-in" className="lp-btn lp-btn-primary lp-btn-lg">Start free</Link>
              <a href="#how" className="lp-btn lp-btn-secondary lp-btn-lg">See how it works</a>
            </div>
            <div className="lp-stage">
              <CleaningDemo />
            </div>
          </div>
        </section>

        <div className="lp-wrap lp-sources">
          <p>Bring data from where it already lives</p>
          <ul>
            <li>CSV</li><li>Excel</li><li>OneDrive</li><li>SharePoint</li><li>Google Drive</li><li>Power BI</li>
          </ul>
        </div>

        <section id="product" className="lp-section">
          <div className="lp-wrap">
            <p className="lp-kicker">Product</p>
            <h2>Every step shown. Every number checked.</h2>
            <p className="lp-sub">The AI does the work a data analyst would. You see what it changed and why, and nothing goes live until you approve it.</p>
            <div className="lp-bento">
              {FEATURES.map((f) => (
                <article key={f.title} className={`lp-panel lp-card${f.wide ? " wide" : ""}`}>
                  <div className="lp-viz">{f.visual}</div>
                  <h3>{f.title}</h3>
                  <p>{f.text}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <HowItWorks />

        <Tour />

        <section className="lp-section">
          <div className="lp-wrap">
            <p className="lp-kicker">Why it helps</p>
            <h2>Less time chasing numbers.</h2>
            <div className="lp-compare">
              {BEFORE_AFTER.map(([before, after]) => (
                <div key={before} className="lp-compare-row">
                  <p className="lp-before"><small>Today</small>{before}</p>
                  <p className="lp-after"><small>With datawazi</small>{after}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-wrap">
            <p className="lp-kicker">Trust</p>
            <h2>Numbers you can stand behind.</h2>
            <div className="lp-trust">
              <div className="lp-panel lp-trust-big">
                <Undo2 size={20} aria-hidden="true" />
                <h3>Nothing changes until you approve it</h3>
                <p>The AI suggests each step with its reason. Approve them one by one or all at once, and undo any of them later.</p>
                <ol className="lp-review" aria-label="Example of cleaning steps waiting for approval">
                  <li><span>Trim spaces in Region</span><span className="lp-tag ok">Approved</span></li>
                  <li><span>Change Order Date to date (dmy)</span><span className="lp-tag ok">Approved</span></li>
                  <li><span>Remove duplicate rows</span><span className="lp-tag ai">AI</span></li>
                </ol>
              </div>
              <div className="lp-panel">
                <Check size={20} aria-hidden="true" />
                <h3>Checked on every refresh</h3>
                <p>Fewer rows, missing columns, broken measures and weaker table links are flagged, and you can roll back.</p>
              </div>
              <div className="lp-panel">
                <Eye size={20} aria-hidden="true" />
                <h3>You know what the AI sees</h3>
                <p>Column summaries and a few sample rows, never your whole dataset.</p>
              </div>
              <div className="lp-panel">
                <ShieldCheck size={20} aria-hidden="true" />
                <h3>Each viewer sees their rows</h3>
                <p>Row-level security applies to every chart, question, email and export.</p>
              </div>
              <div className="lp-panel">
                <Lock size={20} aria-hidden="true" />
                <h3>Your data stays private</h3>
                <p>Each company's workspace is walled off in the database, and only people you invite can get in.</p>
              </div>
            </div>
          </div>
        </section>

        <section id="powerbi" className="lp-section">
          <div className="lp-wrap">
            <p className="lp-kicker">Power BI</p>
            <h2>Works with Power BI, in both directions.</h2>
            <div className="lp-pbi">
              <div className="lp-panel">
                <FileUp size={20} aria-hidden="true" />
                <h3>Bring a Power BI file</h3>
                <p>Import a .pbix file or a .pbip project. Tables, relationships and measures come across, and the AI explains each one.</p>
              </div>
              <div className="lp-panel">
                <FileDown size={20} aria-hidden="true" />
                <h3>Take a Power BI project back</h3>
                <p>Export any dashboard as a Power BI project, with its model, DAX measures and pages, ready to open in Desktop.</p>
              </div>
            </div>
          </div>
        </section>

        <Pricing />

        <section className="lp-section">
          <div className="lp-wrap lp-faq">
            <div>
              <p className="lp-kicker">Questions</p>
              <h2>Asked before signing up.</h2>
            </div>
            <div>
              {FAQ.map((item) => (
                <details key={item.q}>
                  <summary>{item.q}</summary>
                  <p>{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-final">
          <div className="lp-wrap">
            <h2>Your next report, checked and ready by lunch.</h2>
            <div className="lp-ctas">
              <Link to="/sign-in" className="lp-btn lp-btn-primary lp-btn-lg">Start free</Link>
              <a href="#how" className="lp-btn lp-btn-secondary lp-btn-lg">See how it works</a>
            </div>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-wrap lp-footer-row">
          <div className="lp-brand">
            <BrandMark size={20} />
            <span>datawazi</span>
          </div>
          <span>Data kept in Singapore</span>
          <span>English and Bahasa Malaysia</span>
          <span>Privacy notice and terms of service are published before launch.</span>
          <Link to="/sign-in" className="lp-link">Sign in</Link>
        </div>
      </footer>
    </div>
  );
}
