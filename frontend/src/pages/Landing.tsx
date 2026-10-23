import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { Check, Eye, FileDown, FileUp, ShieldCheck, Undo2 } from "lucide-react";
import { BrandMark } from "../components/ui";
import "./landing.css";

/**
 * The page for visitors who aren't signed in (at /, and at /welcome for anyone).
 * The hero replays a real cleaning of samples/sales.csv: the rows, the fixes and the totals are what
 * datawazi produced on that file (153,948.70 over 241 orders, checked against the file in the Gate 1 dry run).
 */

type Fix = { from: string; to: string; at: number };
type Cell = string | Fix;

const ROWS: { id: string; date: Cell; qty: number; price: Cell; region: Cell }[] = [
  { id: "SO-10001", date: { from: "26/01/2024", to: "2024-01-26", at: 1.0 }, qty: 2, price: "18.50", region: "Central" },
  { id: "SO-10005", date: "2024-04-14", qty: 3, price: { from: "RM 79.90", to: "79.90", at: 2.2 }, region: "East Malaysia" },
  { id: "SO-10011", date: { from: "04 Aug 2024", to: "2024-08-04", at: 1.25 }, qty: 3, price: "18.50", region: "East Coast" },
  { id: "SO-10012", date: "2024-05-26", qty: 2, price: { from: "RM1,250.00", to: "1,250.00", at: 2.45 }, region: "East Malaysia" },
  {
    id: "SO-10017", date: "2024-06-05", qty: 1, price: { from: "RM 12.90", to: "12.90", at: 2.7 },
    region: { from: "(blank)", to: "Unknown", at: 3.6 },
  },
  { id: "SO-10030", date: "2024-06-07", qty: 1, price: "1,250.00", region: { from: "N/A", to: "Unknown", at: 3.85 } },
];

const STEPS: [string, number][] = [
  ["Dates read day first", 1.6],
  ["RM taken out of prices", 3.0],
  ["Blank regions marked Unknown", 4.3],
  ["3 repeated rows removed", 5.4],
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

function CleaningDemo() {
  const [run, setRun] = useState(0);
  return (
    <figure className="lp-stage" aria-labelledby="lp-stage-caption">
      <div className="lp-stage-bar">
        <span className="lp-mono">sales.csv</span>
        <span id="lp-stage-caption" className="lp-muted">244 rows, 7 columns, cleaned and charted</span>
        <button type="button" className="lp-replay" onClick={() => setRun((n) => n + 1)}>
          Play again
        </button>
      </div>
      <div className="lp-stage-body" key={run}>
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
                <Check size={16} aria-hidden="true" /> {text}
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
          <div className="lp-chart-title">Revenue by region</div>
          <div className="lp-bars">
            {REGIONS.map(([name, value], i) => (
              <div key={name} className="lp-bar-row">
                <span>{name}</span>
                <div
                  className={`lp-bar${name === "Unknown" ? " other" : ""}`}
                  style={{ ...delay(8.2 + i * 0.1), width: `${(value / REGIONS[0][1]) * 100}%` }}
                />
                <span className="num">{value.toFixed(1)}K</span>
              </div>
            ))}
          </div>
          <p className="lp-checked">
            <Check size={16} aria-hidden="true" /> Totals match the source file
          </p>
        </div>
      </div>
    </figure>
  );
}

const HOW = [
  { title: "Get data", text: "Upload CSV or Excel, link a shared OneDrive, SharePoint or Google Drive file, or bring a Power BI file." },
  { title: "Clean", text: "The AI fixes dates, prices, blanks and repeats. You see each step and approve it." },
  { title: "Model", text: "Tables are related, a date table is added and measures are written, with every formula shown." },
  { title: "Report", text: "A dashboard you can edit, ask questions of in plain language, share and get by email each week." },
];

const PLANS = [
  { name: "Free", price: "RM 0", rows: ["1 creator", "3 viewers", "2 datasets", "Manual refresh", "Small monthly AI credits"] },
  { name: "Pro", price: "Price set at launch", rows: ["1 creator per seat", "10 viewers", "20 datasets", "Daily refresh", "More AI credits, share links and PDF"] },
  {
    name: "Team", price: "Price set at launch",
    rows: ["Creators per seat", "Unlimited viewers", "Unlimited datasets", "Hourly refresh", "Pooled AI credits, row-level security, audit log"],
  },
];

const FAQ = [
  {
    q: "What does the AI see of my data?",
    a: "Column summaries, up to 5 sample rows per table and the results of the queries it runs. Your full file stays in your workspace, and the AI never sees rows a viewer isn't allowed to.",
  },
  {
    q: "Can I check the numbers?",
    a: "Yes. Every measure shows its formula, every cleaning step shows what it changed, and anything the AI did can be undone.",
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
    q: "Is it ready for Malaysia's PDPA?",
    a: "We're completing the PDPA duties (privacy notice, data protection officer and breach procedure) before public launch.",
  },
  {
    q: "Does it work in Bahasa Malaysia?",
    a: "The AI answers in Bahasa Malaysia, and the main screens are translated. The rest of the app follows.",
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
          <a href="#powerbi">Power BI</a>
          <a href="#pricing">Pricing</a>
        </nav>
        <div className="lp-header-actions">
          <Link to="/sign-in" className="lp-link">Sign in</Link>
          <Link to="/sign-in" className="lp-btn lp-btn-primary">Try it with your file</Link>
        </div>
      </header>

      <main id="lp-main">
        <section className="lp-hero">
          <div className="lp-hero-copy">
            <h1>From messy spreadsheets to checked dashboards.</h1>
            <p>Drop in a file. The AI cleans it, models it and builds the dashboard, and shows you every change to approve.</p>
            <div className="lp-ctas">
              <Link to="/sign-in" className="lp-btn lp-btn-primary lp-btn-lg">Try it with your file</Link>
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
              <p>Column summaries and a few sample rows, never your whole file.</p>
            </div>
            <div className="lp-trust-cell">
              <ShieldCheck size={22} aria-hidden="true" />
              <h3>Each viewer sees their rows</h3>
              <p>Row-level security applies to every chart, question, email and export.</p>
            </div>
            <div className="lp-trust-cell lp-trust-dark">
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

        <section id="pricing" className="lp-section">
          <h2>Pricing</h2>
          <p className="lp-lead">Start free. Paid plans open at launch, priced from what the AI really costs to run.</p>
          <div className="lp-pricing-scroll">
            <table className="lp-pricing">
              <thead>
                <tr>
                  {PLANS.map((p) => (
                    <th key={p.name} scope="col">
                      <span className="lp-plan">{p.name}</span>
                      <span className="lp-price">{p.price}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {PLANS[0].rows.map((_, row) => (
                  <tr key={row}>
                    {PLANS.map((p) => <td key={p.name}>{p.rows[row]}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

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
          <h2>See your own file as a dashboard.</h2>
          <Link to="/sign-in" className="lp-btn lp-btn-primary lp-btn-lg">Try it with your file</Link>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-brand">
          <BrandMark size={22} />
          <span>datawazi</span>
        </div>
        <p>Made in Malaysia. Privacy notice and terms of service are published before launch.</p>
        <Link to="/sign-in" className="lp-link">Sign in</Link>
      </footer>
    </div>
  );
}
