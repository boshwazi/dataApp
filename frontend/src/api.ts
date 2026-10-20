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
