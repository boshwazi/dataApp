import { useState } from "react";
import { CheckCircle2, FileBarChart2, TriangleAlert } from "lucide-react";
import { powerbiApi, type PowerBIImport } from "../api";

/** What the last Power BI import brought across, what was split, and what was left behind and why. */
export function PowerBIPanel({ projectId, info, onChanged }: { projectId: string; info: PowerBIImport; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const report = info.report;
  const added = report?.relationships.filter((r) => r.added) ?? [];
  const skipped = report?.relationships.filter((r) => !r.added) ?? [];

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      await powerbiApi.finish(projectId);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card powerbi-panel" aria-label="Power BI import">
      <header>
        <FileBarChart2 size={18} aria-hidden="true" />
        <div>
          <strong>Imported from {info.file}</strong>
          <span className="muted small">
            Power BI {info.kind === "pbix" ? "file" : "project"}, {new Date(info.imported_at).toLocaleString()}
          </span>
        </div>
      </header>

      {info.waiting_for.length > 0 && (
        <div className="notice small">
          <p>
            <strong>{info.waiting_for.length === 1 ? "1 table needs" : `${info.waiting_for.length} tables need`} data:</strong>{" "}
            {info.waiting_for.join(", ")}. A Power BI project holds the model but not the data.
          </p>
          <p>
            Load each table's clean data under the same name, then finish the import. Or save the project as a .pbix in
            Power BI Desktop and import that instead: it carries the data.
          </p>
          <button onClick={finish} disabled={busy}>
            {busy ? "Adding the model…" : "Finish import"}
          </button>
        </div>
      )}
      {error && <p className="bad small">{error}</p>}

      {report && (
        <>
          <ul className="powerbi-summary">
            <li>
              <CheckCircle2 size={15} aria-hidden="true" className="ok" /> {report.measures.length} measures converted
              {report.split.length > 0 && `, ${report.split.length} split into helper measures`}
            </li>
            <li>
              <CheckCircle2 size={15} aria-hidden="true" className="ok" /> {added.length} relationships added
            </li>
            {report.not_converted.length > 0 && (
              <li>
                <TriangleAlert size={15} aria-hidden="true" className="warn-icon" /> {report.not_converted.length} measures
                kept as Power BI only
              </li>
            )}
          </ul>
          {report.notes.map((note) => (
            <p key={note} className="muted small">
              {note}
            </p>
          ))}
          {report.split.length > 0 && (
            <details>
              <summary>Split measures</summary>
              <ul>
                {report.split.map((s) => (
                  <li key={s.helper}>
                    <strong>{s.measure}</strong> uses a helper measure, <span className="ident">{s.helper}</span>, for the part
                    that adds up rows.
                  </li>
                ))}
              </ul>
            </details>
          )}
          {report.not_converted.length > 0 && (
            <details open>
              <summary>Not converted yet</summary>
              <ul className="powerbi-left">
                {report.not_converted.map((m) => (
                  <li key={m.measure}>
                    <strong>{m.measure}</strong>
                    <span className="small">{m.why}</span>
                    <code>{m.dax}</code>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {(added.length > 0 || skipped.length > 0) && (
            <details>
              <summary>Relationships</summary>
              <ul>
                {added.map((r) => (
                  <li key={r.relationship}>
                    <span className="ident">{r.relationship}</span>
                    {typeof r.match === "number" && <span className="muted small"> {Math.round(r.match * 100)}% match</span>}
                    {r.note && <span className="muted small"> {r.note}</span>}
                  </li>
                ))}
                {skipped.map((r) => (
                  <li key={r.relationship} className="bad-line">
                    <span className="ident">{r.relationship}</span> <span className="small">{r.why}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
