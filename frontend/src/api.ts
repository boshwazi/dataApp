export type ProviderSettings = {
  base_url: string;
  model: string;
  has_api_key: boolean;
  api_key_hint: string;
  api_key_from_env: boolean;
  capabilities: { tools?: boolean; json_mode?: boolean };
};

export type ConnectionReport = {
  ok: boolean;
  latency_ms: number | null;
  reply: string | null;
  supports_tools: boolean | null;
  supports_json_mode: boolean | null;
  error: string | null;
};

export type Origin = {
  kind: "csv" | "excel" | "gsheet";
  label: string;
  file?: string;
  url?: string;
  sample?: boolean;
};

export type SourceSummary = {
  id: string;
  table: string;
  origin: Origin;
  added_at: string;
  rows: number;
  columns: number;
  issue_count: number;
  has_ai: boolean;
};

export type Project = {
  id: string;
  workspace_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  sources: SourceSummary[];
};

export type ColumnProfile = {
  name: string;
  detected_type: "integer" | "decimal" | "date" | "boolean" | "text" | "empty";
  type_confidence: number;
  filled: number;
  blanks: number;
  placeholders: number;
  distinct: number;
  unique: boolean;
  min_length: number | null;
  max_length: number | null;
  min: number | string | null;
  max: number | string | null;
  mean: number | null;
  top_values: { value: string; count: number }[];
  issues: string[];
};

export type AIColumn = {
  description: string;
  role: "id" | "dimension" | "measure" | "date" | "attribute" | "text";
  suggested_type: string;
  issues: string[];
};

export type AIDescription = {
  summary: string;
  grain: string;
  table_role: "fact" | "dimension" | "unknown";
  columns: Record<string, AIColumn>;
  status: "AI" | "Approved";
  model: string;
};

export type Source = {
  id: string;
  table: string;
  origin: Origin;
  added_at: string;
  warnings: string[];
  profile: { rows: number; duplicate_rows: number; columns: ColumnProfile[]; profiled_at: string };
  ai: AIDescription | null;
};

export type Preview = {
  columns: string[];
  rows: (string | null)[][];
  total: number;
  offset: number;
};

export type AddResult = { added: SourceSummary[]; errors: string[] };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const response = await fetch(path, {
    ...init,
    // The browser sets the multipart boundary itself for uploads.
    headers: isForm ? init?.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
  }
  return response.json();
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: JSON.stringify(body ?? {}) });
const project = (id: string) => `/api/projects/${id}`;
const source = (pid: string, sid: string) => `${project(pid)}/sources/${sid}`;

export const api = {
  getSettings: () => request<ProviderSettings>("/api/settings"),
  saveSettings: (update: { base_url?: string; model?: string; api_key?: string }) =>
    request<ProviderSettings>("/api/settings", { method: "PUT", body: JSON.stringify(update) }),
  testConnection: () => request<ConnectionReport>("/api/settings/test", { method: "POST" }),

  listProjects: () => request<Project[]>("/api/projects"),
  createProject: (name: string) => request<Project>("/api/projects", json("POST", { name })),
  getProject: (id: string) => request<Project>(project(id)),
  renameProject: (id: string, name: string) => request<Project>(project(id), json("PATCH", { name })),
  deleteProject: (id: string) => request<unknown>(project(id), { method: "DELETE" }),

  uploadFiles: (pid: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    return request<AddResult>(`${project(pid)}/sources/upload`, { method: "POST", body: form });
  },
  addGoogleSheet: (pid: string, url: string) =>
    request<AddResult>(`${project(pid)}/sources/gsheet`, json("POST", { url })),
  addSample: (pid: string) => request<AddResult>(`${project(pid)}/sources/sample`, { method: "POST" }),

  getSource: (pid: string, sid: string) => request<Source>(source(pid, sid)),
  preview: (pid: string, sid: string, offset = 0, limit = 100) =>
    request<Preview>(`${source(pid, sid)}/preview?offset=${offset}&limit=${limit}`),
  renameSource: (pid: string, sid: string, name: string) => request<Source>(source(pid, sid), json("PATCH", { name })),
  deleteSource: (pid: string, sid: string) => request<unknown>(source(pid, sid), { method: "DELETE" }),
  reprofile: (pid: string, sid: string) => request<Source>(`${source(pid, sid)}/profile`, { method: "POST" }),
  describe: (pid: string, sid: string) => request<Source>(`${source(pid, sid)}/describe`, { method: "POST" }),
};

// Transform

export type LogicalType = "text" | "integer" | "decimal" | "date" | "boolean";

export type Step = {
  id: string;
  op: string;
  params: Record<string, unknown>;
  reason: string;
  status: "AI" | "Approved";
  added_at: string;
  label: string;
};

export type QuerySummary = {
  id: string;
  name: string;
  kind: "source" | "lookup";
  status: "AI" | "Approved" | null;
  reason: string;
  base_table: string | null;
  split_after_step: number | null;
  source_table: string | null;
  steps: Step[];
  error: string | null;
  rows: number | null;
  columns: { name: string; type: LogicalType }[];
  issue_count: number | null;
};

export type TransformState = { queries: QuerySummary[]; can_undo: boolean };

export type QueryDetail = QuerySummary & { profile: Source["profile"] | null };

export type StepRows = {
  columns: { name: string; type: LogicalType }[];
  rows: (string | null)[][];
  total: number;
};

export type StepPreview = {
  step: number;
  before: StepRows | null;
  after: StepRows;
  impact: {
    rows_before: number;
    rows_after: number;
    added_columns: string[];
    removed_columns: string[];
    type_changes: { column: string; from: LogicalType; to: LogicalType }[];
    blank_changes: { column: string; before: number; after: number }[];
  } | null;
};

export type AIRun = TransformState & {
  summary: string;
  added: Step[];
  new_tables: string[];
  rejected: { op: string; reason: string; error: string }[];
};

const tf = (pid: string) => `/api/projects/${pid}/transform`;
const tq = (pid: string, qid: string) => `${tf(pid)}/queries/${qid}`;

export const transformApi = {
  state: (pid: string) => request<TransformState>(tf(pid)),
  query: (pid: string, qid: string) => request<QueryDetail>(tq(pid, qid)),
  preview: (pid: string, qid: string, step: number) => request<StepPreview>(`${tq(pid, qid)}/preview?step=${step}`),
  approve: (pid: string, qid: string, sid: string) =>
    request<TransformState>(`${tq(pid, qid)}/steps/${sid}/approve`, { method: "POST" }),
  remove: (pid: string, qid: string, sid: string) =>
    request<TransformState>(`${tq(pid, qid)}/steps/${sid}`, { method: "DELETE" }),
  move: (pid: string, qid: string, sid: string, to: number) =>
    request<TransformState>(`${tq(pid, qid)}/steps/${sid}/move`, json("POST", { to })),
  rename: (pid: string, qid: string, name: string) => request<TransformState>(tq(pid, qid), json("PATCH", { name })),
  removeTable: (pid: string, qid: string) => request<TransformState>(tq(pid, qid), { method: "DELETE" }),
  approveAll: (pid: string, qid?: string) =>
    request<TransformState>(`${tf(pid)}/approve-all${qid ? `?query_id=${qid}` : ""}`, { method: "POST" }),
  undo: (pid: string) => request<TransformState>(`${tf(pid)}/undo`, { method: "POST" }),
  aiClean: (pid: string, qid: string) => request<AIRun>(`${tq(pid, qid)}/ai`, { method: "POST" }),
};

// Model

export type ModelColumn = { name: string; type: LogicalType; hidden: boolean; key: boolean };

export type ModelTable = {
  id: string;
  name: string;
  kind: "fact" | "dimension" | "date" | "table";
  rows: number;
  columns: ModelColumn[];
};

export type Relationship = {
  id: string;
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  from_name: string;
  to_name: string;
  active: boolean;
  status: "AI" | "Approved";
  reason: string;
  error: string | null;
  checks: {
    rows: number;
    keyed_rows: number;
    matched_rows: number;
    match_rate: number;
    blank_keys: number;
    unmatched_examples: string[];
    cardinality: "many_to_one" | "one_to_one";
  };
};

export type MeasureFormat = { type: "number" | "integer" | "currency" | "percent"; decimals?: number; currency?: string };

export type Measure = {
  id: string;
  name: string;
  kind: "aggregate" | "formula";
  label: string;
  description: string;
  reason: string;
  status: "AI" | "Approved";
  format: MeasureFormat;
  value: number | null;
  error: string | null;
};

export type DateTable = {
  start_year: number | null;
  end_year: number | null;
  status: "AI" | "Approved";
  reason: string;
  error?: string | null;
  first?: string;
  last?: string;
};

export type ModelState = {
  tables: ModelTable[];
  relationships: Relationship[];
  measures: Measure[];
  date_table: DateTable | null;
  can_undo: boolean;
};

export type ModelRun = ModelState & {
  summary: string;
  added: { kind: string; label: string }[];
  refused: { kind: string; label: string; error: string }[];
};

const md = (pid: string) => `/api/projects/${pid}/model`;

export const modelApi = {
  state: (pid: string) => request<ModelState>(md(pid)),
  ai: (pid: string) => request<ModelRun>(`${md(pid)}/ai`, { method: "POST" }),
  approve: (pid: string, itemId?: string) =>
    request<ModelState>(`${md(pid)}/approve${itemId ? `?item_id=${itemId}` : ""}`, { method: "POST" }),
  undo: (pid: string) => request<ModelState>(`${md(pid)}/undo`, { method: "POST" }),
  removeRelationship: (pid: string, id: string) =>
    request<ModelState>(`${md(pid)}/relationships/${id}`, { method: "DELETE" }),
  removeMeasure: (pid: string, id: string) => request<ModelState>(`${md(pid)}/measures/${id}`, { method: "DELETE" }),
  removeDateTable: (pid: string) => request<ModelState>(`${md(pid)}/date-table`, { method: "DELETE" }),
  setHidden: (pid: string, table: string, column: string, hidden: boolean) =>
    request<ModelState>(`${md(pid)}/hidden`, json("POST", { table, column, hidden })),
};

export function formatMeasure(value: number | null, format: MeasureFormat): string {
  if (value === null || value === undefined) return "(blank)";
  const decimals = format.type === "integer" ? 0 : (format.decimals ?? 0);
  const number = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  if (format.type === "percent") return `${number(value * 100)}%`;
  if (format.type === "currency") return `${format.currency ?? "RM"} ${number(value)}`;
  return number(value);
}
