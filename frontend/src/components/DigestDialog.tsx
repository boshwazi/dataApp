import { useEffect, useRef, useState } from "react";
import { digestApi, securityApi, type DigestState, type Group } from "../api";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** A weekly or monthly digest of a dashboard's headline numbers, by email and in the app. */
export function DigestDialog(props: { dashboardId: string; name: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [state, setState] = useState<DigestState | null>(null);
  const [people, setPeople] = useState("");
  const [groups, setGroups] = useState<Group[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; bad?: boolean } | null>(null);

  function loaded(next: DigestState) {
    setState(next);
    setPeople(next.people.join(", "));
    setChosen(next.groups);
  }

  useEffect(() => {
    ref.current?.showModal();
    digestApi.get(props.dashboardId).then(loaded).catch((e) => setMessage({ text: e.message, bad: true }));
    securityApi.groups().then((r) => setGroups(r.groups)).catch(() => setGroups([]));
  }, [props.dashboardId]);

  async function save(frequency = state?.frequency ?? "off") {
    if (!state) return;
    setBusy("save");
    setMessage(null);
    try {
      loaded(
        await digestApi.set(props.dashboardId, {
          frequency,
          day: state.day,
          at: state.at,
          people: people.split(",").map((p) => p.trim()).filter(Boolean),
          groups: chosen,
        }),
      );
      setMessage({ text: frequency === "off" ? "Digest turned off" : "Saved" });
    } catch (e) {
      setMessage({ text: (e as Error).message, bad: true });
    } finally {
      setBusy(null);
    }
  }

  async function sendNow() {
    setBusy("send");
    setMessage(null);
    try {
      const sent = await digestApi.send(props.dashboardId);
      setMessage({
        text: sent.email
          ? `Sent to ${sent.emailed.join(", ") || "nobody"}${sent.failed.length ? `; failed: ${sent.failed.join("; ")}` : ""}`
          : `Shown in the app to ${sent.to.join(", ")} (email isn't set up on the server)`,
        bad: sent.failed.length > 0,
      });
    } catch (e) {
      setMessage({ text: (e as Error).message, bad: true });
    } finally {
      setBusy(null);
    }
  }

  return (
    <dialog
      ref={ref}
      className="dialog share-dialog"
      aria-label={`Digest for ${props.name}`}
      onCancel={(e) => {
        e.preventDefault();
        props.onClose();
      }}
    >
      <h2>Email digest</h2>
      <p className="muted small">
        The published pages' summaries and KPI numbers, with a link to the dashboard. It goes only to members the dashboard is shared with;
        with row-level security it carries no numbers, just the link.
      </p>
      {state && (
        <>
          <div className="digest-when">
            <select aria-label="How often" value={state.frequency} onChange={(e) => setState({ ...state, frequency: e.target.value as DigestState["frequency"] })}>
              <option value="off">Off</option>
              <option value="weekly">Every week</option>
              <option value="monthly">Every month, on the 1st</option>
            </select>
            {state.frequency === "weekly" && (
              <select aria-label="Day" value={state.day} onChange={(e) => setState({ ...state, day: Number(e.target.value) })}>
                {DAYS.map((d, i) => (
                  <option key={d} value={i}>
                    on {d}
                  </option>
                ))}
              </select>
            )}
            {state.frequency !== "off" && (
              <label className="small">
                at <input type="time" value={state.at} onChange={(e) => setState({ ...state, at: e.target.value })} /> Malaysia time
              </label>
            )}
          </div>
          <div className="share-who">
            <label className="field">
              <span className="muted small">Emails, separated by commas</span>
              <input value={people} onChange={(e) => setPeople(e.target.value)} placeholder="aisyah@company.com" />
            </label>
            {groups.map((g) => (
              <label key={g.id} className="check small">
                <input type="checkbox" checked={chosen.includes(g.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, g.id] : chosen.filter((x) => x !== g.id))} />
                {g.name} <span className="muted">({g.members.length})</span>
              </label>
            ))}
          </div>
          <p className="small muted">
            {state.recipients.length ? `Goes to: ${state.recipients.join(", ")}.` : "Nobody gets it yet."}
            {state.next_run && ` Next: ${new Date(state.next_run).toLocaleString("en-MY", { timeZone: "Asia/Kuala_Lumpur", dateStyle: "medium", timeStyle: "short" })}.`}
            {!state.email && " Email isn't set up on the server, so it is shown in the app only."}
          </p>
        </>
      )}
      {message && <p className={`small ${message.bad ? "bad" : "ok"}`}>{message.text}</p>}
      <div className="instructions-foot">
        <button className="secondary" onClick={sendNow} disabled={busy !== null || !state?.recipients.length}>
          {busy === "send" ? "Sending…" : "Send now"}
        </button>
        <span className="grow" />
        <button className="secondary" onClick={props.onClose}>
          Close
        </button>
        <button onClick={() => save()} disabled={busy !== null || !state}>
          {busy === "save" ? "Saving…" : "Save"}
        </button>
      </div>
    </dialog>
  );
}
