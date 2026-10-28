import { useEffect, useRef, useState, type DragEvent, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, MoreHorizontal, Pencil, Plus, RefreshCw, Sparkles, Table2, Trash2, Upload, X } from "lucide-react";
import { api, powerbiApi, type AddResult, type ColumnProfile, type LinkCatalogue, type Preview, type Source, type SourceSummary } from "../api";
import { Menu, useDialogs } from "../components/ui";
import { PowerBIPanel } from "../components/PowerBIPanel";
import { useDataset } from "./Dataset";

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xlsm";
const PAGE_SIZE = 100;

const ORIGIN_LABEL = { csv: "CSV", excel: "Excel", gsheet: "Google Sheets", dataset: "Linked", powerbi: "Power BI", link: "Shared link" } as const;
const PROVIDER_LABEL: Record<string, string> = { onedrive: "OneDrive", sharepoint: "SharePoint", gdrive: "Google Drive" };

export function GetData() {
  const { project, refresh } = useDataset();
  const [params, setParams] = useSearchParams();
  const [hasKey, setHasKey] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [messages, setMessages] = useState<string[]>([]);
  const [describing, setDescribing] = useState<Set<string>>(new Set());
  const [describeErrors, setDescribeErrors] = useState<Record<string, string>>({});
  const [detailVersion, setDetailVersion] = useState(0);
  const [adding, setAdding] = useState(false);

  const selectedId = params.get("source") ?? project.sources[0]?.id ?? null;
  const select = (id: string | null) => setParams(id ? { source: id } : {}, { replace: true });

  useEffect(() => {
    api.getSettings().then((s) => setHasKey(s.has_api_key)).catch(() => setHasKey(false));
  }, []);

  async function describe(ids: string[]) {
    if (!hasKey) return;
    setDescribing((current) => new Set([...current, ...ids]));
    await Promise.all(
      ids.map(async (id) => {
        try {
          await api.describe(project.id, id);
          setDescribeErrors(({ [id]: _, ...rest }) => rest);
        } catch (e) {
          setDescribeErrors((current) => ({ ...current, [id]: (e as Error).message }));
        } finally {
          setDescribing((current) => {
            const next = new Set(current);
            next.delete(id);
            return next;
          });
        }
      }),
    );
    await refresh();
    setDetailVersion((v) => v + 1);
  }

  async function add(label: string, action: () => Promise<AddResult>) {
    setBusy(label);
    setMessages([]);
    try {
      const result = await action();
      setMessages(result.errors);
      await refresh();
      if (result.added.length) {
        select(result.added[0].id);
        void describe(result.added.map((s) => s.id));
      }
    } catch (e) {
      setMessages([(e as Error).message]);
    } finally {
      setBusy(null);
    }
  }

  async function removed() {
    const next = await refresh();
    select(next.sources[0]?.id ?? null);
  }

  const empty = project.sources.length === 0;
  const notes = (
    <>
      {messages.map((m) => (
        <p key={m} className="bad small" role="alert">
          {m}
        </p>
      ))}
    </>
  );

  // Nothing loaded yet: adding data is the only job, so it fills the page.
  if (empty) {
    return (
      <div className="page-scroll">
        <div className="page narrow-page">
          <header className="page-header">
            <div>
              <h1>Add data</h1>
              <p className="muted">
                Load the tables this dataset is built from. Each one is profiled as it loads: types, blanks, ranges and
                data quality issues.
              </p>
            </div>
          </header>
          <section className="card add-data-card">
            <AddData busy={busy} onAdd={add} projectId={project.id} />
            {notes}
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="editor getdata-editor">
      {adding && (
        <AddDataDialog onClose={() => setAdding(false)}>
          <AddData
            busy={busy}
            onAdd={(label, action) => {
              setAdding(false);
              add(label, action);
            }}
            projectId={project.id}
          />
        </AddDataDialog>
      )}
      <aside className="pane pane-left" aria-label="Tables">
        <div className="pane-head">
          <span>Tables</span>
          <span className="pane-count">{project.sources.length}</span>
          <span className="grow" />
          <button className="tool pane-add" onClick={() => setAdding(true)} disabled={busy !== null}>
            <Plus size={15} aria-hidden="true" /> {busy ? "Adding…" : "Add data"}
          </button>
        </div>
        <ul className="source-list" aria-label="Tables in this dataset">
          {project.sources.map((source) => (
            <SourceItem
              key={source.id}
              source={source}
              selected={source.id === selectedId}
              describing={describing.has(source.id)}
              onSelect={() => select(source.id)}
            />
          ))}
        </ul>
        <div className="pane-foot">
          {notes}
          {project.powerbi && <PowerBIPanel projectId={project.id} info={project.powerbi} onChanged={() => void refresh()} />}
          {hasKey ? (
            <Link className="autopilot-line" to="../autopilot">
              <Sparkles size={14} aria-hidden="true" />
              <span>
                <strong>Autopilot</strong> cleans, models and publishes this, then drafts a dashboard
              </span>
            </Link>
          ) : (
            <p className="muted small">Add an API key in Settings and the AI describes each table as it loads.</p>
          )}
        </div>
      </aside>

      <section className="pane-center source-detail" aria-label="Selected table">
        {selectedId && (
          <SourceDetail
            key={`${selectedId}-${detailVersion}`}
            projectId={project.id}
            sourceId={selectedId}
            hasKey={hasKey}
            describing={describing.has(selectedId)}
            describeError={describeErrors[selectedId]}
            onDescribe={() => describe([selectedId])}
            onChanged={refresh}
            onRemoved={removed}
          />
        )}
      </section>
    </div>
  );
}

/** Adding data as a dialog over the page, so it's there when wanted and out of the way otherwise. */
function AddDataDialog({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className="dialog add-data-dialog"
      aria-labelledby="add-data-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="dialog-head">
        <h2 id="add-data-title">Add data</h2>
        <button className="icon" aria-label="Close" onClick={onClose}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      {children}
    </dialog>
  );
}

function SourceItem(props: { source: SourceSummary; selected: boolean; describing: boolean; onSelect: () => void }) {
  const { source } = props;
  return (
    <li>
      <button
        className={`source-item ${props.selected ? "selected" : ""}`}
        onClick={props.onSelect}
        aria-current={props.selected ? "true" : undefined}
      >
        <span className="source-line">
          <Table2 size={14} aria-hidden="true" className="source-icon" />
          <span className="source-name" title={source.table}>
            {source.table}
          </span>
          <span className="source-rows">{source.rows.toLocaleString()}</span>
        </span>
        <span className="source-meta">
          <span className="muted">
            {ORIGIN_LABEL[source.origin.kind]}, {source.columns} columns
          </span>
          {source.update_available && <span className="badge warn">update</span>}
          {source.issue_count > 0 && (
            <span className="badge warn">
              {source.issue_count} {source.issue_count === 1 ? "issue" : "issues"}
            </span>
          )}
          {props.describing ? <span className="badge">AI…</span> : source.has_ai && <span className="badge ai">AI</span>}
        </span>
      </button>
    </li>
  );
}

function AddData(props: { projectId: string; busy: string | null; onAdd: (label: string, action: () => Promise<AddResult>) => void }) {
  const { projectId, busy, onAdd } = props;
  const [link, setLink] = useState("");
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const pbiInput = useRef<HTMLInputElement>(null);

  function importPowerBI(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    onAdd("powerbi", async () => {
      const result = await powerbiApi.import(projectId, file);
      return { added: result.added, errors: [] };
    });
  }

  function upload(files: FileList | null) {
    if (!files?.length) return;
    const list = Array.from(files);
    onAdd("upload", () => api.uploadFiles(projectId, list));
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    upload(event.dataTransfer.files);
  }

  function addSheet(event: FormEvent) {
    event.preventDefault();
    onAdd("gsheet", async () => {
      const result = await api.addSharedLink(projectId, link);
      setLink("");
      return result;
    });
  }

  return (
    <div className="add-data">
      <h3 className="add-data-label">From your computer</h3>
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      >
        <Upload size={18} aria-hidden="true" />
        <span>
          <strong>{busy === "upload" ? "Loading…" : "Drop CSV or Excel files here"}</strong>
          <span className="muted small">or click to choose them</span>
        </span>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => {
            upload(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      <h3 className="add-data-label">From a shared link</h3>
      <form onSubmit={addSheet} className="sheet-form">
        <input
          value={link}
          onChange={(e) => setLink(e.target.value)}
          placeholder="https://docs.google.com/spreadsheets/…"
          aria-label="Shared link to a Google Sheet or a file"
          aria-describedby="shared-link-hint"
        />
        <button type="submit" className="secondary" disabled={!link.trim() || busy !== null}>
          {busy === "gsheet" ? "Loading…" : "Add"}
        </button>
      </form>
      <p className="muted small" id="shared-link-hint">
        A Google Sheet, or a OneDrive, SharePoint or Google Drive file anyone with the link can view. It can be pulled on a
        schedule.
      </p>
      <h3 className="add-data-label">Other ways</h3>
      <div className="add-data-more">
        <LinkPicker projectId={projectId} busy={busy} onAdd={onAdd} />
        <button className="link" disabled={busy !== null} onClick={() => pbiInput.current?.click()}>
          {busy === "powerbi" ? "Reading the Power BI model…" : "Import a Power BI file (.pbix, or .pbip as .zip)"}
        </button>
      <input
        ref={pbiInput}
        type="file"
        accept=".pbix,.zip"
        hidden
        onChange={(e) => {
          importPowerBI(e.target.files);
          e.target.value = "";
        }}
      />
        <button className="link" disabled={busy !== null} onClick={() => onAdd("sample", () => api.addSample(projectId))}>
          {busy === "sample" ? "Loading sample data…" : "Load the sample data"}
        </button>
      </div>
    </div>
  );
}

type DetailProps = {
  projectId: string;
  sourceId: string;
  hasKey: boolean;
  describing: boolean;
  describeError?: string;
  onDescribe: () => void;
  onChanged: () => Promise<unknown>;
  onRemoved: () => void;
};

function SourceDetail(props: DetailProps) {
  const { projectId, sourceId } = props;
  const [source, setSource] = useState<Source | null>(null);
  const [tab, setTab] = useState<"profile" | "preview">("profile");
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const dialogs = useDialogs();

  useEffect(() => {
    api.getSource(projectId, sourceId).then(setSource).catch((e) => setError(e.message));
  }, [projectId, sourceId]);

  async function run(action: () => Promise<void>) {
    setWorking(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }

  const rename = () =>
    run(async () => {
      const name = await dialogs.prompt({ title: "Rename table", label: "Table name", initial: source!.table, confirmLabel: "Rename" });
      if (!name || name === source!.table) return;
      setSource(await api.renameSource(projectId, sourceId, name));
      await props.onChanged();
    });

  const remove = () =>
    run(async () => {
      const ok = await dialogs.confirm({
        title: `Remove ${source!.table}?`,
        body: "The loaded data and its cleaning steps are deleted from this dataset.",
        confirmLabel: "Remove table",
        danger: true,
      });
      if (!ok) return;
      await api.deleteSource(projectId, sourceId);
      props.onRemoved();
    });

  const reprofile = () =>
    run(async () => {
      setSource(await api.reprofile(projectId, sourceId));
      await props.onChanged();
      props.onDescribe();
    });

  if (!source) return <p className={error ? "bad" : "muted"}>{error ?? "Loading…"}</p>;

  const { profile, ai } = source;
  const issues = profile.columns.reduce((n, c) => n + c.issues.length, 0);
  const origin =
    source.origin.kind === "gsheet"
      ? "Google Sheet"
      : source.origin.kind === "link"
        ? `${source.origin.file ?? "A file"} from a shared ${PROVIDER_LABEL[source.origin.provider ?? ""] ?? ""} link`
      : source.origin.kind === "dataset"
        ? `Linked from ${source.origin.dataset_name} v${source.origin.version}`
        : source.origin.kind === "powerbi"
          ? `From ${source.origin.file}.pbix (Power BI, ${source.origin.connector ?? "unknown source"})` +
            (source.origin.snapshot ? `: a snapshot as of ${new Date(source.origin.snapshot).toLocaleDateString()}` : "")
          : source.origin.file;

  return (
    <>
      <header className="page-header detail-header">
        <div className="page-header-text">
          <h1 title={source.table}>{source.table}</h1>
          <p className="muted small">
            {origin}
            {source.origin.sample && " (sample)"}
          </p>
          <dl className="fact-row">
            <div>
              <dt>Rows</dt>
              <dd>{profile.rows.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Columns</dt>
              <dd>{profile.columns.length}</dd>
            </div>
            <div>
              <dt>Issues</dt>
              <dd className={issues ? "warn-text" : ""}>{issues}</dd>
            </div>
            {profile.duplicate_rows > 0 && (
              <div>
                <dt>Duplicate rows</dt>
                <dd className="warn-text">{profile.duplicate_rows}</dd>
              </div>
            )}
          </dl>
        </div>
        <div className="page-actions">
          <Menu
            label="More actions for this table"
            icon={<MoreHorizontal size={16} aria-hidden="true" />}
            items={[
              { label: "Rename", icon: <Pencil size={15} aria-hidden="true" />, onSelect: () => void rename() },
              { label: "Profile again", icon: <RefreshCw size={15} aria-hidden="true" />, onSelect: () => void reprofile() },
              { label: "Remove table", icon: <Trash2 size={15} aria-hidden="true" />, onSelect: () => void remove(), danger: true },
            ]}
          />
          <Link className="button" to={`../transform?table=${encodeURIComponent(source.table)}`} aria-disabled={working}>
            Clean in Transform <ArrowRight size={15} aria-hidden="true" />
          </Link>
        </div>
      </header>

      {error && <p className="bad">{error}</p>}
      {source.warnings.map((w) => (
        <div key={w} className="notice small">
          {w}
        </div>
      ))}

      <AISummary
        ai={ai}
        hasKey={props.hasKey}
        describing={props.describing}
        error={props.describeError}
        onDescribe={props.onDescribe}
      />

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "profile"} onClick={() => setTab("profile")}>
          Column profile
        </button>
        <button role="tab" aria-selected={tab === "preview"} onClick={() => setTab("preview")}>
          Preview
        </button>
      </div>
      {tab === "profile" ? (
        <ProfileTable columns={profile.columns} rows={profile.rows} ai={ai} />
      ) : (
        <PreviewTable projectId={projectId} sourceId={sourceId} />
      )}
    </>
  );
}

function AISummary(props: {
  ai: Source["ai"];
  hasKey: boolean;
  describing: boolean;
  error?: string;
  onDescribe: () => void;
}) {
  if (props.describing) return <div className="ai-card muted">The AI is profiling this table…</div>;
  if (props.ai) {
    return (
      <div className="ai-card">
        <span className="badge ai" title={`Written by ${props.ai.model}`}>
          AI
        </span>
        <div>
          <p>{props.ai.summary}</p>
          <p className="muted small">
            One row = {props.ai.grain}
            {props.ai.table_role !== "unknown" && ` · looks like a ${props.ai.table_role} table`}
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="ai-offer-row">
      {props.hasKey ? (
        <>
          <button className="ai-button" onClick={props.onDescribe}>
            <Sparkles size={15} aria-hidden="true" /> {props.error ? "Describe again" : "Describe with AI"}
          </button>
          <span className="muted small">What one row is, and what the table is for.</span>
        </>
      ) : (
        <span className="muted small">Add an API key in Settings and the AI will describe this table.</span>
      )}
      {props.error && <p className="bad small">{props.error}</p>}
    </div>
  );
}

function formatValue(value: number | string | null, type: ColumnProfile["detected_type"]) {
  if (value === null) return "";
  if (type === "date" && typeof value === "string") return value.replace("T00:00:00", "");
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return value;
}

export function ProfileTable({ columns, rows, ai }: { columns: ColumnProfile[]; rows: number; ai: Source["ai"] }) {
  return (
    <div className="table-wrap">
      <table className="grid profile">
        <thead>
          <tr>
            <th>Column</th>
            <th>Type</th>
            <th className="num">Filled</th>
            <th className="num">Distinct</th>
            <th>Range / common values</th>
            <th>Issues</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => {
            const aiColumn = ai?.columns[c.name];
            const filledShare = rows ? c.filled / rows : 0;
            return (
              <tr key={c.name}>
                <td>
                  <strong>{c.name}</strong>
                  {aiColumn && (
                    <div className="muted small">
                      <span className="role">{aiColumn.role}</span> {aiColumn.description}
                    </div>
                  )}
                </td>
                <td>
                  <span className={`type type-${c.detected_type}`}>{c.detected_type}</span>
                  {c.type_confidence < 1 && <span className="muted small"> {Math.round(c.type_confidence * 100)}%</span>}
                  {c.unique && <div className="muted small">unique</div>}
                </td>
                <td className="num">
                  <div>{Math.round(filledShare * 100)}%</div>
                  <div className="bar">
                    <span style={{ width: `${filledShare * 100}%` }} />
                  </div>
                </td>
                <td className="num">{c.distinct.toLocaleString()}</td>
                <td className="small">
                  {c.min !== null ? (
                    <>
                      {formatValue(c.min, c.detected_type)} – {formatValue(c.max, c.detected_type)}
                    </>
                  ) : (
                    c.top_values.slice(0, 3).map((t) => (
                      <div key={t.value} className="top-value">
                        <span>{t.value}</span> <span className="muted">×{t.count}</span>
                      </div>
                    ))
                  )}
                </td>
                <td className="small">
                  {[...c.issues, ...(aiColumn?.issues ?? []).map((i) => `AI: ${i}`)].map((issue) => (
                    <div key={issue} className="issue">
                      {issue}
                    </div>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PreviewTable({ projectId, sourceId }: { projectId: string; sourceId: string }) {
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.preview(projectId, sourceId, offset, PAGE_SIZE).then(setPreview).catch((e) => setError(e.message));
  }, [projectId, sourceId, offset]);

  if (error) return <p className="bad">{error}</p>;
  if (!preview) return <p className="muted">Loading…</p>;

  const last = Math.min(offset + PAGE_SIZE, preview.total);
  return (
    <>
      <div className="table-wrap">
        <table className="grid preview">
          <thead>
            <tr>
              <th className="num muted">#</th>
              {preview.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, i) => (
              <tr key={offset + i}>
                <td className="num muted">{offset + i + 1}</td>
                {row.map((value, j) => (
                  <td key={j}>{value === null ? <span className="null">null</span> : value}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <span className="muted small">
          Rows {preview.total ? offset + 1 : 0}–{last} of {preview.total.toLocaleString()}
        </span>
        <button className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </button>
        <button className="secondary" disabled={last >= preview.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
          Next
        </button>
      </div>
    </>
  );
}

/** Link a clean table from another dataset's published version, certified datasets first. */
function LinkPicker(props: { projectId: string; busy: string | null; onAdd: (label: string, action: () => Promise<AddResult>) => void }) {
  const [catalogue, setCatalogue] = useState<LinkCatalogue | null>(null);
  const [choice, setChoice] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (open && catalogue === null) api.linkCatalogue(props.projectId).then(setCatalogue).catch(() => setCatalogue([]));
  }, [open, catalogue, props.projectId]);

  if (!open) {
    return (
      <button className="link" onClick={() => setOpen(true)}>
        Link a table from another dataset
      </button>
    );
  }
  if (catalogue === null) return <p className="muted small">Loading datasets…</p>;
  if (catalogue.length === 0) return <p className="muted small">No other published datasets to link from yet.</p>;
  const [datasetId, table] = choice.split("|");
  return (
    <div className="link-picker">
      <select value={choice} onChange={(e) => setChoice(e.target.value)} aria-label="Table to link">
        <option value="">Choose a table…</option>
        {catalogue.map((d) => (
          <optgroup key={d.dataset_id} label={`${d.certified ? "✓ " : ""}${d.dataset_name} (v${d.version})`}>
            {d.tables.map((t) => (
              <option key={t.query_id} value={`${d.dataset_id}|${t.query_id}`}>
                {t.name} · {t.rows.toLocaleString()} rows
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <button
        className="secondary"
        disabled={!choice || props.busy !== null}
        onClick={() => props.onAdd("link", () => api.linkTable(props.projectId, datasetId, table))}
      >
        {props.busy === "link" ? "Linking…" : "Link"}
      </button>
      <p className="muted small">A read-only copy that updates when that dataset publishes again (via Refresh).</p>
    </div>
  );
}
