import { useEffect, useState, type FormEvent } from "react";
import { api, type ConnectionReport, type ProviderSettings } from "../api";

function YesNo({ value }: { value: boolean | null }) {
  if (value === null) return <span className="muted">not checked</span>;
  return value ? <span className="ok">Supported</span> : <span className="bad">Not supported</span>;
}

export function Settings() {
  const [settings, setSettings] = useState<ProviderSettings | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [report, setReport] = useState<ConnectionReport | null>(null);

  function apply(next: ProviderSettings) {
    setSettings(next);
    setBaseUrl(next.base_url);
    setModel(next.model);
    setApiKey("");
  }

  useEffect(() => {
    api.getSettings().then(apply).catch((e) => setMessage(e.message));
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      apply(await api.saveSettings({ base_url: baseUrl, model, api_key: apiKey }));
      setReport(null);
      setMessage("Saved.");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    setMessage(null);
    setReport(null);
    try {
      setReport(await api.testConnection());
      apply(await api.getSettings());
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  if (!settings) return <p className="muted">{message ?? "Loading…"}</p>;

  const keyPlaceholder = settings.has_api_key
    ? `Saved (${settings.api_key_hint}). Leave blank to keep it.`
    : "Paste your API key";

  return (
    <div className="page">
      <h1>Settings</h1>
      <p className="muted">
        The AI provider used by every stage. Any OpenAI-compatible API works. Your key stays on this machine and is
        never sent to the browser.
      </p>

      <form className="card form" onSubmit={save}>
        <label>
          Base URL
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} required />
        </label>
        <label>
          Model
          <input value={model} onChange={(e) => setModel(e.target.value)} required />
        </label>
        <label>
          API key
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={keyPlaceholder}
            disabled={settings.api_key_from_env}
            autoComplete="off"
          />
          {settings.api_key_from_env && <small className="muted">Set by LLM_API_KEY in .env</small>}
        </label>
        <div className="actions">
          <button type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" className="secondary" onClick={test} disabled={testing || !settings.has_api_key}>
            {testing ? "Testing…" : "Test connection"}
          </button>
          {message && <span className="muted">{message}</span>}
        </div>
      </form>

      {report && (
        <section className="card">
          <h2>{report.ok ? "Connected" : "Connection failed"}</h2>
          {report.ok ? (
            <dl className="report">
              <dt>Response time</dt>
              <dd>{report.latency_ms} ms</dd>
              <dt>Model reply</dt>
              <dd>{report.reply || <span className="muted">(empty)</span>}</dd>
              <dt>Tool calling</dt>
              <dd>
                <YesNo value={report.supports_tools} />
              </dd>
              <dt>JSON mode</dt>
              <dd>
                <YesNo value={report.supports_json_mode} />
              </dd>
            </dl>
          ) : (
            <p className="bad">{report.error}</p>
          )}
          {report.ok && !report.supports_tools && (
            <p className="muted">
              Without tool calling, the stage agents will use {report.supports_json_mode ? "JSON mode" : "plain-text JSON"}{" "}
              instead.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
