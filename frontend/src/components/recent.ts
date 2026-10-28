/** Dashboards this person opened lately, for Home's Recent cards. Kept in this browser only (a convenience, not
    a record): storage can be off or cleared, and then Recent falls back to what changed last. */
const KEY = "datawazi-recent-dashboards";
const KEEP = 12;

export type Opened = { id: string; at: string };

export function recentlyOpened(): Opened[] {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(list) ? list.filter((o) => typeof o?.id === "string" && typeof o?.at === "string") : [];
  } catch {
    return [];
  }
}

export function rememberOpened(id: string) {
  try {
    const next = [{ id, at: new Date().toISOString() }, ...recentlyOpened().filter((o) => o.id !== id)].slice(0, KEEP);
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // storage unavailable: Recent shows what changed last instead
  }
}
