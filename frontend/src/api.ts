import { language } from "./i18n";
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
  kind: "csv" | "excel" | "gsheet" | "dataset" | "powerbi" | "link";
  label: string;
  file?: string;
  url?: string;
  /** Shared links: where the file lives. */
  provider?: "onedrive" | "sharepoint" | "gdrive";
  sample?: boolean;
  dataset_id?: string;
  dataset_name?: string;
  table?: string;
  version?: number;
  /** Power BI imports: the connector Power BI used, its storage mode and when it last refreshed the data. */
  connector?: string;
  mode?: "import" | "directquery" | "dual" | "directlake";
  snapshot?: string | null;
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
  update_available?: boolean;
};

export type LinkCatalogue = {
  dataset_id: string;
  dataset_name: string;
  certified: boolean;
  version: number;
  tables: { query_id: string; name: string; rows: number; columns: string[] }[];
}[];

export type LineageNode = { id: string; kind: "source" | "table" | "measure" | "visual"; label: string; sub: string; column: number };
export type Lineage = { nodes: LineageNode[]; edges: { from: string; to: string; kind: string }[] };

export type PublishStatus = {
  published: boolean;
  version: number | null;
  published_at: string | null;
  unpublished_changes: boolean;
};

export type Dataset = {
  id: string;
  workspace_id: string;
  name: string;
  instructions?: string;
  created_at: string;
  updated_at: string;
  sources: SourceSummary[];
  status: PublishStatus;
  stale: Record<DatasetStage, string | null>;
  certified: boolean;
  /** Built on another dataset: its tables and model are inherited, read-only and kept in step. */
  extends?: { id: string; name: string; version: number };
  /** A semantic model: every table linked from these datasets. */
  semantic?: { how: "merge" | "tables"; from: { id: string; name: string }[]; built_at: string };
  linked_by: { id: string; name: string; tables: string[] }[];
  dashboards: { id: string; name: string }[];
  progress: { sources: number; steps: number; relationships: number; measures: number };
  powerbi: PowerBIImport | null;
};

export type PowerBIReport = {
  relationships: { relationship: string; added: boolean; active?: boolean; match?: number; note?: string | null; why?: string }[];
  measures: { measure: string; dax: string }[];
  split: { measure: string; helper: string }[];
  not_converted: { measure: string; dax: string; why: string }[];
  notes: string[];
};

export type PowerBIImport = {
  file: string;
  kind: "pbix" | "pbip";
  imported_at: string;
  report: PowerBIReport | null;
  waiting_for: string[];
};

export type DatasetStage = "get_data" | "transform" | "model";

export type BrokenVisual = { dashboard: string; dashboard_id: string; version: string; page: string; visual: string; error: string };

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

// Who is calling: the sign-in token and the chosen workspace ride along on every request.
// (downloadFile below uses the same session for files that aren't JSON.)
const session = {
  token: async (): Promise<string | null> => null,
  workspace: null as string | null,
  onSignedOut: () => {},
  /** The workspace requires two-step sign-in and this session hasn't done the second step. */
  onTwoStepRequired: (_message: string) => {},
};

/** Errors a whole screen already explains (two-step sign-in required), so a toast repeating them is skipped. */
export const handledErrors = new Set<string>();

export function configureSession(options: Partial<typeof session>) {
  Object.assign(session, options);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const token = await session.token();
  const who: Record<string, string> = {};
  if (token) who.Authorization = `Bearer ${token}`;
  if (session.workspace) who["X-Workspace"] = session.workspace;
  who["X-Language"] = language; // the AI writes in the person's language
  const response = await fetch(path, {
    ...init,
    // The browser sets the multipart boundary itself for uploads.
    headers: isForm ? { ...who, ...init?.headers } : { "Content-Type": "application/json", ...who, ...init?.headers },
  });
  if (response.status === 401) session.onSignedOut();
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message ?? `Request failed (${response.status})`;
    if (response.status === 403 && detail?.code === "mfa_required") {
      handledErrors.add(message);
      session.onTwoStepRequired(message);
    }
    throw new ApiError(message, response.status, typeof detail === "object" ? detail : null);
  }
  return response.json();
}

/** An API error; `detail` carries structured information such as visuals a publish would break. */
export class ApiError extends Error {
  constructor(message: string, public status: number, public detail: { problems?: BrokenVisual[] } | null) {
    super(message);
  }
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: JSON.stringify(body ?? {}) });
const project = (id: string) => `/api/datasets/${id}`;
const source = (pid: string, sid: string) => `${project(pid)}/sources/${sid}`;

// Sign-in, workspaces and members

export type Role = "admin" | "creator" | "viewer";
export type WorkspaceSettings = { logo?: string; require_mfa?: boolean; domains?: string[]; exported_at?: string; delete_after?: string };
export type WorkspaceRef = { id: string; name: string; role: Role; settings?: WorkspaceSettings };
export type AuthConfig = { mode: "local" | "supabase"; supabase_url: string | null; supabase_key: string | null };
export type Me = { mode: "local" | "supabase"; user: { id: string; email: string }; platform_admin: boolean; workspaces: WorkspaceRef[] };
export type Member = { workspace_id: string; user_id: string; email: string; role: Role; joined_at: string };
export type Invite = { id: string; email: string; role: Role; invited_by: string; created_at: string; expires_at: string };
export type Delivery = { invite: Invite; link: string; emailed: boolean; email_problem: string | null };
export type People = { members: Member[]; invites: Invite[] };

export type Plan = {
  id: "free" | "pro" | "team";
  name: string;
  creators: number | "seats";
  viewers: number | null;
  datasets: number | null;
  refresh: "manual" | "daily" | "hourly";
  credits: number;
  extras: string[];
};
export type UsageRow = { calls: number; prompt_tokens: number; completion_tokens: number; credits: number };
export type Usage = {
  month: string;
  total: UsageRow;
  by_person: (UsageRow & { person: string })[];
  by_feature: (UsageRow & { feature: string })[];
  by_day: (UsageRow & { day: string })[];
};
export type BillingView = {
  enforced: boolean;
  account: {
    plan: Plan["id"];
    status: string;
    seats: number;
    period_end: string | null;
    cancel_at_period_end: boolean;
    payment_method: { brand: string; last4: string } | null;
  };
  plan: Plan;
  plans: Plan[];
  credits: { month: string; used: number; allowance: number; percent: number };
  receipts: Receipt[];
  payments_ready: boolean;
  /** Monthly price in sen (RM 1 = 100) per seat; null until launch. */
  prices: Record<"pro" | "team", number | null>;
  topup: { credits: number; price: number | null };
};
export type Receipt = {
  number: string;
  date: string;
  workspace: string;
  bill_to: string | null;
  currency: string;
  lines: { description: string; quantity: number; unit_price: number; discount: number; amount: number }[];
  subtotal: number;
  tax: { name: string; rate: number; amount: number };
  total: number;
  paid_with: { brand: string; last4: string } | null;
};

export const billingApi = {
  get: () => request<BillingView>("/api/billing"),
  checkout: (body: { kind: "plan" | "topup"; plan?: "pro" | "team"; seats?: number }) =>
    request<{ id: string; checkout_url: string }>("/api/billing/checkout", json("POST", body)),
  settle: (id: string) => request<{ status: string }>(`/api/billing/purchases/${id}/settle`, { method: "POST" }),
  cancel: (cancel: boolean) => request<{ cancel_at_period_end: boolean }>("/api/billing/cancel", json("POST", { cancel })),
  usage: (month?: string) => request<Usage>(`/api/workspaces/current/usage${month ? `?month=${month}` : ""}`),
};

export const accountApi = {
  config: () => request<AuthConfig>("/api/auth/config"),
  me: () => request<Me>("/api/me"),
  deleteAccount: () => request<{ deleted: boolean }>("/api/me", { method: "DELETE" }),
  setSignInRules: (require_mfa: boolean) =>
    request<{ require_mfa: boolean }>("/api/workspaces/current/sign-in", json("PUT", { require_mfa })),
  setDomains: (domains: string[]) => request<{ domains: string[] }>("/api/workspaces/current/domains", json("PUT", { domains })),
  joinable: () => request<{ workspaces: { id: string; name: string }[] }>("/api/workspaces/joinable"),
  join: (id: string) => request<WorkspaceRef>(`/api/workspaces/${id}/join`, { method: "POST" }),
  deleteWorkspace: (confirm: string) =>
    request<{ delete_after: string }>("/api/workspaces/current/delete", json("POST", { confirm })),
  restoreWorkspace: () => request<{ delete_after: null }>("/api/workspaces/current/restore", { method: "POST" }),
  createWorkspace: (name: string) => request<WorkspaceRef>("/api/workspaces", json("POST", { name })),
  setLogo: (image: string) => request<{ logo: string }>("/api/workspaces/current/logo", json("PUT", { image })),
  removeLogo: () => request<{ logo: null }>("/api/workspaces/current/logo", { method: "DELETE" }),
  renameWorkspace: (name: string) => request<WorkspaceRef>("/api/workspaces/current", json("PATCH", { name })),
  instructions: () => request<{ instructions: string }>("/api/workspaces/current/instructions"),
  setInstructions: (instructions: string) =>
    request<{ instructions: string }>("/api/workspaces/current/instructions", json("PUT", { instructions })),
  aiPrivacy: () => request<{ samples: boolean }>("/api/workspaces/current/ai-privacy"),
  setAiPrivacy: (samples: boolean) => request<{ samples: boolean }>("/api/workspaces/current/ai-privacy", json("PUT", { samples })),
  people: () => request<People>("/api/workspaces/current/members"),
  setRole: (userId: string, role: Role) => request<People>(`/api/workspaces/current/members/${userId}`, json("PUT", { role })),
  removeMember: (userId: string) => request<People>(`/api/workspaces/current/members/${userId}`, { method: "DELETE" }),
  invite: (email: string, role: Role) => request<People & Delivery>("/api/workspaces/current/invites", json("POST", { email, role })),
  resendInvite: (id: string) => request<People & Delivery>(`/api/workspaces/current/invites/${id}/resend`, { method: "POST" }),
  revokeInvite: (id: string) => request<People>(`/api/workspaces/current/invites/${id}`, { method: "DELETE" }),
  showInvite: (token: string) =>
    request<{ workspace: string; email: string; role: Role; invited_by: string }>(`/api/invites/${encodeURIComponent(token)}`),
  acceptInvite: (token: string) => request<WorkspaceRef>(`/api/invites/${encodeURIComponent(token)}/accept`, { method: "POST" }),
};

/** Download a file from the API with the session's token, then save it under `filename`. */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const token = await session.token();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (session.workspace) headers["X-Workspace"] = session.workspace;
  const response = await fetch(path, { headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(typeof body.detail === "string" ? body.detail : `Download failed (${response.status})`, response.status, null);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const powerbiApi = {
  import: (pid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ added: SourceSummary[]; report: PowerBIReport | null; waiting_for: string[] }>(
      `/api/datasets/${pid}/sources/powerbi`,
      { method: "POST", body: form },
    );
  },
  refresh: (pid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<RefreshEntry & { not_added: string[] }>(`/api/datasets/${pid}/powerbi/refresh`, { method: "POST", body: form });
  },
  finish: (pid: string) =>
    request<{ report: PowerBIReport; waiting_for: string[] }>(`/api/datasets/${pid}/powerbi/apply`, { method: "POST" }),
  download: (pid: string, name: string) => downloadFile(`/api/datasets/${pid}/powerbi/export`, `${name}.zip`),
};

export const api = {
  getSettings: () => request<ProviderSettings>("/api/settings"),
  saveSettings: (update: { base_url?: string; model?: string; api_key?: string }) =>
    request<ProviderSettings>("/api/settings", { method: "PUT", body: JSON.stringify(update) }),
  testConnection: () => request<ConnectionReport>("/api/settings/test", { method: "POST" }),

  listDatasets: () => request<Dataset[]>("/api/datasets"),
  createDataset: (name: string) => request<Dataset>("/api/datasets", json("POST", { name })),
  getDataset: (id: string) => request<Dataset>(project(id)),
  renameDataset: (id: string, name: string) => request<Dataset>(project(id), json("PATCH", { name })),
  deleteDataset: (id: string) => request<unknown>(project(id), { method: "DELETE" }),
  publishDataset: (id: string, confirm = false) =>
    request<Dataset & { broken_visuals: BrokenVisual[] }>(`${project(id)}/publish${confirm ? "?confirm=true" : ""}`, {
      method: "POST",
    }),

  uploadFiles: (pid: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    return request<AddResult>(`${project(pid)}/sources/upload`, { method: "POST", body: form });
  },
  addSharedLink: (pid: string, url: string) =>
    request<AddResult>(`${project(pid)}/sources/shared-link`, json("POST", { url })),
  refreshAlerts: () => request<RefreshAlert[]>("/api/refresh-alerts"),
  addGoogleSheet: (pid: string, url: string) =>
    request<AddResult>(`${project(pid)}/sources/gsheet`, json("POST", { url })),
  linkCatalogue: (pid: string) => request<LinkCatalogue>(`${project(pid)}/link-catalogue`),
  linkTable: (pid: string, datasetId: string, table: string) =>
    request<AddResult>(`${project(pid)}/sources/link`, json("POST", { dataset_id: datasetId, table })),
  certify: (pid: string, certified: boolean) => request<Dataset>(`${project(pid)}/certify`, json("POST", { certified })),
  extend: (pid: string, name: string) => request<Dataset>(`${project(pid)}/extend`, json("POST", { name })),
  setInstructions: (pid: string, instructions: string) => request<Dataset>(`${project(pid)}/instructions`, json("PUT", { instructions })),
  lineage: (pid: string) => request<Lineage>(`${project(pid)}/lineage`),
  addSample: (pid: string) => request<AddResult>(`${project(pid)}/sources/sample`, { method: "POST" }),

  getSource: (pid: string, sid: string) => request<Source>(source(pid, sid)),
  preview: (pid: string, sid: string, offset = 0, limit = 100) =>
    request<Preview>(`${source(pid, sid)}/preview?offset=${offset}&limit=${limit}`),
  renameSource: (pid: string, sid: string, name: string) => request<Source>(source(pid, sid), json("PATCH", { name })),
  deleteSource: (pid: string, sid: string) => request<unknown>(source(pid, sid), { method: "DELETE" }),
  reprofile: (pid: string, sid: string) => request<Source>(`${source(pid, sid)}/profile`, { method: "POST" }),
  describe: (pid: string, sid: string) => request<Source>(`${source(pid, sid)}/describe`, { method: "POST" }),
  startAutopilot: (pid: string, dashboardName?: string) =>
    request<AutopilotRun>(`${project(pid)}/autopilot`, json("POST", { dashboard_name: dashboardName ?? null })),
  autopilot: (pid: string) => request<{ run: AutopilotRun | null }>(`${project(pid)}/autopilot`),
};

// Autopilot: the whole pipeline in one go

export type AutopilotStepKey = "read" | "clean" | "model" | "publish" | "dashboard";

export type AutopilotRun = {
  id: string;
  status: "running" | "done" | "failed" | "stopped";
  started_at: string;
  finished_at: string | null;
  error: string | null;
  fix_at: "get-data" | "transform" | "model" | null;
  dashboard_id: string | null;
  dashboard_name: string;
  steps: { key: AutopilotStepKey; label: string; status: "pending" | "running" | "done" | "failed"; detail: string }[];
};

// Transform

export type LogicalType = "text" | "integer" | "decimal" | "date" | "boolean";

export type Step = {
  id: string;
  op: string;
  params: Record<string, unknown>;
  reason: string;
  status: "AI" | "Approved";
  by?: "autopilot";
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
  warnings?: { step: number; text: string }[];
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

const tf = (pid: string) => `/api/datasets/${pid}/transform`;
const tq = (pid: string, qid: string) => `${tf(pid)}/queries/${qid}`;

export const transformApi = {
  state: (pid: string) => request<TransformState>(tf(pid)),
  query: (pid: string, qid: string) => request<QueryDetail>(tq(pid, qid)),
  preview: (pid: string, qid: string, step: number) => request<StepPreview>(`${tq(pid, qid)}/preview?step=${step}`),
  removed: (pid: string, qid: string, step: number) =>
    request<StepRows & { comparable: boolean }>(`${tq(pid, qid)}/steps/${step}/removed`),
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
  addStep: (pid: string, qid: string, op: string, params: Record<string, unknown>, position?: number) =>
    request<TransformState>(`${tq(pid, qid)}/steps`, json("POST", { op, params, position })),
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
  by?: "autopilot";
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

export type Aggregation = "sum" | "average" | "count" | "count_distinct" | "min" | "max";

/** Only rows where…: on the measure's own table, or on a related `table`. */
export type MeasureFilter = { table?: string; column: string; operator: string; value?: unknown; values?: unknown[] };

/** Time intelligence: a measure worked out year to date, last year, over rolling months... */
export type TimeCalc = "ytd" | "qtd" | "mtd" | "prior_year" | "prior_year_ytd" | "rolling";

export type Measure = {
  id: string;
  name: string;
  kind: "aggregate" | "formula" | "time";
  base?: string | null;
  calc?: TimeCalc | null;
  months?: number | null;
  use_relationship?: string | null;
  table: string | null;
  aggregation: Aggregation | null;
  expression: string | null;
  filters: MeasureFilter[];
  folder?: string;
  label: string;
  description: string;
  reason: string;
  status: "AI" | "Approved";
  by?: "autopilot";
  /** From the dataset this one extends: read-only here. */
  inherited?: boolean;
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
  layout: Record<string, { x: number; y: number }>;
  can_undo: boolean;
};

/** What the model API takes to add or change a measure. */
export type MeasureBody = {
  name: string;
  kind: "aggregate" | "formula" | "time";
  base?: string | null;
  calc?: TimeCalc | null;
  months?: number | null;
  use_relationship?: string | null;
  table?: string | null;
  aggregation?: Aggregation | null;
  expression?: string | null;
  filters?: MeasureFilter[];
  format: MeasureFormat;
  description?: string;
  folder?: string;
};

export type MeasureCheck = { ok: true; value: number | null; label: string; table: string | null } | { ok: false; error: string };

export type RelationshipCheck =
  | ({ ok: true; active: boolean; checks: Relationship["checks"] } & Pick<Relationship, "from_table" | "from_column" | "to_table" | "to_column">)
  | { ok: false; error: string };

export type ModelRun = ModelState & {
  summary: string;
  added: { kind: string; label: string }[];
  refused: { kind: string; label: string; error: string }[];
};

const md = (pid: string) => `/api/datasets/${pid}/model`;

export type SemanticResult = {
  dataset: Dataset;
  notes: { conformed: string[]; renamed: string[]; skipped: string[] };
};

// Permissions (M16)

export type RoleFilter = { table: string; column: string; operator: "in"; values: (string | number)[] };
export type RoleDef = {
  id?: string;
  name: string;
  filters: RoleFilter[];
  per_person: { table: string; column: string } | null;
  members: { people: string[]; groups: string[] };
  reach?: { restricted: string[]; not_restricted: string[] };
};
export type Group = { id: string; name: string; members: string[] };
export type SecurityState = {
  roles: RoleDef[];
  fields: { id: string; name: string; columns: { name: string; type: LogicalType }[] }[];
  groups: Group[];
};
export type AuditEntry = { at: string; by: string; action: string; detail: string };

export const securityApi = {
  get: (pid: string) => request<SecurityState>(`/api/datasets/${pid}/security`),
  save: (pid: string, roles: RoleDef[]) => request<SecurityState>(`/api/datasets/${pid}/security`, json("PUT", { roles })),
  groups: () => request<{ groups: Group[] }>("/api/workspaces/current/groups"),
  saveGroups: (groups: Group[]) => request<{ groups: Group[] }>("/api/workspaces/current/groups", json("PUT", { groups })),
  audit: () => request<{ entries: AuditEntry[] }>("/api/workspaces/current/audit"),
};

// Notifications and digests

export type Notice = { id: string; at: string; kind: string; title: string; text: string; link: string; by: string };

export const notificationsApi = {
  list: () => request<{ notifications: Notice[] }>("/api/notifications"),
};

export type DigestState = {
  frequency: "off" | "weekly" | "monthly";
  day: number;
  at: string;
  people: string[];
  groups: string[];
  next_run: string | null;
  last_sent: string | null;
  email: boolean;
  recipients: string[];
};

export const digestApi = {
  get: (id: string) => request<DigestState>(`/api/dashboards/${id}/digest`),
  set: (id: string, body: Pick<DigestState, "frequency" | "day" | "at" | "people" | "groups">) =>
    request<DigestState>(`/api/dashboards/${id}/digest`, json("PUT", body)),
  send: (id: string) =>
    request<{ to: string[]; emailed: string[]; failed: string[]; email: boolean }>(`/api/dashboards/${id}/digest/send`, { method: "POST" }),
};

export const semanticApi = {
  catalogue: () => request<LinkCatalogue>("/api/semantic-models/catalogue"),
  build: (name: string, tables: { dataset_id: string; table: string }[]) =>
    request<SemanticResult>("/api/semantic-models", json("POST", { name, tables })),
  merge: (name: string, datasetIds: string[]) =>
    request<SemanticResult>("/api/semantic-models/merge", json("POST", { name, dataset_ids: datasetIds })),
};

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
  addRelationship: (pid: string, rel: { from_table: string; from_column: string; to_table: string; to_column: string }) =>
    request<ModelState>(`${md(pid)}/relationships`, json("POST", rel)),
  checkRelationship: (pid: string, rel: { from_table: string; from_column: string; to_table: string; to_column: string }) =>
    request<RelationshipCheck>(`${md(pid)}/relationships/check`, json("POST", rel)),
  addMeasure: (pid: string, body: MeasureBody) => request<ModelState>(`${md(pid)}/measures`, json("POST", body)),
  updateMeasure: (pid: string, id: string, body: MeasureBody) => request<ModelState>(`${md(pid)}/measures/${id}`, json("PUT", body)),
  checkMeasure: (pid: string, body: MeasureBody & { id?: string }, signal?: AbortSignal) =>
    request<MeasureCheck>(`${md(pid)}/measures/check`, { ...json("POST", body), signal }),
  setDateTable: (pid: string) => request<ModelState>(`${md(pid)}/date-table`, json("PUT", {})),
  setLayout: (pid: string, positions: Record<string, { x: number; y: number }>) =>
    request<ModelState>(`${md(pid)}/layout`, json("PUT", { positions })),
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

// Report

export type VisualType = "kpi" | "bar" | "column" | "stacked_bar" | "stacked_column" | "line" | "area" | "pie" | "donut" | "scatter" | "table";

export type KpiData = {
  value: number | null;
  period: string;
  previous: number | null;
  delta: number | null;
  delta_pct: number | null;
  comparison: string | null;
  sparkline: { label: string; value: number }[];
  driver: { name: string; delta: number; column: string } | null;
  note: string | null;
};

export type ChartData = {
  columns: string[];
  rows: (string | number | null)[][];
  total_rows: number;
  truncated: boolean;
  check: { total: number; parts: number; ok: boolean } | null;
};

export type Visual = {
  id: string;
  type: VisualType;
  title: string;
  measures: string[];
  group_by: { table: string; column: string }[];
  kpi: { direction?: "higher_is_better" | "lower_is_better" };
  width: "half" | "full";
  layout?: GridBox;
  drill?: Drill | null;
  drill_next?: string | null;
  insight: { text: string; check: { verified: boolean; unmatched: number[] }; stale?: boolean } | null;
  status: "AI" | "Approved";
  by?: "autopilot";
  reason: string;
  data: KpiData | ChartData | null;
  error: string | null;
  advice?: string[];
};

/** A visual's place on the report canvas, in cells of a 12-column grid. */
export type GridBox = { x: number; y: number; w: number; h: number };

/** A filter on a report page: fixed by its creator, or chosen in a slicer by whoever is looking. */
export type PageFilter = {
  table: string;
  column: string;
  operator: string;
  values?: (string | number | boolean)[];
  value?: string | number;
  /** The visual it was picked in by a click; that visual itself stays unfiltered. */
  source?: string;
};

/** A date chart shown further down (Year → Year Month → Date) for one viewer. */
export type Drill = { column: string; filters: PageFilter[] };

/** The page on show and its slicer choices, sent with Ask and Report chat questions so they answer for them. */
export type PageScope = { page_id: string | null; filters: PageFilter[] };

export type SlicerValues = { table: string; column: string; type: LogicalType; values: (string | number | boolean)[]; truncated: boolean };

export type ReportField = { id: string; name: string; columns: { name: string; type: LogicalType }[]; measures: string[] };

export type ReportPage = {
  id: string;
  title: string;
  question: string;
  summary: string;
  status: "AI" | "Approved";
  reason?: string;
  slicers: { table: string; column: string }[];
  filters: PageFilter[];
  selected: PageFilter[];
  visuals: Visual[];
};

export type Sharing = { mode: "workspace" } | { mode: "people"; people: string[]; groups: string[] };

export type DashboardSummary = {
  id: string;
  name: string;
  updated_at: string;
  pages: number;
  owner?: string | null;
  sharing?: Sharing;
  dataset: ({ id: string; name: string } & PublishStatus) | null;
  published: boolean;
  published_at: string | null;
  unpublished_changes: boolean;
  /** The first page's shape, for a card on Home (kinds and places of its visuals, no data). */
  preview?: { title: string; visuals: { id: string; type: VisualType; layout?: GridBox | null; width?: string | null; insight?: boolean }[] } | null;
};

export type ReportState = Omit<DashboardSummary, "pages"> & {
  dataset_updated_to: number | null;
  side: "draft" | "published";
  pages: ReportPage[];
  formats: Record<string, MeasureFormat>;
  fields: ReportField[];
  as_of: string | null;
  can_undo: boolean;
  instructions?: string;
  /** Row-level security limits what this person sees (so AI insights, written about all the data, are hidden). */
  restricted?: boolean;
  viewing_as?: ViewAs | null;
};

export type ReportRun = ReportState & {
  summary: string;
  added: { kind: string; label: string }[];
  refused: { kind: string; label: string; error: string }[];
  new_pages: string[];
};

const rp = (id: string) => `/api/dashboards/${id}`;

/** A creator looking at dashboards as a security role (and, for per-person roles, as one person). */
export type ViewAs = { role: string; email?: string | null };
let viewingAs: ViewAs | null = null;
export function setViewAs(next: ViewAs | null) {
  viewingAs = next;
}
function asQuery(): string {
  if (!viewingAs) return "";
  return `as_role=${encodeURIComponent(viewingAs.role)}` + (viewingAs.email ? `&as_email=${encodeURIComponent(viewingAs.email)}` : "");
}
const withAs = (url: string) => (asQuery() ? `${url}${url.includes("?") ? "&" : "?"}${asQuery()}` : url);

export const dashboardsApi = {
  list: () => request<DashboardSummary[]>("/api/dashboards"),
  create: (name: string, datasetId: string) =>
    request<DashboardSummary>("/api/dashboards", json("POST", { name, dataset_id: datasetId })),
  rename: (id: string, name: string) => request<ReportState>(rp(id), json("PATCH", { name })),
  remove: (id: string) => request<unknown>(rp(id), { method: "DELETE" }),
  publish: (id: string) => request<ReportState>(`${rp(id)}/publish`, { method: "POST" }),
  share: (id: string, sharing: Sharing) => request<DashboardSummary>(`${rp(id)}/sharing`, json("PUT", sharing)),
  downloadPowerBI: (id: string, name: string) => downloadFile(`${rp(id)}/powerbi`, `${name}.zip`),
};

/** What the Report AI aims for: a template, the user's own words, or its pick of template. */
export type DesignBrief = { template?: string; description?: string; choose?: boolean };

export type DashboardTemplate = { id: string; name: string; description: string; pages: string[] };

export const reportApi = {
  state: (id: string, side: "draft" | "published" = "draft", page?: string, filters?: PageFilter[], drill?: Record<string, Drill>) =>
    request<ReportState>(
      withAs(
        `${rp(id)}?side=${side}` +
          (page && filters?.length ? `&page=${encodeURIComponent(page)}&filters=${encodeURIComponent(JSON.stringify(filters))}` : "") +
          (drill && Object.keys(drill).length ? `&drill=${encodeURIComponent(JSON.stringify(drill))}` : ""),
      ),
    ),
  values: (id: string, table: string, column: string) =>
    request<SlicerValues>(withAs(`${rp(id)}/values?table=${encodeURIComponent(table)}&column=${encodeURIComponent(column)}`)),
  setSlicers: (id: string, pageId: string, slicers: { table: string; column: string }[]) =>
    request<ReportState>(`${rp(id)}/pages/${pageId}/slicers`, json("PUT", { slicers })),
  setInstructions: (id: string, instructions: string) => request<ReportState>(`${rp(id)}/instructions`, json("PUT", { instructions })),
  setFilters: (id: string, pageId: string, filters: PageFilter[]) =>
    request<ReportState>(`${rp(id)}/pages/${pageId}/filters`, json("PUT", { filters })),
  ai: (pid: string, brief?: DesignBrief) => request<ReportRun & { template: string | null }>(`${rp(pid)}/ai`, json("POST", brief ?? {})),
  templates: () => request<DashboardTemplate[]>("/api/dashboard-templates"),
  approve: (pid: string, itemId?: string) =>
    request<ReportState>(`${rp(pid)}/approve${itemId ? `?item_id=${itemId}` : ""}`, { method: "POST" }),
  undo: (pid: string) => request<ReportState>(`${rp(pid)}/undo`, { method: "POST" }),
  removeVisual: (pid: string, id: string) => request<ReportState>(`${rp(pid)}/visuals/${id}`, { method: "DELETE" }),
  removePage: (pid: string, id: string) => request<ReportState>(`${rp(pid)}/pages/${id}`, { method: "DELETE" }),
  addPage: (pid: string, title: string) => request<ReportState>(`${rp(pid)}/pages`, json("POST", { title })),
  renamePage: (pid: string, id: string, title: string) => request<ReportState>(`${rp(pid)}/pages/${id}`, json("PATCH", { title })),
  addVisual: (
    pid: string,
    pageId: string,
    body: { type: VisualType; title: string; measures: string[]; group_by?: { table: string; column: string }[]; layout?: GridBox },
  ) => request<ReportState>(`${rp(pid)}/pages/${pageId}/visuals`, json("POST", body)),
  updateVisual: (
    pid: string,
    id: string,
    body: Partial<Pick<Visual, "type" | "title" | "measures" | "group_by" | "kpi">>,
  ) => request<ReportState>(`${rp(pid)}/visuals/${id}`, json("PATCH", body)),
  setLayout: (pid: string, pageId: string, positions: Record<string, GridBox>) =>
    request<ReportState>(`${rp(pid)}/pages/${pageId}/layout`, json("PUT", { positions })),
};

/** Rounded for cards and chart labels: RM 1.2M, 34.5K, 53%. Tables use formatMeasure (full precision). */
export function formatCompact(value: number | null | undefined, format: MeasureFormat | undefined): string {
  if (value === null || value === undefined) return "(blank)";
  const fmt = format ?? { type: "number" };
  if (fmt.type === "percent") return `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  const abs = Math.abs(value);
  // Units are picked after rounding to one decimal, so 999,999,999 reads 1.0B, never 1,000.0M.
  const [scaled, unit] =
    abs >= 999.95e6 ? [value / 1e9, "B"] : abs >= 999.95e3 ? [value / 1e6, "M"] : abs >= 1e4 ? [value / 1e3, "K"] : [value, ""];
  // Cards round: RM 478, not RM 478.12; small values keep the measure's decimals.
  const digits = unit ? 1 : fmt.type === "integer" || abs >= 100 ? 0 : Math.min(fmt.decimals ?? 0, 2);
  const text = `${scaled.toLocaleString(undefined, { minimumFractionDigits: unit ? 1 : 0, maximumFractionDigits: digits })}${unit}`;
  return fmt.type === "currency" ? `${fmt.currency ?? "RM"} ${text}` : text;
}

// Chat and activity

export type ChatMode = "full" | "review";

export type ChatAction = {
  action: string;
  params: Record<string, unknown>;
  reason: string;
  label: string;
  ok: boolean;
  error: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  at: string;
  change?: { id: string; status: "pending" | "applied" | "rejected"; mode: ChatMode; actions: ChatAction[] };
  /** A question answered from the data model (Report chat), with the same checks as Ask. */
  answer?: AskResponse & { question: string };
  /** Whether the numbers in the reply match the visuals' data. */
  check?: { verified: boolean; unmatched: number[] };
  /** What the agent looked at before answering: a column's values, or sample rows. */
  looked?: { tool: "column_values" | "sample_rows"; table: string; column?: string; error?: string }[];
  /** For an answer read off the dashboard: the visuals it used and their measures. */
  how?: {
    visuals: { title: string; page: string; type: string; split_by: string[] }[];
    measures: { name: string; definition: string }[];
    as_of: string | null;
  };
};

export type ChatResult = { messages: ChatMessage[]; applied: boolean };

export type ActivityEntry = { at: string; stage: string; summary: string; actor: "You" | "AI"; via: "button" | "chat" };

/** One chat thread: a dataset stage or a dashboard. */
export type ChatThread = {
  load: () => Promise<{ messages: ChatMessage[] }>;
  send: (message: string, mode: ChatMode, signal?: AbortSignal) => Promise<ChatResult>;
  decide: (changeId: string, decision: "approve" | "reject") => Promise<ChatResult>;
};

export function datasetChat(datasetId: string, stage: DatasetStage): ChatThread {
  const base = `/api/datasets/${datasetId}/chat/${stage}`;
  return {
    load: () => request(base),
    send: (message, mode, signal) => request(base, { ...json("POST", { message, mode }), signal }),
    decide: (changeId, decision) => request(`${base}/changes/${changeId}/${decision}`, { method: "POST" }),
  };
}

export function dashboardChat(dashboardId: string, scope?: () => PageScope): ChatThread {
  const base = `/api/dashboards/${dashboardId}/chat`;
  return {
    load: () => request(base),
    send: (message, mode, signal) => request(base, { ...json("POST", { message, mode, ...scope?.() }), signal }),
    decide: (changeId, decision) => request(`${base}/changes/${changeId}/${decision}`, { method: "POST" }),
  };
}

export const activityApi = {
  dataset: (id: string) => request<{ entries: ActivityEntry[] }>(`/api/datasets/${id}/activity`),
  dashboard: (id: string) => request<{ entries: ActivityEntry[] }>(`/api/dashboards/${id}/activity`),
  stageReviewed: (id: string, stage: DatasetStage) =>
    request<Dataset>(`/api/datasets/${id}/stages/${stage}/reviewed`, { method: "POST" }),
  dashboardReviewed: (id: string) => request<ReportState>(`/api/dashboards/${id}/reviewed`, { method: "POST" }),
};

// Ask (questions about a dashboard's data)

export type AskResult = {
  title: string;
  show_as: "number" | "bar" | "line" | "table" | "change";
  period?: string;
  periods?: { before: string; after: string };
  skipped?: string[];
  columns: string[];
  rows: (string | number | null)[][];
  truncated: boolean;
  error: string | null;
  query: { measures: string[]; group_by: { table: string; column: string }[]; filters?: unknown[] };
  how?: { measures: { name: string; definition: string }[]; split_by: string[]; filters: string[]; tables: string[] };
};

export type AskResponse = {
  answer: string;
  follow_ups: string[];
  check: { verified: boolean; unmatched: number[] };
  results: AskResult[];
  cannot_answer: string;
  formats: Record<string, MeasureFormat>;
  dataset_version: number;
  as_of: string | null;
  scope?: string[];
};

export const askApi = {
  ask: (
    dashboardId: string,
    question: string,
    history: { role: string; content: string }[],
    side: "draft" | "published",
    signal?: AbortSignal,
    scope?: PageScope,
  ) => request<AskResponse>(withAs(`/api/dashboards/${dashboardId}/ask`), { ...json("POST", { question, history, side, ...scope }), signal }),
};

// Refresh

export type RefreshCheck = { level: "error" | "warning" | "info"; text: string };

export type RefreshEntry = {
  at: string;
  trigger: "manual" | "replace" | "schedule" | "powerbi" | "linked";
  status: "ok" | "warnings" | "failed";
  sources: { table: string; rows_before: number; rows_after: number }[];
  failed_sources: string[];
  checks: RefreshCheck[];
  published_version: number | null;
  /** A scheduled pull that couldn't download is tried again at this time. */
  retry_at?: string;
};

export type RefreshAlert = {
  at: string;
  status: "warnings" | "failed";
  trigger: "schedule" | "linked";
  text: string;
};

export type RefreshSettings = {
  schedule: string;
  auto_publish: boolean;
  last_run: string | null;
  next_run: string | null;
  /** HH:MM in Malaysia time, for daily, weekday and weekly schedules. */
  at: string;
  /** 0 Monday to 6 Sunday, for weekly schedules. */
  day: number;
  follow_links: boolean;
  retry: number;
};

export type LoadMode = { mode: "replace" | "append"; key: string | null };

export type PublishedVersion = { version: number; published_at: string; live: boolean; rolled_back_from: number | null };

export type RefreshState = {
  settings: RefreshSettings;
  schedules: string[];
  history: RefreshEntry[];
  alert: RefreshAlert | null;
  versions: PublishedVersion[];
  sources: {
    id: string;
    table: string;
    kind: string;
    file?: string;
    url?: string;
    provider?: string;
    rows: number;
    refreshed_at: string;
    pullable: boolean;
    load: LoadMode;
    columns: string[];
  }[];
  entry?: RefreshEntry;
};

const rf = (id: string) => `/api/datasets/${id}`;

export const refreshApi = {
  state: (id: string) => request<RefreshState>(`${rf(id)}/refresh`),
  configure: (id: string, update: Partial<Pick<RefreshSettings, "schedule" | "auto_publish" | "at" | "day" | "follow_links">>) =>
    request<RefreshState>(`${rf(id)}/refresh`, json("PUT", update)),
  setLoad: (id: string, sourceId: string, load: LoadMode) =>
    request<RefreshState>(`${rf(id)}/sources/${sourceId}/load`, json("PUT", load)),
  dismissAlert: (id: string) => request<RefreshState>(`${rf(id)}/refresh/alert/dismiss`, { method: "POST" }),
  rollBack: (id: string, version: number, confirm = false) =>
    request<{ versions: PublishedVersion[] }>(`${rf(id)}/versions/${version}/roll-back?confirm=${confirm}`, { method: "POST" }),
  pullAll: (id: string) => request<RefreshState>(`${rf(id)}/refresh`, { method: "POST" }),
  pull: (id: string, sourceId: string) => request<RefreshState>(`${rf(id)}/sources/${sourceId}/pull`, { method: "POST" }),
  replace: (id: string, sourceId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<RefreshState>(`${rf(id)}/sources/${sourceId}/replace`, { method: "POST", body: form });
  },
};

export const exportApi = {
  summary: (dashboardId: string) =>
    request<{ bullets: { text: string; unmatched: number[] }[] }>(`/api/dashboards/${dashboardId}/export/summary`, {
      method: "POST",
    }),
};
