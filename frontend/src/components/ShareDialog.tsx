import { useEffect, useRef, useState } from "react";
import { dashboardsApi, securityApi, type Group, type Sharing } from "../api";

/** Who sees a published dashboard: the whole workspace, or chosen people and groups. Creators always see it. */
export function ShareDialog(props: { dashboardId: string; name: string; sharing: Sharing; onSaved: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [mode, setMode] = useState(props.sharing.mode);
  const [people, setPeople] = useState(props.sharing.mode === "people" ? props.sharing.people.join(", ") : "");
  const [chosen, setChosen] = useState<string[]>(props.sharing.mode === "people" ? props.sharing.groups : []);
  const [groups, setGroups] = useState<Group[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    ref.current?.showModal();
    securityApi.groups().then((r) => setGroups(r.groups)).catch(() => setGroups([]));
  }, []);

  async function save() {
    setSaving(true);
    setError(null);
    const sharing: Sharing =
      mode === "workspace"
        ? { mode }
        : { mode, people: people.split(",").map((p) => p.trim()).filter(Boolean), groups: chosen };
    try {
      await dashboardsApi.share(props.dashboardId, sharing);
      props.onSaved();
      props.onClose();
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="dialog share-dialog"
      aria-label={`Share ${props.name}`}
      onCancel={(e) => {
        e.preventDefault();
        props.onClose();
      }}
    >
      <h2>Share {props.name}</h2>
      <p className="muted small">Viewers see the published version. Creators and admins see every dashboard.</p>
      <div className="share-modes" role="radiogroup" aria-label="Who can see it">
        <label className="check">
          <input type="radio" name="share" checked={mode === "workspace"} onChange={() => setMode("workspace")} />
          Everyone in the workspace
        </label>
        <label className="check">
          <input type="radio" name="share" checked={mode === "people"} onChange={() => setMode("people")} />
          Only chosen people and groups
        </label>
      </div>
      {mode === "people" && (
        <div className="share-who">
          <label className="field">
            <span className="muted small">Emails, separated by commas</span>
            <input value={people} onChange={(e) => setPeople(e.target.value)} placeholder="aisyah@company.com, raj@company.com" />
          </label>
          {groups.length > 0 ? (
            groups.map((g) => (
              <label key={g.id} className="check small">
                <input
                  type="checkbox"
                  checked={chosen.includes(g.id)}
                  onChange={(e) => setChosen(e.target.checked ? [...chosen, g.id] : chosen.filter((x) => x !== g.id))}
                />
                {g.name} <span className="muted">({g.members.length})</span>
              </label>
            ))
          ) : (
            <p className="muted small">Admins can make groups on the Members page.</p>
          )}
        </div>
      )}
      <div className="instructions-foot">
        {error && <span className="bad small">{error}</span>}
        <span className="grow" />
        <button className="secondary" onClick={props.onClose}>
          Cancel
        </button>
        <button onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </dialog>
  );
}
