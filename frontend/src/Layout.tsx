import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { ChevronRight, Database, House, LayoutDashboard, Search, Settings as SettingsIcon } from "lucide-react";
import { api, dashboardsApi, type DashboardSummary, type Dataset } from "./api";
import { LemonMark, useCurrentCrumbs } from "./components/ui";

const NAV = [
  { to: "/", label: "Home", icon: House, end: true },
  { to: "/datasets", label: "Datasets", icon: Database, end: false },
  { to: "/dashboards", label: "Dashboards", icon: LayoutDashboard, end: false },
  { to: "/settings", label: "Settings", icon: SettingsIcon, end: false },
];

export function Layout() {
  const [searching, setSearching] = useState(false);
  const crumbs = useCurrentCrumbs();

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearching(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link to="/" className="brand" aria-label="datalemon home">
          <LemonMark /> datalemon
        </Link>
        <nav aria-label="Main">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} title={label}>
              <Icon size={17} aria-hidden="true" />
              <span className="nav-label">{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="workspace">
          <span className="workspace-dot" aria-hidden="true" />
          Local workspace
        </div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <nav className="crumbs" aria-label="Breadcrumb">
            {crumbs.map((c, i) => (
              <span key={i} className="crumb">
                {i > 0 && <ChevronRight size={14} aria-hidden="true" />}
                {c.to && i < crumbs.length - 1 ? <Link to={c.to}>{c.label}</Link> : <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c.label}</span>}
              </span>
            ))}
          </nav>
          <button className="search-button" onClick={() => setSearching(true)} aria-label="Search (Ctrl K)">
            <Search size={15} aria-hidden="true" />
            <span>Search</span>
            <kbd>Ctrl K</kbd>
          </button>
        </header>
        <main className="content" id="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
      {searching && <QuickSearch onClose={() => setSearching(false)} />}
    </div>
  );
}

type Item = { label: string; detail: string; to: string; group: string };

function QuickSearch({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [dashboards, setDashboards] = useState<DashboardSummary[]>([]);

  useEffect(() => {
    ref.current?.showModal();
    api.listDatasets().then(setDatasets).catch(() => {});
    dashboardsApi.list().then(setDashboards).catch(() => {});
  }, []);

  const items = useMemo<Item[]>(() => {
    const all: Item[] = [
      ...dashboards.map((d) => ({ label: d.name, detail: d.dataset ? `Dashboard on ${d.dataset.name}` : "Dashboard", to: `/dashboards/${d.id}`, group: "Dashboards" })),
      ...datasets.map((d) => ({ label: d.name, detail: `Dataset with ${d.sources.length} ${d.sources.length === 1 ? "source" : "sources"}`, to: `/datasets/${d.id}/get-data`, group: "Datasets" })),
      { label: "New dashboard", detail: "Start from existing or new data", to: "/?new=1", group: "Actions" },
      { label: "Settings", detail: "AI provider and connection test", to: "/settings", group: "Actions" },
    ];
    const q = query.trim().toLowerCase();
    return q ? all.filter((i) => i.label.toLowerCase().includes(q) || i.detail.toLowerCase().includes(q)) : all;
  }, [query, datasets, dashboards]);

  function go(item: Item | undefined) {
    if (!item) return;
    onClose();
    navigate(item.to);
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(items[active]);
    }
  }

  return (
    <dialog
      ref={ref}
      className="dialog search-dialog"
      aria-label="Search"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      <div className="search-field">
        <Search size={17} aria-hidden="true" />
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKey}
          placeholder="Find a dashboard, dataset or action"
          aria-label="Search"
          role="combobox"
          aria-expanded="true"
          aria-controls="search-results"
          aria-activedescendant={items[active] ? `search-item-${active}` : undefined}
        />
      </div>
      <ul id="search-results" role="listbox" className="search-results">
        {items.length === 0 && <li className="muted small search-empty">Nothing matches "{query}".</li>}
        {items.map((item, i) => (
          <li
            key={item.group + item.to}
            id={`search-item-${i}`}
            role="option"
            aria-selected={i === active}
            className={i === active ? "on" : ""}
            onMouseEnter={() => setActive(i)}
            onClick={() => go(item)}
          >
            <span className="search-label">{item.label}</span>
            <span className="muted small">{item.detail}</span>
          </li>
        ))}
      </ul>
    </dialog>
  );
}
