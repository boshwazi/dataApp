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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return response.json();
}

export const api = {
  getSettings: () => request<ProviderSettings>("/api/settings"),
  saveSettings: (update: { base_url?: string; model?: string; api_key?: string }) =>
    request<ProviderSettings>("/api/settings", { method: "PUT", body: JSON.stringify(update) }),
  testConnection: () => request<ConnectionReport>("/api/settings/test", { method: "POST" }),
};
