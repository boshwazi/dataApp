import { useEffect, useState } from "react";
import { Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { securityApi, type RoleDef, type SecurityState } from "../api";
import { EmptyState, useToast } from "../components/ui";
import { useDataset } from "./Dataset";

type Draft = RoleDef & { valuesText: string[] };

function draftOf(role: RoleDef): Draft {
  return { ...role, valuesText: role.filters.map((f) => (f.values ?? []).join(", ")) };
}

/** Row-level security: who sees which rows of this dataset on its dashboards. */
export function Security() {
  const { project } = useDataset();
  const toast = useToast();
  const [state, setState] = useState<SecurityState | null>(null);
  const [roles, setRoles] = useState<Draft[]>([]);
  const [savedRoles, setSavedRoles] = useState("[]"); // what was last loaded or saved, to tell when there's something to save
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loaded(next: SecurityState) {
    setState(next);
    setRoles(next.roles.map(draftOf));
    setSavedRoles(JSON.stringify(next.roles.map(draftOf)));
  }

  useEffect(() => {
    securityApi.get(project.id).then(loaded).catch((e) => setError(e.message));
  }, [project.id]);

  if (!state) return <p className={error ? "bad" : "muted"}>{error ?? "Loading…"}</p>;

  const tables = state.fields;
  const textColumns = (tableId: string) => tables.find((t) => t.id === tableId)?.columns.filter((c) => c.type === "text") ?? [];
  const update = (i: number, patch: Partial<Draft>) => setRoles((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload = roles.map((r) => ({
        id: r.id,
        name: r.name,
        filters: r.filters.map((f, k) => ({
          table: f.table,
          column: f.column,
          operator: "in" as const,
          values: r.valuesText[k].split(",").map((v) => v.trim()).filter(Boolean),
        })),
        per_person: r.per_person,
        members: { ...r.members, people: r.members.people.filter(Boolean) },
      }));
      loaded(await securityApi.save(project.id, payload));
      toast("Roles saved");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const first = tables[0];
  const newFilter = () => ({ table: first?.id ?? "", column: textColumns(first?.id ?? "")[0]?.name ?? "", operator: "in" as const, values: [] });
  const addRole = () =>
    setRoles((rs) => [...rs, { name: "", filters: [newFilter()], valuesText: [""], per_person: null, members: { people: [], groups: [] } }]);
  const dirty = JSON.stringify(roles) !== savedRoles;

  return (
    <div className="security">
      <header className="page-header">
        <div className="page-header-text">
          <h1>Who sees which rows</h1>
          <p className="muted security-lead">
            Roles limit what viewers see on this dataset's dashboards: a fixed filter (Region is North), or a column matched to
            each person's email so everyone sees their own rows. Once there is a role, a viewer in none of them sees no data.
            Creators and admins see everything, and can view a dashboard as any role.
          </p>
        </div>
      </header>

      {roles.map((role, i) => (
        <section key={role.id ?? i} className="card role-card">
          <header className="role-head">
            <input aria-label="Role name" value={role.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="Role name, for example North team" />
            <button className="tool danger" title="Remove role" aria-label={`Remove ${role.name || "role"}`} onClick={() => setRoles((rs) => rs.filter((_, j) => j !== i))}>
              <Trash2 size={15} />
            </button>
          </header>

          <h3>Rows they see</h3>
          {role.filters.map((f, k) => (
            <div key={k} className="role-filter">
              <select aria-label="Table" value={f.table} onChange={(e) => update(i, { filters: role.filters.map((g, m) => (m === k ? { ...g, table: e.target.value, column: tables.find((t) => t.id === e.target.value)?.columns[0]?.name ?? "" } : g)) })}>
                {tables.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
              <select aria-label="Column" value={f.column} onChange={(e) => update(i, { filters: role.filters.map((g, m) => (m === k ? { ...g, column: e.target.value } : g)) })}>
                {tables.find((t) => t.id === f.table)?.columns.map((c) => (
                  <option key={c.name} value={c.name}>{c.name}</option>
                ))}
              </select>
              <span className="muted small">is one of</span>
              <input
                aria-label="Values, separated by commas"
                value={role.valuesText[k]}
                onChange={(e) => update(i, { valuesText: role.valuesText.map((v, m) => (m === k ? e.target.value : v)) })}
                placeholder="North, Central"
              />
              <button className="tool" aria-label="Remove filter" onClick={() => update(i, { filters: role.filters.filter((_, m) => m !== k), valuesText: role.valuesText.filter((_, m) => m !== k) })}>
                <X size={14} />
              </button>
            </div>
          ))}
          <div className="role-filter">
            <label className="check small">
              <input
                type="checkbox"
                checked={!!role.per_person}
                onChange={(e) => update(i, { per_person: e.target.checked ? { table: first?.id ?? "", column: textColumns(first?.id ?? "")[0]?.name ?? "" } : null })}
              />
              Each person sees rows where
            </label>
            {role.per_person && (
              <>
                <select aria-label="Table holding emails" value={role.per_person.table} onChange={(e) => update(i, { per_person: { table: e.target.value, column: textColumns(e.target.value)[0]?.name ?? "" } })}>
                  {tables.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
                <select aria-label="Column holding emails" value={role.per_person.column} onChange={(e) => update(i, { per_person: { ...role.per_person!, column: e.target.value } })}>
                  {textColumns(role.per_person.table).map((c) => (
                    <option key={c.name} value={c.name}>{c.name}</option>
                  ))}
                </select>
                <span className="muted small">is their email</span>
              </>
            )}
          </div>
          <button className="link small" onClick={() => update(i, { filters: [...role.filters, newFilter()], valuesText: [...role.valuesText, ""] })}>
            Add a filter
          </button>
          {role.reach && (
            <p className="small role-reach">
              Restricts {role.reach.restricted.join(", ") || "nothing yet"}.
              {role.reach.not_restricted.length > 0 && (
                <> Not restricted (no relationship leads to the filtered table): {role.reach.not_restricted.join(", ")}.</>
              )}
            </p>
          )}

          <h3>Members</h3>
          <label className="field">
            <span className="muted small">Emails, separated by commas</span>
            <input
              value={role.members.people.join(", ")}
              onChange={(e) => update(i, { members: { ...role.members, people: e.target.value.split(",").map((v) => v.trim()) } })}
              placeholder="aisyah@company.com, raj@company.com"
            />
          </label>
          {state.groups.length > 0 && (
            <div className="role-groups">
              {state.groups.map((g) => (
                <label key={g.id} className="check small">
                  <input
                    type="checkbox"
                    checked={role.members.groups.includes(g.id)}
                    onChange={(e) =>
                      update(i, {
                        members: {
                          ...role.members,
                          groups: e.target.checked ? [...role.members.groups, g.id] : role.members.groups.filter((x) => x !== g.id),
                        },
                      })
                    }
                  />
                  {g.name} <span className="muted">({g.members.length})</span>
                </label>
              ))}
            </div>
          )}
        </section>
      ))}

      {roles.length === 0 && (
        <EmptyState
          icon={<ShieldCheck size={28} />}
          title="Everyone with access sees every row"
          action={
            <button onClick={addRole}>
              <Plus size={15} aria-hidden="true" /> Add a role
            </button>
          }
        >
          Add a role to limit what viewers see: the North team sees only rows where Region is North, or each salesperson sees
          only their own customers.
        </EmptyState>
      )}

      {(roles.length > 0 || dirty) && (
        <div className="security-actions">
          {roles.length > 0 && (
            <button className="secondary" onClick={addRole}>
              <Plus size={15} aria-hidden="true" /> Add a role
            </button>
          )}
          {dirty && (
            <button onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save roles"}
            </button>
          )}
        </div>
      )}
      {error && <p className="bad">{error}</p>}
    </div>
  );
}
