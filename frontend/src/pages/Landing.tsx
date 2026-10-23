import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Eye, FileDown, FileUp, Lock, ShieldCheck, Undo2 } from "lucide-react";
import { BrandMark } from "../components/ui";
import "./landing.css";

/**
 * The page for visitors who aren't signed in (at /, and at /welcome for anyone).
 * The hero replays a real cleaning of samples/sales.csv: the rows, the fixes, the totals and the answer are what
 * datawazi produced on that file (153,948.70 over 241 orders, checked against the file in the Gate 1 dry run).
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

// Revenue by region in RM thousands, after cleaning.
const REGIONS: [string, number][] = [
  ["Central", 46.4], ["Southern", 36.2], ["East Coast", 25.8], ["Unknown", 16.0], ["East Malaysia", 15.4], ["Northern", 14.2],
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

/** Messy rows fix themselves, fold into a checked dashboard, and a plain-language question gets answered. */
function CleaningDemo() {
  const [run, setRun] = useState(0);
  return (
    <figure
      className="lp-stage"
      aria-label="Demo: a messy sales file is cleaned (dates, prices, blank regions and 3 repeated rows), becomes a dashboard with revenue of RM 153,948.70 over 241 orders, and answers which region sold the most: Central."
    >
      <div className="lp-stage-bar">
        <span className="lp-mono">sales.csv</span>
        <span className="lp-muted">244 rows, 7 columns</span>
        <button type="button" className="lp-replay" onClick={() => setRun((n) => n + 1)}>
          Play again
        </button>
      </div>
      <div className="lp-stage-body" key={run} aria-hidden="true">
        <div className="lp-table-layer">
          <div className="lp-table-scroll">
            <table className="lp-table">
              <thead>
                <tr>
                  <th>Order ID</th>
                  <th>Order Date</th>
                  <th className="num">Qty</th>
                  <th className="num">Unit Price</th>
                  <th>Region</th>
                </tr>
              </thead>
              <tbody>
                {ROWS.map((r) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td><Value cell={r.date} /></td>
                    <td className="num">{r.qty}</td>
                    <td className="num"><Value cell={r.price} /></td>
                    <td><Value cell={r.region} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="lp-steps">
            {STEPS.map(([text, at]) => (
              <li key={text} className="lp-step" style={delay(at)}>
                <Check size={16} /> {text}
              </li>
            ))}
          </ul>
        </div>
        <div className="lp-dash-layer">
          <div className="lp-kpis">
            <div>
              <div className="lp-kpi-label">Revenue</div>
              <div className="lp-kpi-value">RM 153,948.70</div>
            </div>
            <div>
              <div className="lp-kpi-label">Orders</div>
              <div className="lp-kpi-value">241</div>
            </div>
          </div>
          <div className="lp-bars">
            {REGIONS.map(([name, value], i) => (
              <div key={name} className="lp-bar-row">
                <span>{name}</span>
                <div
                  className={`lp-bar${name === "Unknown" ? " other" : ""}${i === 0 ? " top" : ""}`}
                  style={{ ...delay(4.0 + i * 0.08), width: `${(value / REGIONS[0][1]) * 100}%` }}
                />
                <span className="num">{value.toFixed(1)}K</span>
              </div>
            ))}
          </div>
          <div className="lp-ask">
            <p className="lp-question">Which region sold the most?</p>
            <p className="lp-answer">
              <span className="lp-tag ai">AI</span> Central, at RM 46.4K, about 30% of revenue.
            </p>
          </div>
        </div>
      </div>
    </figure>
  );
}

const HOW = [
  { title: "Get data", text: "Upload CSV or Excel, link OneDrive, SharePoint or Google Drive, or bring a Power BI model. More sources are on the way." },
  { title: "Clean", text: "The AI fixes dates, prices, blanks and repeats. You see each step and approve it." },
  { title: "Model", text: "Tables are related, a date table is added and measures are written, with every formula shown." },
  { title: "Report", text: "A dashboard you can edit, ask questions of in plain language, share and get by email each week." },
];

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
      <div className="lp-pricing-head">
        <h2>Pricing</h2>
        <div className="lp-currency" role="group" aria-label="Currency">
          {(Object.keys(CURRENCIES) as Currency[]).map((c) => (
            <button key={c} type="button" aria-pressed={currency === c} onClick={() => setCurrency(c)}>
              {c === "USD" ? "US$" : "RM"}
            </button>
          ))}
        </div>
      </div>
      <p className="lp-lead">Start free. Paid plans open at launch, priced from what the AI really costs to run.</p>
      <div className="lp-plans">
        {PLANS.map((plan) => (
          <div key={plan.name} className="lp-plan-card">
            <h3>{plan.name}</h3>
            <p className="lp-price">{plan.free ? `${CURRENCIES[currency]}${currency === "MYR" ? " " : ""}0` : "Price set at launch"}</p>
            <ul>
              {plan.rows.map((row) => (
                <li key={row}><Check size={16} aria-hidden="true" /> {row}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function Tour() {
  const [current, setCurrent] = useState(TOUR[0].id);
  const shown = TOUR.find((t) => t.id === current)!;
  return (
    <section id="tour" className="lp-section">
      <h2>The real product, on real data</h2>
      <div className="lp-tabs" role="tablist" aria-label="Product tour">
        {TOUR.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`lp-tab-${t.id}`}
            aria-selected={t.id === current}
            aria-controls="lp-tour-panel"
            onClick={() => setCurrent(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div id="lp-tour-panel" role="tabpanel" aria-labelledby={`lp-tab-${shown.id}`} className="lp-tour">
        <p>{shown.text}</p>
        <img src={shown.image} alt={shown.alt} width={1440} height={900} loading="lazy" decoding="async" />
      </div>
    </section>
  );
}

const FAQ = [
  {
    q: "How long does it take?",
    a: "In our test with a messy sales file, Autopilot went from upload to a checked dashboard in 99 seconds. You can also go step by step and approve each change.",
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
        <Link to="/welcome" className="lp-brand" aria-label="datawazi home">
          <BrandMark size={28} />
          <span>datawazi</span>
        </Link>
        <nav aria-label="Main" className="lp-nav">
          <a href="#how">How it works</a>
          <a href="#tour">Product</a>
          <a href="#powerbi">Power BI</a>
          <a href="#pricing">Pricing</a>
        </nav>
        <div className="lp-header-actions">
          <Link to="/sign-in" className="lp-link">Sign in</Link>
          <Link to="/sign-in" className="lp-btn lp-btn-primary">Start free</Link>
        </div>
      </header>

      <main id="lp-main">
        <section className="lp-hero">
          <div className="lp-hero-copy">
            <h1>From messy data to checked dashboards.</h1>
            <p>For teams without a data analyst. The AI cleans, models and charts your data, and shows every change for you to approve.</p>
            <div className="lp-ctas">
              <Link to="/sign-in" className="lp-btn lp-btn-primary lp-btn-lg">Start free</Link>
              <a href="#how" className="lp-btn lp-btn-ghost lp-btn-lg">See how it works</a>
            </div>
          </div>
          <CleaningDemo />
        </section>

        <section id="how" className="lp-section">
          <h2>How it works</h2>
          <ol className="lp-how">
            {HOW.map((step, i) => (
              <li key={step.title}>
                <span className="lp-how-n" aria-hidden="true">{i + 1}</span>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <Tour />

        <section className="lp-section">
          <h2>Less time chasing numbers</h2>
          <div className="lp-compare">
            <div className="lp-compare-head" aria-hidden="true">
              <span>Today</span>
              <span>With datawazi</span>
            </div>
            {BEFORE_AFTER.map(([before, after]) => (
              <div key={before} className="lp-compare-row">
                <p className="lp-before"><span className="lp-sr">Today: </span>{before}</p>
                <ArrowRight size={18} aria-hidden="true" className="lp-compare-arrow" />
                <p className="lp-after"><span className="lp-sr">With datawazi: </span>{after}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="lp-section">
          <h2>Numbers you can stand behind</h2>
          <div className="lp-trust">
            <div className="lp-trust-big">
              <Undo2 size={22} aria-hidden="true" />
              <h3>Nothing changes until you approve it</h3>
              <p>The AI suggests each step with its reason. Approve them one by one or all at once, and undo any of them later.</p>
              <ol className="lp-review" aria-label="Example of cleaning steps waiting for approval">
                <li><span>Trim spaces in Region</span><span className="lp-tag ok">Approved</span></li>
                <li><span>Change Order Date to date (dmy)</span><span className="lp-tag ok">Approved</span></li>
                <li><span>Remove duplicate rows</span><span className="lp-tag ai">AI</span></li>
              </ol>
            </div>
            <div className="lp-trust-cell">
              <Check size={22} aria-hidden="true" />
              <h3>Checked on every refresh</h3>
              <p>Fewer rows, missing columns, broken measures and weaker table links are flagged, and you can roll back.</p>
            </div>
            <div className="lp-trust-cell">
              <Eye size={22} aria-hidden="true" />
              <h3>You know what the AI sees</h3>
              <p>Column summaries and a few sample rows, never your whole dataset.</p>
            </div>
            <div className="lp-trust-cell">
              <ShieldCheck size={22} aria-hidden="true" />
              <h3>Each viewer sees their rows</h3>
              <p>Row-level security applies to every chart, question, email and export.</p>
            </div>
            <div className="lp-trust-cell lp-trust-dark">
              <Lock size={22} aria-hidden="true" />
              <h3>Your data stays private</h3>
              <p>Each company's workspace is walled off in the database, and only people you invite can get in.</p>
            </div>
          </div>
        </section>

        <section id="powerbi" className="lp-band">
          <div className="lp-band-inner">
            <h2>Works with Power BI, in both directions</h2>
            <div className="lp-band-rows">
              <div>
                <FileUp size={22} aria-hidden="true" />
                <h3>Bring a Power BI file</h3>
                <p>Import a .pbix file or a .pbip project. Tables, relationships and measures come across, and the AI explains each one.</p>
              </div>
              <div>
                <FileDown size={22} aria-hidden="true" />
                <h3>Take a Power BI project back</h3>
                <p>Export any dashboard as a Power BI project, with its model, DAX measures and pages, ready to open in Desktop.</p>
              </div>
            </div>
          </div>
        </section>

        <Pricing />

        <section className="lp-section lp-faq">
          <h2>Questions</h2>
          <div>
            {FAQ.map((item) => (
              <details key={item.q}>
                <summary>{item.q}</summary>
                <p>{item.a}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="lp-final">
          <h2>See your own data as a dashboard.</h2>
          <Link to="/sign-in" className="lp-btn lp-btn-primary lp-btn-lg">Start free</Link>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-brand">
          <BrandMark size={22} />
          <span>datawazi</span>
        </div>
        <p>Privacy notice and terms of service are published before launch.</p>
        <Link to="/sign-in" className="lp-link">Sign in</Link>
      </footer>
    </div>
  );
}
