import { useCallback, useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { notificationsApi, type Notice } from "../api";
import { useAccount } from "./account";
import { t } from "../i18n";

const POLL_MS = 60_000;

/** What this person has seen is kept by their browser: reading notifications never writes anything. */
function seenKey(workspace: string | undefined) {
  return `datawazi.notices-seen.${workspace ?? "local"}`;
}

function readSeen(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function ago(iso: string): string {
  const minutes = (Date.now() - new Date(iso).getTime()) / 60000;
  if (minutes < 1) return t("just now");
  if (minutes < 60) return t("{n} min ago", { n: Math.floor(minutes) });
  if (minutes < 1440) return t("{n} h ago", { n: Math.floor(minutes / 60) });
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** The bell in the rail: refreshes that failed, Autopilot runs, dashboards shared or updated, digests. */
export function Notifications() {
  const account = useAccount();
  const navigate = useNavigate();
  const key = seenKey(account.workspace?.id);
  const [items, setItems] = useState<Notice[]>([]);
  const [seen, setSeen] = useState(() => readSeen(key));
  // What was seen before this opening: items newer than it stay marked new while the panel is open.
  const [before, setBefore] = useState(seen);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    notificationsApi.list().then((r) => setItems(r.notifications)).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const unread = items.filter((n) => n.at > seen).length;
  const fresh = items.filter((n) => n.at > before).length;

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next) setBefore(seen);
    if (next && items.length) {
      const newest = items[0].at;
      try {
        localStorage.setItem(key, newest);
      } catch {
        /* private windows may refuse; the count simply comes back next time */
      }
      setSeen(newest); // the badge clears; the panel keeps its highlights from `before`
    }
  }

  return (
    <div className="notices" ref={root}>
      <button className="rail-button" onClick={toggle} aria-expanded={open} aria-label={unread ? t("Notifications, {n} new", { n: unread }) : t("Notifications")} data-tip={t("Notifications")}>
        <Bell size={18} aria-hidden="true" />
        {unread > 0 && <span className="notice-count">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="notices-panel" role="dialog" aria-label={t("Notifications")}>
          <div className="notices-head">
            <h2>{t("Notifications")}</h2>
            {fresh > 0 && <span className="notices-new">{t("{n} new", { n: fresh })}</span>}
          </div>
          {items.length === 0 ? (
            <p className="muted small">{t("Nothing yet. Failed refreshes, finished Autopilot runs and dashboards shared with you show up here.")}</p>
          ) : (
            <ul>
              {items.map((n) => (
                <li key={n.id} className={n.at > before ? "new" : ""}>
                  <button
                    className="notice-item"
                    onClick={() => {
                      setOpen(false);
                      if (n.link) navigate(n.link);
                    }}
                  >
                    <span className="notice-title">{n.title}</span>
                    {n.text && <span className="notice-text">{n.text}</span>}
                    <span className="muted small">{ago(n.at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
