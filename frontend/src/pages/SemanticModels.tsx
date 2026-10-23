import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Boxes, Plus, Sparkles } from "lucide-react";
import { api, modelApi, semanticApi, type Dataset, type LinkCatalogue, type SemanticResult } from "../api";
import { useCrumbs, useToast } from "../components/ui";

type Mode = "merge" | "tables";
type Built = SemanticResult & { ai: "running" | "done" | "skipped" | string; added: number };

/** Semantic models: one model over tables from several published datasets, built by picking tables or merging. */
export function SemanticModels() {
  useCrumbs([{ label: "Semantic models" }]);
  const toast = useToast();
  const [catalogue, setCatalogue] = useState<LinkCatalogue | null>(null);
  const [models, setModels] = useState<Dataset[] | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [mode, setMode] = useState<Mode>("merge");
  const [name, setName] = useState("");
  const [datasets, setDatasets] = useState<Set<string>>(new Set());
  const [tables, setTables] = useState<Set<string>>(new Set()); // "datasetId:queryId"
  const [busy, setBusy] = useState(false);
  const [built, setBuilt] = useState<Built | null>(null);
  const [opened, setOpened] = useState(false); // the builder, once there are models to list first

  function load() {
    semanticApi.catalogue().then(setCatalogue).catch((e) => toast(e.message, "error"));
    api.listDatasets().then((all) => setModels(all.filter((d) => d.semantic))).catch((e) => toast(e.message, "error"));
  }

  useEffect(() => {
    load();
    api.getSettings().then((s) => setHasKey(s.has_api_key)).catch(() => setHasKey(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const picked = mode === "merge" ? datasets.size : tables.size;
  const ready = name.trim() && (mode === "merge" ? datasets.size >= 2 : tables.size >= 1);
  const tableCount = useMemo(
    () => (catalogue ?? []).filter((d) => datasets.has(d.dataset_id)).reduce((n, d) => n + d.tables.length, 0),
    [catalogue, datasets],
  );

  function toggle(set: Set<string>, key: string, update: (next: Set<string>) => void) {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    update(next);
  }

  async function build() {
    setBusy(true);
    setBuilt(null);
    try {
      const result =
        mode === "merge"
          ? await semanticApi.merge(name.trim(), [...datasets])
          : await semanticApi.build(
              name.trim(),
              [...tables].map((key) => {
                const [dataset_id, table] = key.split(":");
                return { dataset_id, table };
              }),
            );
      setBuilt({ ...result, ai: hasKey ? "running" : "skipped", added: 0 });
      load();
      if (hasKey) {
        // Relationships across the source models: proposed by the Model AI, checked, left for review.
        try {
          const run = await modelApi.ai(result.dataset.id);
          const added = run.added.filter((a) => a.kind === "relationship").length;
          setBuilt((b) => b && { ...b, ai: "done", added });
        } catch (e) {
          setBuilt((b) => b && { ...b, ai: (e as Error).message });
        }
      }
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page wide semantic">
      <div className="home-head">
        <h1>Semantic models</h1>
        <span className="spacer" />
        {!!models?.length && !opened && (
          <button onClick={() => setOpened(true)}>
            <Plus size={16} aria-hidden="true" /> New semantic model
          </button>
        )}
      </div>
      <p className="muted semantic-lead">
        One model over tables from several published datasets. Nothing is uploaded again: each table is linked, and follows its dataset
        when that dataset publishes. Relationships, the date table and measures come along; dashboards use it like any dataset.
      </p>

      {built && <BuiltCard built={built} />}

      {!!models?.length && (
      <section>
        <h2 className="section-title">Your semantic models</h2>
        {models === null ? null : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Built from</th>
                  <th className="num">Tables</th>
                  <th className="num">Measures</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.id}>
                    <td>
                      <Link to={`/datasets/${m.id}/model`}>{m.name}</Link>
                    </td>
                    <td className="small">{m.semantic?.from.map((f) => f.name).join(", ")}</td>
                    <td className="num">{m.progress.sources}</td>
                    <td className="num">{m.progress.measures}</td>
                    <td className="small">{m.status.published ? `Published v${m.status.version}` : "Not published"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      )}

      {(opened || models?.length === 0) && (
      <section className="card semantic-new">
        <h2 className="section-title">{models?.length ? "New semantic model" : "Make your first semantic model"}</h2>
        <div className="segmented" role="tablist" aria-label="How to build it">
          <button role="tab" aria-selected={mode === "merge"} className={mode === "merge" ? "on" : ""} onClick={() => setMode("merge")}>
            Merge datasets
          </button>
          <button role="tab" aria-selected={mode === "tables"} className={mode === "tables" ? "on" : ""} onClick={() => setMode("tables")}>
            Pick tables
          </button>
        </div>
        <p className="muted small">
          {mode === "merge"
            ? "Every table of each dataset. A table with the same name and columns in two of them (Customers, say) is kept once, so the models meet on it; measures with the same name are renamed."
            : "Only the tables you pick. Relationships and measures come along when their tables do."}
        </p>

        {catalogue === null ? (
          <p className="muted">Loading…</p>
        ) : catalogue.length < (mode === "merge" ? 2 : 1) ? (
          <p className="muted">
            {mode === "merge" ? "Merging needs at least two published datasets." : "Publish a dataset first; its clean tables show up here."}
          </p>
        ) : (
          <ul className="semantic-picks">
            {catalogue.map((d) => (
              <li key={d.dataset_id}>
                {mode === "merge" ? (
                  <label className="check">
                    <input type="checkbox" checked={datasets.has(d.dataset_id)} onChange={() => toggle(datasets, d.dataset_id, setDatasets)} />
                    <span className="pick-name">{d.dataset_name}</span>
                    {d.certified && <span className="badge ok-badge">Certified</span>}
                    <span className="muted small">
                      v{d.version}: {d.tables.map((t) => t.name).join(", ")}
                    </span>
                  </label>
                ) : (
                  <fieldset>
                    <legend>
                      {d.dataset_name} <span className="muted small">v{d.version}</span>
                    </legend>
                    {d.tables.map((t) => {
                      const key = `${d.dataset_id}:${t.query_id}`;
                      return (
                        <label key={key} className="check">
                          <input type="checkbox" checked={tables.has(key)} onChange={() => toggle(tables, key, setTables)} />
                          <span className="pick-name">{t.name}</span>
                          <span className="muted small">
                            {t.rows.toLocaleString()} rows, {t.columns.length} columns
                          </span>
                        </label>
                      );
                    })}
                  </fieldset>
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="semantic-actions">
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Customer 360" />
          </label>
          <button onClick={build} disabled={!ready || busy}>
            <Boxes size={16} aria-hidden="true" />
            {busy
              ? "Building…"
              : mode === "merge"
                ? `Merge ${picked || ""} datasets${tableCount ? ` (${tableCount} tables)` : ""}`.replace("  ", " ")
                : `Build from ${picked || ""} ${picked === 1 ? "table" : "tables"}`.replace("  ", " ")}
          </button>
        </div>
      </section>
      )}

    </div>
  );
}

function BuiltCard({ built }: { built: Built }) {
  const { dataset, notes } = built;
  const lines = [...notes.conformed, ...notes.renamed];
  return (
    <section className="card semantic-built" aria-live="polite">
      <h2>Built {dataset.name}</h2>
      <p className="small">
        {dataset.sources.length} tables linked from {dataset.semantic?.from.map((f) => f.name).join(" and ")}.
      </p>
      {lines.length > 0 && (
        <ul className="small">
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}
      {notes.skipped.length > 0 && (
        <details className="small">
          <summary>{notes.skipped.length} couldn't come along</summary>
          <ul>
            {notes.skipped.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </details>
      )}
      <p className="small semantic-ai">
        <Sparkles size={14} aria-hidden="true" />{" "}
        {built.ai === "running"
          ? "Looking for relationships across the tables…"
          : built.ai === "skipped"
            ? "Add an AI key in Settings and the AI will propose relationships across the tables."
            : built.ai === "done"
              ? built.added
                ? `The AI proposed ${built.added} ${built.added === 1 ? "relationship" : "relationships"} across the tables. Review them in the Model.`
                : "The AI found no further relationships to add."
              : `The AI couldn't look for relationships: ${built.ai}`}
      </p>
      <Link className="button" to={`/datasets/${dataset.id}/model`}>
        Open the model
      </Link>
    </section>
  );
}
