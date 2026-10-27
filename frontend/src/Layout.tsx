import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { Boxes, Building2, Check, CircleHelp, Database, Monitor, Moon, ScrollText, Sun, House, LayoutDashboard, LogOut, Plus, Search, Settings as SettingsIcon, Users } from "lucide-react";
import { accountApi, api, dashboardsApi, type DashboardSummary, type Dataset, type Role } from "./api";
import { useAccount } from "./components/account";
import { InstructionsDialog } from "./components/InstructionsDialog";
import { Notifications } from "./components/Notifications";
import { savedTheme, setTheme, type ThemeChoice } from "./components/theme";
import { savedDensity, setDensity, type Density } from "./components/density";
import { BrandMark, TopbarContext, useCurrentCrumbs, useDialogs, useShortcut, useToast } from "./components/ui";
import { language, setLanguage, t, type Language } from "./i18n";

type NavItem = {
  to: string;
  label: string;
  icon: typeof House;
  end: boolean;
  section: "Workspace" | "Admin";
  role?: Role;
  platform?: boolean;
  hosted?: boolean;
  /** On phones it moves from the bottom bar into the workspace menu, keeping the bar to a few labelled items. */
  more?: boolean;
};

const NAV: NavItem[] = [
  { to: "/", label: "Home", icon: House, end: true, section: "Workspace" },
  { to: "/datasets", label: "Datasets", icon: Database, end: false, section: "Workspace", role: "creator" },
  { to: "/semantic-models", label: "Semantic models", icon: Boxes, end: false, section: "Workspace", role: "creator", more: true },
  { to: "/dashboards", label: "Dashboards", icon: LayoutDashboard, end: false, section: "Workspace" },
  { to: "/help", label: "Help", icon: CircleHelp, end: false, section: "Workspace" },
  { to: "/workspace", label: "Workspace", icon: Building2, end: false, section: "Admin", role: "admin", more: true },
  { to: "/members", label: "Members", icon: Users, end: false, section: "Admin", role: "admin", hosted: true, more: true },
  { to: "/settings", label: "Settings", icon: SettingsIcon, end: false, section: "Admin", platform: true, more: true },
];

export function Layout() {
  const [searching, setSearching] = useState(false);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const crumbs = useCurrentCrumbs();
  const account = useAccount();
  const nav = NAV.filter(
    (n) => (!n.role || account.can(n.role)) && (!n.platform || account.me.platform_admin) && (!n.hosted || account.me.mode !== "local"),
  );
  useShortcut("k", () => setSearching(true));

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="rail">
        <Link to="/" className="rail-brand" aria-label="datawazi home" title="datawazi">
          <BrandMark size={26} />
        </Link>
        <nav aria-label="Main">
          {(["Workspace", "Admin"] as const).map((section) => {
            const items = nav.filter((n) => n.section === section);
            if (!items.length) return null;
            return (
              <div key={section} className="rail-section" role="group" aria-label={section}>
                {items.map(({ to, label, icon: Icon, end, more }) => (
                  <NavLink key={to} to={to} end={end} aria-label={t(label)} data-tip={t(label)} className={more ? "rail-more" : undefined}>
                    <Icon size={18} aria-hidden="true" />
                    <span className="rail-label" aria-hidden="true">{t(label)}</span>
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <Notifications />
        <WorkspaceMenu more={nav.filter((n) => n.more)} />
      </aside>
      <div className="main-column">
        <header className="topbar">
          <nav className="crumbs" aria-label="Breadcrumb">
            {crumbs.map((c, i) => (
              <span key={i} className="crumb">
                {i > 0 && <span className="crumb-sep" aria-hidden="true">/</span>}
                {c.to && i < crumbs.length - 1 ? <Link to={c.to}>{c.label}</Link> : <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c.label}</span>}
              </span>
            ))}
          </nav>
          <div className="topbar-slot" ref={setSlot} />
          <button className="search-button" onClick={() => setSearching(true)} aria-label="Search (Ctrl K)" title="Search">
            <Search size={15} aria-hidden="true" />
            <kbd>Ctrl K</kbd>
          </button>
        </header>
        <TopbarContext.Provider value={slot}>
          <main className="content" id="main" tabIndex={-1}>
            <Outlet />
          </main>
        </TopbarContext.Provider>
      </div>
      {searching && <QuickSearch onClose={() => setSearching(false)} />}
    </div>
  );
}

function WorkspaceMenu({ more }: { more: NavItem[] }) {
  const account = useAccount();
  const dialogs = useDialogs();
  const toast = useToast();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [instructions, setInstructions] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const hosted = account.me.mode !== "local";

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function go(id: string) {
    setOpen(false);
    account.switchTo(id);
    navigate("/");
  }

  async function create() {
    setOpen(false);
    const name = await dialogs.prompt({ title: "New workspace", label: "Company or team name", confirmLabel: "Create workspace" });
    if (!name) return;
    try {
      const workspace = await accountApi.createWorkspace(name);
      await account.refresh();
      go(workspace.id);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  const roleName = t(account.role[0].toUpperCase() + account.role.slice(1));
  return (
    <div className="workspace-menu" ref={root}>
      {instructions && (
        <InstructionsDialog
          title="AI instructions for the workspace"
          scope="anything in this workspace"
          load={async () => (await accountApi.instructions()).instructions}
          save={(text) => accountApi.setInstructions(text)}
          onClose={() => setInstructions(false)}
        />
      )}
      <button
        className="workspace"
        onClick={() => setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Workspace: ${account.workspace?.name ?? ""}`}
        data-tip={`${account.workspace?.name ?? ""}, ${hosted ? roleName : t("On this computer")}`}
      >
        <span className="workspace-badge" aria-hidden="true">
          {account.workspace?.name[0]?.toUpperCase()}
        </span>
      </button>
      {open && (
        <div className="menu-list up rail-menu" role="menu">
          <div className="menu-note">
            <strong>{account.workspace?.name}</strong>
            <span>{hosted ? roleName : t("On this computer")}</span>
          </div>
          <div className="menu-separator" role="separator" />
          {account.me.workspaces.map((w) => (
            <button key={w.id} role="menuitem" onClick={() => go(w.id)}>
              <span className="workspace-badge small" aria-hidden="true">
                {w.name[0]?.toUpperCase()}
              </span>
              <span className="grow">{w.name}</span>
              {w.id === account.workspace?.id && <Check size={15} aria-label="Current" />}
            </button>
          ))}
          {hosted && (
            <button role="menuitem" onClick={create}>
              <Plus size={16} aria-hidden="true" /> {t("New workspace")}
            </button>
          )}
          <div className="menu-separator" role="separator" />
          {more.map(({ to, label, icon: Icon }) => (
            <Link key={to} to={to} role="menuitem" className="menu-phone-only" onClick={() => setOpen(false)}>
              <Icon size={16} aria-hidden="true" /> {t(label)}
            </Link>
          ))}
          {more.length > 0 && <div className="menu-separator menu-phone-only" role="separator" />}
          {account.can("admin") && (
            <button role="menuitem" onClick={() => (setOpen(false), setInstructions(true))}>
              <ScrollText size={16} aria-hidden="true" /> {t("AI instructions for the workspace")}
            </button>
          )}
          <ThemePicker />
          <DensityPicker />
          <LanguagePicker />
          {hosted && <div className="menu-separator" role="separator" />}
          {hosted && <div className="menu-note">{account.me.user.email}</div>}
          {account.signOut && (
            <button role="menuitem" onClick={() => void account.signOut!()}>
              <LogOut size={16} aria-hidden="true" /> {t("Sign out")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Comfortable or compact spacing for tables, grids and panels. */
function DensityPicker() {
  const [choice, setChoice] = useState<Density>(savedDensity);
  const options: { value: Density; label: string }[] = [
    { value: "comfortable", label: "Comfortable" },
    { value: "compact", label: "Compact" },
  ];
  return (
    <div className="theme-picker" role="radiogroup" aria-label={t("Density")}>
      <span>{t("Density")}</span>
      <span className="segmented compact">
        {options.map(({ value, label }) => (
          <button
            key={value}
            role="radio"
            aria-checked={choice === value}
            className={choice === value ? "on" : ""}
            onClick={() => {
              setChoice(value);
              setDensity(value);
            }}
          >
            {t(label)}
          </button>
        ))}
      </span>
    </div>
  );
}

/** English or Bahasa Malaysia: the main screens and the AI's replies. */
function LanguagePicker() {
  const options: { value: Language; label: string }[] = [
    { value: "en", label: "English" },
    { value: "ms", label: "Bahasa Melayu" },
  ];
  return (
    <div className="theme-picker" role="radiogroup" aria-label="Language">
      <span>{t("Language")}</span>
      <span className="segmented compact">
        {options.map(({ value, label }) => (
          <button key={value} role="radio" aria-checked={language === value} className={language === value ? "on" : ""} onClick={() => language !== value && setLanguage(value)}>
            {label}
          </button>
        ))}
      </span>
    </div>
  );
}

function ThemePicker() {
  const [choice, setChoice] = useState<ThemeChoice>(savedTheme);
  const options: { value: ThemeChoice; label: string; icon: typeof Sun }[] = [
    { value: "light", label: "Light", icon: Sun },
    { value: "dark", label: "Dark", icon: Moon },
    { value: "system", label: "Computer", icon: Monitor },
  ];
  return (
    <div className="theme-picker" role="radiogroup" aria-label="Theme">
      <span>{t("Theme")}</span>
      <span className="segmented compact">
        {options.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            role="radio"
            aria-checked={choice === value}
            className={choice === value ? "on" : ""}
            title={value === "system" ? "Follow the computer's setting" : label}
            onClick={() => {
              setChoice(value);
              setTheme(value);
            }}
          >
            <Icon size={13} aria-hidden="true" /> {t(label)}
          </button>
        ))}
      </span>
    </div>
  );
}

type Item = { label: string; detail: string; to: string; group: string };
const PALETTE_PER_GROUP = 5;

function QuickSearch({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [dashboards, setDashboards] = useState<DashboardSummary[]>([]);
  const account = useAccount();
  const canCreate = account.can("creator");

  useEffect(() => {
    ref.current?.showModal();
    if (canCreate) api.listDatasets().then(setDatasets).catch(() => {});
    dashboardsApi.list().then(setDashboards).catch(() => {});
    // Once, when the search opens: showing the dialog twice throws.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const items = useMemo<Item[]>(() => {
    const all: Item[] = [
      ...dashboards.map((d) => ({ label: d.name, detail: d.dataset ? `Dashboard on ${d.dataset.name}` : "Dashboard", to: `/dashboards/${d.id}`, group: "Dashboards" })),
      ...datasets.map((d) => ({ label: d.name, detail: `Dataset with ${d.sources.length} ${d.sources.length === 1 ? "source" : "sources"}`, to: `/datasets/${d.id}/get-data`, group: "Datasets" })),
      ...(canCreate ? [{ label: "New dashboard", detail: "Start from existing or new data", to: "/?new=1", group: "Actions" }] : []),
      ...(account.me.platform_admin ? [{ label: "Settings", detail: "AI provider and connection test", to: "/settings", group: "Actions" }] : []),
    ];
    const q = query.trim().toLowerCase();
    if (q) return all.filter((i) => i.label.toLowerCase().includes(q) || i.detail.toLowerCase().includes(q));
    // Nothing typed yet: the most recent few of each kind.
    const count: Record<string, number> = {};
    return all.filter((i) => (count[i.group] = (count[i.group] ?? 0) + 1) <= PALETTE_PER_GROUP);
  }, [query, datasets, dashboards, canCreate, account.me.platform_admin]);

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
        {items.map((item, i) => [
          i === 0 || items[i - 1].group !== item.group ? (
            <li key={`heading-${item.group}`} role="presentation" className="search-group">
              {item.group}
            </li>
          ) : null,
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
          </li>,
        ])}
      </ul>
      <p className="search-keys" aria-hidden="true">
        <span><kbd>↑</kbd> <kbd>↓</kbd> to move</span>
        <span><kbd>Enter</kbd> to open</span>
        <span><kbd>Esc</kbd> to close</span>
      </p>
    </dialog>
  );
}
