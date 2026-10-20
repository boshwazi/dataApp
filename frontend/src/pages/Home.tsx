import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ProviderSettings } from "../api";

const STAGES = [
  ["Get Data", "Load CSV, Excel and Google Sheets"],
  ["Transform", "AI cleans and merges tables as Applied Steps"],
  ["Model", "AI builds relationships, a date table and measures"],
  ["Report", "AI picks visuals and writes insights"],
  ["Ask", "Ask questions about your data"],
  ["Export", "PDF report"],
];

export function Home() {
  const [settings, setSettings] = useState<ProviderSettings | null>(null);

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setSettings(null));
  }, []);

  const connected = settings?.capabilities.tools !== undefined;

  return (
    <>
      <h1>Projects</h1>
      {settings && !connected && (
        <div className="notice">
          Connect your AI provider first: <Link to="/settings">open Settings</Link> and run the connection test.
        </div>
      )}
      <p className="muted">Projects arrive in the next milestone. Each project runs these stages:</p>
      <ol className="stages">
        {STAGES.map(([name, detail]) => (
          <li key={name}>
            <strong>{name}</strong>
            <span>{detail}</span>
          </li>
        ))}
      </ol>
    </>
  );
}
