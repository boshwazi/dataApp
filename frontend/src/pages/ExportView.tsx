import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { exportApi, reportApi, type ReportState } from "../api";
import { ChartVisual, KpiCard } from "./Dashboard";
import { applyTheme } from "../components/theme";
import { BrandMark } from "../components/ui";
import { formatDay } from "../i18n";

type Bullet = { text: string; unmatched: number[] };

/**
 * A print-ready copy of a published dashboard: a cover, an optional AI summary, then one A4
 * landscape sheet per page. The browser's "Save as PDF" turns it into vector PDF.
 */
export function ExportView() {
  const { dashboardId = "" } = useParams();
  const [state, setState] = useState<ReportState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<Bullet[] | null>(null);
  const [summarising, setSummarising] = useState(false);

  // A printed page is always light, whatever theme the app is in.
  useEffect(() => {
    document.documentElement.dataset.theme = "light";
    window.dispatchEvent(new Event("themechange"));
    return () => applyTheme();
  }, []);

  useEffect(() => {
    reportApi
      .state(dashboardId, "published")
      .then((s) => {
        setState(s);
        document.title = `${s.name} — datawazi`;
      })
      .catch((e) => setError(e.message));
  }, [dashboardId]);

  async function addSummary() {
    setSummarising(true);
    setError(null);
    try {
      setSummary((await exportApi.summary(dashboardId)).bullets);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSummarising(false);
    }
  }

  if (!state) return <p className={error ? "bad" : "muted"}>{error ?? "Preparing the export…"}</p>;
  if (!state.published || state.pages.length === 0) {
    return (
      <div className="export-empty">
        <p>This dashboard has no published version to export yet.</p>
        <Link to={`/dashboards/${dashboardId}`}>Back to the dashboard</Link>
      </div>
    );
  }

  const exported = new Date().toLocaleString();
  return (
    <div className="export">
      <div className="export-toolbar no-print">
        <Link to={`/dashboards/${dashboardId}?view=published`}>← Back</Link>
        <span className="spacer" />
        {!summary && (
          <button className="secondary" onClick={addSummary} disabled={summarising}>
            {summarising ? "Writing summary…" : "Add AI executive summary"}
          </button>
        )}
        <button onClick={() => window.print()}>Save as PDF</button>
        <span className="muted small">In the print dialog, choose "Save as PDF".</span>
      </div>
      {error && <p className="bad no-print">{error}</p>}

      <section className="export-sheet export-cover">
        <div className="brand-line">
          <BrandMark size={20} /> datawazi
        </div>
        <h1>{state.name}</h1>
        <dl>
          <dt>Data as of</dt>
          <dd>{state.as_of ?? "unknown"}</dd>
          <dt>Dataset</dt>
          <dd>
            {state.dataset?.name} (version {state.dataset?.version})
          </dd>
          <dt>Published</dt>
          <dd>{state.published_at ? new Date(state.published_at).toLocaleString() : "—"}</dd>
          <dt>Exported</dt>
          <dd>{exported}</dd>
        </dl>
        {summary && (
          <div className="export-summary">
            <h2>Executive summary</h2>
            <ul>
              {summary.map((b, i) => (
                <li key={i}>
                  {b.text}
                  {b.unmatched.length > 0 && (
                    <span className="unverified"> (unverified: {b.unmatched.join(", ")})</span>
                  )}
                </li>
              ))}
            </ul>
            <p className="muted small">Written by AI from the dashboard's data; numbers are checked against it.</p>
          </div>
        )}
        <div className="export-contents">
          <h2>Contents</h2>
          <ol>
            {state.pages.map((page) => (
              <li key={page.id}>
                <strong>{page.title}</strong>
                {page.summary && <p className="muted">{page.summary}</p>}
              </li>
            ))}
          </ol>
        </div>
      </section>

      {state.pages.map((page, n) => (
        <section key={page.id} className="export-sheet">
          <header className="export-page-head">
            <h2>{page.title}</h2>
            <span className="muted small">
              {state.name} · page {n + 1} of {state.pages.length} · data as of {state.as_of ? formatDay(state.as_of) : "unknown"}
            </span>
          </header>
          {page.summary && <p className="page-summary">{page.summary}</p>}
          <div className="kpi-strip">
            {page.visuals
              .filter((v) => v.type === "kpi")
              .map((v) => (
                <article key={v.id} className="visual">
                  <h3 className="export-visual-title">{v.title}</h3>
                  {v.error ? <p className="bad small">{v.error}</p> : <KpiCard visual={v} format={state.formats[v.measures[0]]} />}
                  {v.insight && <p className="insight">{v.insight.text}</p>}
                </article>
              ))}
          </div>
          <div className="visual-grid">
            {page.visuals
              .filter((v) => v.type !== "kpi")
              .map((v) => (
                <article key={v.id} className={`visual ${v.width === "full" ? "full" : ""}`}>
                  <h3 className="export-visual-title">{v.title}</h3>
                  {v.error ? <p className="bad small">{v.error}</p> : <ChartVisual visual={v} formats={state.formats} />}
                  {v.insight && (
                    <p className="insight">
                      {v.insight.text}
                      {!v.insight.check.verified && <span className="unverified"> (unverified)</span>}
                    </p>
                  )}
                </article>
              ))}
          </div>
        </section>
      ))}
    </div>
  );
}
