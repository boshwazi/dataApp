import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Check } from "lucide-react";
import { BrandMark } from "../components/ui";
import "./landing.css";

/**
 * The page for visitors who aren't signed in (at /, and at /welcome for anyone). Direction C of the landing mock-ups
 * (docs/design/landing-mockups): DataHub's teal as the stage, the real product rising out of it, then the app's own
 * look (Geist, hairlines, mono labels, cyan for "where you are", amber only for AI).
 * The screenshots in public/landing are the app on the sample sales data; the run log replays the same file
 * (241 orders and RM 153,948.70 after cleaning, checked against the file in the Gate 1 dry run).
 */

const STAGE = [
  {
    id: "getdata", no: "01", label: "Get data", image: "/landing/getdata.png",
    text: "Each table is profiled as it loads: types, blanks, ranges and data quality issues, with the AI's description.",
    alt: "The Get Data screen: three tables, with a column profile for customers listing each column's type, fill rate and issues.",
  },
  {
    id: "clean", no: "02", label: "Clean", image: "/landing/clean.png",
    text: "Every cleaning step is listed with the AI's reason. Compare the rows before and after, and undo any step.",
    alt: "The Transform screen: the sales table with eight applied steps, each with a short reason, and one AI step waiting for review.",
  },
  {
    id: "model", no: "03", label: "Model", image: "/landing/model.png",
    text: "Tables are related and a date table is added for you. Every measure shows its formula.",
    alt: "The Model screen: sales, customers, products and date tables joined by relationships, with measures waiting for review.",
  },
  {
    id: "report", no: "04", label: "Report", image: "/landing/report.png",
    text: "Each card shows the change, what drove it and a short summary the AI checked against the numbers.",
    alt: "The dashboard editor: revenue, orders, customers and order value cards with changes and sparklines, and a monthly revenue chart.",
  },
];

/** The product, under step tabs. Clicking a tab swaps the screenshot; nothing moves on its own. */
function Stage() {
  const [current, setCurrent] = useState("clean");
  const shown = STAGE.find((s) => s.id === current)!;
  return (
    <div className="lp-wrap lp-stage">
      <div className="lp-stage-tabs" role="tablist" aria-label="The product, step by step">
        {STAGE.map((s) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            id={`lp-tab-${s.id}`}
            aria-selected={s.id === current}
            aria-controls="lp-stage-panel"
            onClick={() => setCurrent(s.id)}
          >
            <b>{s.no}</b>
            {s.label}
          </button>
        ))}
      </div>
      <figure id="lp-stage-panel" role="tabpanel" aria-labelledby={`lp-tab-${shown.id}`} className="lp-window">
        <div className="lp-chrome" aria-hidden="true">
          <i /><i /><i />
          <span>datawazi · Sample sales review / {shown.label}</span>
        </div>
        <div className="lp-shots">
          {STAGE.map((s) => (
            <img
              key={s.id}
              src={s.image}
              alt={s.id === current ? s.alt : ""}
              aria-hidden={s.id !== current}
              className={s.id === current ? "on" : ""}
              width={1440}
              height={900}
              loading={s.id === "clean" ? "eager" : "lazy"}
              decoding="async"
            />
          ))}
        </div>
      </figure>
      <p className="lp-stage-text" aria-live="polite">{shown.text}</p>
    </div>
  );
}

const RUN: { at: string; mark: "ok" | "ai"; text: string; note: string }[] = [
  { at: "00:02", mark: "ok", text: "Read sales, customers, products", note: "293 rows" },
  { at: "00:19", mark: "ok", text: "Dates read day first", note: "3 formats" },
  { at: "00:31", mark: "ok", text: "RM taken out of Unit Price", note: "decimal" },
  { at: "00:44", mark: "ai", text: "Remove 3 repeated orders", note: "AI · review" },
  { at: "00:52", mark: "ai", text: "Mark 16 blank regions Unknown", note: "AI · review" },
  { at: "01:12", mark: "ok", text: "Relate sales to customers, products", note: "100% match" },
  { at: "01:40", mark: "ok", text: "Revenue over 241 orders", note: "RM 153,948.70" },
  { at: "02:38", mark: "ok", text: "Dashboard, 3 pages", note: "totals add up" },
];

/** An example Autopilot run, written out line by line once it scrolls into view (all at once with reduced motion). */
function RunLog() {
  const box = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div ref={box} className={`lp-log${shown ? " shown" : ""}`} role="img" aria-label="An example Autopilot run on sales.csv: three tables read, dates and prices fixed, two AI steps waiting for review, tables related, revenue of RM 153,948.70 over 241 orders, and a three-page dashboard whose totals add up. It stops for your review with 8 changes and nothing published.">
      <div className="lp-log-top" aria-hidden="true">
        <span>Example run · sales.csv</span>
        <span>02:41</span>
      </div>
      <ol aria-hidden="true">
        {RUN.map((r, i) => (
          <li key={r.at} style={{ animationDelay: `${0.15 + i * 0.28}s` }}>
            <span className="t">{r.at}</span>
            <span className={r.mark}>{r.mark === "ok" ? "✓" : "✦"}</span>
            <span>{r.text}</span>
            <span className="n">{r.note}</span>
          </li>
        ))}
      </ol>
      <div className="lp-log-stop" aria-hidden="true" style={{ animationDelay: `${0.15 + RUN.length * 0.28}s` }}>
        <span><b>Stopped for your review.</b> 8 changes, nothing published.</span>
        <span className="go">Review →</span>
      </div>
    </div>
  );
}

const HOW = [
  { title: "Get data", head: "Bring it as it is", text: "Upload CSV or Excel, link a folder in OneDrive, SharePoint or Google Drive, or bring a Power BI file. Cleaning comes next." },
  { title: "Clean", head: "Every fix, with its reason", text: "The AI reads dates day first, takes RM out of prices, marks blanks and removes repeats. Each fix is listed for you to approve." },
  { title: "Model", head: "Related and measured", text: "Tables are related, a date table is added and measures are written. Every formula is shown before anything uses it." },
  { title: "Report", head: "A dashboard you can ask", text: "Edit it and ask it questions, with each number checked against the data. Share it, or get it by email each week." },
];

const BEFORE_AFTER = [
  ["Month-end means copying exports into a spreadsheet, again.", "Refresh pulls in the new data and checks it before anyone sees it."],
  ["A new report waits in someone's queue for a week.", "Ask in plain language and get a chart back, with the numbers checked."],
  ["Nobody is sure which version of the numbers is right.", "One published dataset, every formula shown, and a history you can roll back."],
];

const TRUST = [
  ["Nothing changes until you approve it", "The AI suggests each step with its reason. Approve them one by one or all at once, and undo any of them later."],
  ["Checked on every refresh", "Fewer rows, missing columns, broken measures and weaker table links are flagged, and you can roll back."],
  ["You know what the AI sees", "Column summaries and a few sample rows, never your whole dataset."],
  ["Each viewer sees their rows", "Row-level security applies to every chart, question, email and export."],
  ["Your data stays private", "Each company's workspace is walled off in the database, and only people you invite can get in."],
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
        <div className="lp-head">
          <div>
            <p className="lp-kicker">Pricing</p>
            <h2>Start free.</h2>
            <p className="lp-sub">Paid plans open at launch, priced from what the AI really costs to run.</p>
          </div>
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
            <div key={plan.name} className={`lp-cell lp-plan${plan.free ? " free" : ""}`}>
              <p className="lp-kicker">{plan.name}</p>
              <p className="lp-price">{plan.free ? `${CURRENCIES[currency]}${currency === "MYR" ? " " : ""}0` : "Price set at launch"}</p>
              <ul>
                {plan.rows.map((row) => (
                  <li key={row}><Check size={15} aria-hidden="true" /> {row}</li>
                ))}
              </ul>
              {plan.free && <Link to="/sign-in" className="lp-btn lp-btn-primary">Start free</Link>}
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
    a: "In our tests with a messy sales file, Autopilot went from upload to a checked dashboard in under three minutes. How long it takes depends on how large and complex your source data is. You can also go step by step and approve each change.",
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
    q: "Do I need Power BI?",
    a: "No. datawazi does the whole job in the browser. If your team already has Power BI files, import a .pbix or .pbip and carry on here.",
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
            <div>
              <p className="lp-kicker">For teams without a data analyst</p>
              <h1>From raw data to <em>real insights</em>.</h1>
            </div>
            <div className="lp-hero-side">
              <p className="lp-lede">The AI cleans, models and charts your data, and shows every change for you to approve. Nothing goes live until you do.</p>
              <div className="lp-ctas">
                <Link to="/sign-in" className="lp-btn lp-btn-light lp-btn-lg">Start free</Link>
                <a href="#how" className="lp-btn lp-btn-ghost lp-btn-lg">See how it works</a>
              </div>
              <a className="lp-hero-note" href="#autopilot">Autopilot: upload to a checked dashboard in under three minutes →</a>
            </div>
          </div>
        </section>

        <Stage />

        <div className="lp-wrap lp-sources">
          <p className="lp-kicker">Bring data from where it lives</p>
          <ul>
            <li>CSV</li><li>Excel</li><li>OneDrive</li><li>SharePoint</li><li>Google Drive</li><li>Power&nbsp;BI</li>
          </ul>
        </div>

        <section id="product" className="lp-section">
          <div className="lp-wrap">
            <div className="lp-head">
              <div>
                <p className="lp-kicker">Product</p>
                <h2>Every step shown. Every number checked.</h2>
                <p className="lp-sub">The AI does the work a data analyst would. You see what it changed and why, and nothing goes live until you approve it.</p>
              </div>
            </div>
            <div className="lp-bento">
              <article className="lp-cell tall">
                <p className="lp-kicker">Clean</p>
                <h3>Each fix listed, with its reason</h3>
                <p>The AI proposes steps from a fixed catalogue. The engine checks each one and refuses anything that would break a table.</p>
                <ol className="lp-steps" aria-label="Example of cleaning steps">
                  <li><i>01</i><span>Trim spaces in Region<small>Stray spaces split one region into two</small></span></li>
                  <li><i>02</i><span>Make Region title case<small>'CENTRAL' and 'central' become one</small></span></li>
                  <li className="on"><i>03</i><span>Order Date to date (dmy)<small>26/01/2024 is read day first</small></span></li>
                  <li className="ai"><i>04</i><span>Remove repeated orders <em className="lp-tag">AI</em><small>3 order IDs appear twice; waiting for you</small></span></li>
                </ol>
              </article>
              <article className="lp-cell">
                <p className="lp-kicker">Model</p>
                <h3>Related, with a date table</h3>
                <p>Every relationship is checked before it's kept, and every formula is shown.</p>
                <div className="lp-rels" aria-hidden="true">
                  <div><span>sales</span><i /><span>customers</span></div>
                  <div><span>sales</span><i className="ai" /><span>Date</span><em className="lp-tag">AI</em></div>
                </div>
              </article>
              <article className="lp-cell">
                <p className="lp-kicker">Report</p>
                <h3>KPI cards that explain themselves</h3>
                <div className="lp-kpi" aria-hidden="true">
                  <small>Revenue · 2025 to date</small>
                  <strong>RM 46.4K</strong>
                  <span>▼ −RM 42.8K (−48.0%) vs same period 2024</span>
                  <svg viewBox="0 0 280 40" preserveAspectRatio="none"><polyline points="2,26 28,20 54,30 80,12 106,22 132,16 158,28 184,8 210,18 236,24 262,16 278,20" /></svg>
                </div>
              </article>
              <article className="lp-cell">
                <p className="lp-kicker">Ask</p>
                <h3>Answers with the working shown</h3>
                <div className="lp-ask" aria-hidden="true">
                  <div className="q">Which region sold the most?</div>
                  <div><b>Central</b>, at RM 46.4K, about 30% of revenue.</div>
                  <div className="how">› How this was calculated</div>
                </div>
              </article>
              <article className="lp-cell">
                <p className="lp-kicker">Bahasa Malaysia</p>
                <h3>Ask in English or BM</h3>
                <p>The AI answers in the language you ask in, and the main screens are translated.</p>
              </article>
            </div>
          </div>
        </section>

        <section id="autopilot" className="lp-section lp-tint">
          <div className="lp-wrap lp-auto">
            <div>
              <p className="lp-kicker">Autopilot</p>
              <h2>Upload a file. Get a checked dashboard in under three minutes.</h2>
              <p className="lp-sub">Autopilot cleans your data, relates the tables, writes the measures and builds the dashboard on its own. Then it stops, so you can review every change before anyone sees it.</p>
              <dl className="lp-facts">
                <div><dt>&lt;3 min</dt><dd>from upload to a checked dashboard on our messy sample files</dd></div>
                <div><dt>8</dt><dd>cleaning steps, each with the AI's reason, to approve or undo</dd></div>
                <div><dt>Every</dt><dd>number the AI writes is checked; anything that doesn't match is flagged</dd></div>
              </dl>
              <p className="lp-note">Times depend on how large and complex your source data is.</p>
            </div>
            <RunLog />
          </div>
        </section>

        <section id="how" className="lp-section">
          <div className="lp-wrap">
            <div className="lp-head">
              <div>
                <p className="lp-kicker">How it works</p>
                <h2>Four steps, one dataset you can trust.</h2>
              </div>
            </div>
            <ol className="lp-how">
              {HOW.map((step, i) => (
                <li key={step.title}>
                  <span className="lp-no">0{i + 1} / {step.title}</span>
                  <h3>{step.head}</h3>
                  <p>{step.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="lp-section lp-tint">
          <div className="lp-wrap lp-split">
            <div>
              <p className="lp-kicker">Why it helps</p>
              <h2>Less time chasing numbers.</h2>
            </div>
            <div className="lp-compare">
              <div className="lp-compare-head" aria-hidden="true"><span>Today</span><span>With datawazi</span></div>
              {BEFORE_AFTER.map(([before, after]) => (
                <div key={before} className="lp-compare-row">
                  <p><small>Today</small>{before}</p>
                  <p><small>With datawazi</small>{after}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-wrap lp-split">
            <div>
              <p className="lp-kicker">Trust</p>
              <h2>Numbers you can stand behind.</h2>
              <p className="lp-sub">Built for the meeting where someone asks where a number came from.</p>
            </div>
            <ol className="lp-ledger">
              {TRUST.map(([head, text], i) => (
                <li key={head}>
                  <span className="lp-no">0{i + 1}</span>
                  <h3>{head}</h3>
                  <p>{text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="powerbi" className="lp-section lp-tint">
          <div className="lp-wrap">
            <div className="lp-head">
              <div>
                <p className="lp-kicker">Power&nbsp;BI</p>
                <h2>Bring your Power&nbsp;BI work. Go further here.</h2>
              </div>
            </div>
            <div className="lp-pbi">
              <div className="lp-cell">
                <p className="lp-kicker">Import</p>
                <h3>Bring a Power&nbsp;BI file</h3>
                <p>Import a .pbix file or a .pbip project. Tables, relationships and measures come across, and DAX it can't translate is kept, not guessed.</p>
                <ul className="lp-files" aria-hidden="true">
                  <li><span>✓</span>Sales.SemanticModel</li>
                  <li><span>✓</span>Sales.Report</li>
                  <li><span>✓</span>Sales.pbip</li>
                </ul>
              </div>
              <div className="lp-cell">
                <p className="lp-kicker">Beyond</p>
                <h3>Not limited to Power&nbsp;BI</h3>
                <p>Once your work is here, ask questions in plain language, get summaries checked against the data, and use statistics like medians and spread in any measure.</p>
              </div>
            </div>
          </div>
        </section>

        <Pricing />

        <section className="lp-section lp-tint">
          <div className="lp-wrap lp-split">
            <div>
              <p className="lp-kicker">Questions</p>
              <h2>Asked before signing up.</h2>
            </div>
            <div className="lp-faq">
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
          <div className="lp-wrap lp-final-row">
            <h2>Your next report, checked and ready by lunch.</h2>
            <div className="lp-ctas">
              <Link to="/sign-in" className="lp-btn lp-btn-light lp-btn-lg">Start free</Link>
              <a href="#how" className="lp-btn lp-btn-ghost lp-btn-lg">See how it works</a>
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
