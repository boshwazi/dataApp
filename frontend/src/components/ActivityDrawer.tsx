import { useEffect, useState } from "react";
import type { ActivityEntry } from "../api";

const STAGE_NAMES: Record<string, string> = {
  get_data: "Get Data",
  transform: "Transform",
  model: "Model",
  report: "Report",
  publish: "Publish",
};

/** Every change, by you or the AI, through a button or the chat. */
export function ActivityDrawer(props: { load: () => Promise<{ entries: ActivityEntry[] }>; onClose: () => void }) {
  const [entries, setEntries] = useState<ActivityEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    props.load().then((r) => setEntries(r.entries)).catch((e) => setError(e.message));
  }, [props]);

  return (
    <div className="drawer-backdrop" onClick={props.onClose}>
      <aside className="drawer" role="dialog" aria-label="Activity" onClick={(e) => e.stopPropagation()}>
        <header className="chat-head">
          <strong>Activity</strong>
          <button className="icon" onClick={props.onClose} aria-label="Close activity">
            ×
          </button>
        </header>
        {error && <p className="bad small">{error}</p>}
        {entries?.length === 0 && <p className="muted small">No changes yet.</p>}
        <ol className="activity">
          {entries?.map((e, i) => (
            <li key={i}>
              <span className={`badge ${e.actor === "AI" ? "ai" : ""}`}>{e.actor === "AI" ? "AI" : "You"}</span>
              <div>
                <p>{e.summary}</p>
                <p className="muted small">
                  {STAGE_NAMES[e.stage] ?? e.stage}
                  {e.via === "chat" ? " · chat" : ""} · {new Date(e.at).toLocaleString()}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
