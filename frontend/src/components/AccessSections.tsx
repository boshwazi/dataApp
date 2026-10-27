import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { securityApi, type AuditEntry, type Group } from "../api";
import { useToast } from "./ui";

/** Named groups of people, for sharing dashboards and giving security roles to several people at once. */
export function GroupsSection() {
  const toast = useToast();
  const [groups, setGroups] = useState<(Group & { text: string })[] | null>(null);
  const [savedGroups, setSavedGroups] = useState("[]"); // what was last loaded or saved
  const [saving, setSaving] = useState(false);
  const load = (list: Group[]) => {
    const next = list.map((g) => ({ ...g, text: g.members.join(", ") }));
    setGroups(next);
    setSavedGroups(JSON.stringify(next));
  };

  useEffect(() => {
    securityApi
      .groups()
      .then((r) => load(r.groups))
      .catch((e) => toast(e.message, "error"));
  }, [toast]);

  async function save() {
    if (!groups) return;
    setSaving(true);
    try {
      const saved = await securityApi.saveGroups(
        groups.map((g) => ({ id: g.id, name: g.name, members: g.text.split(",").map((m) => m.trim()).filter(Boolean) })),
      );
      load(saved.groups);
      toast("Groups saved");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  }

  const update = (i: number, patch: Partial<Group & { text: string }>) =>
    setGroups((gs) => gs && gs.map((g, j) => (j === i ? { ...g, ...patch } : g)));

  return (
    <section className="access-section">
      <h2 className="section-title">Groups</h2>
      <p className="muted small">Share a dashboard with a group, or give a whole group a security role, instead of naming each person.</p>
      {groups?.map((g, i) => (
        <div key={g.id || i} className="group-row">
          <input aria-label="Group name" value={g.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="Field sales" />
          <input aria-label="Members' emails" value={g.text} onChange={(e) => update(i, { text: e.target.value })} placeholder="aisyah@company.com, raj@company.com" />
          <button className="icon danger" aria-label={`Remove ${g.name || "group"}`} onClick={() => setGroups((gs) => gs && gs.filter((_, j) => j !== i))}>
            <Trash2 size={16} />
          </button>
        </div>
      ))}
      <div className="security-actions">
        <button className="secondary" onClick={() => setGroups((gs) => [...(gs ?? []), { id: "", name: "", members: [], text: "" }])}>
          <Plus size={15} aria-hidden="true" /> Add a group
        </button>
        {groups !== null && JSON.stringify(groups) !== savedGroups && (
          <button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save groups"}
          </button>
        )}
      </div>
    </section>
  );
}

const ACTIONS: Record<string, string> = { members: "Members", groups: "Groups", roles: "Security roles", sharing: "Sharing", publish: "Publishing", privacy: "AI privacy" };

const AUDIT_PAGE = 15;

/** Who changed access, or published, and when. */
export function AuditSection() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState(AUDIT_PAGE);

  useEffect(() => {
    securityApi.audit().then((r) => setEntries(r.entries)).catch((e) => setError(e.message));
  }, []);

  return (
    <section className="access-section">
      <h2 className="section-title">Audit log</h2>
      {error && <p className="bad small">{error}</p>}
      {entries && entries.length === 0 && <p className="muted small">Nothing yet.</p>}
      {entries && entries.length > 0 && (
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>What</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {entries.slice(0, shown).map((e, i) => (
                <tr key={i}>
                  <td className="small nowrap">{new Date(e.at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</td>
                  <td className="small">{e.by}</td>
                  <td className="small">{ACTIONS[e.action] ?? e.action}</td>
                  <td className="small">{e.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {entries && entries.length > shown && (
        <button className="secondary audit-more" onClick={() => setShown((n) => n + AUDIT_PAGE)}>
          Show {Math.min(AUDIT_PAGE, entries.length - shown)} more of {entries.length - shown}
        </button>
      )}
    </section>
  );
}
