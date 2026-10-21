import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

// Logo

export function BrandMark({ size = 22 }: { size?: number }) {
  // Placeholder mark until the datawazi logo is designed: rising bars, with the yellow AI dot.
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#1B5FD1" />
      <rect x="6" y="12" width="3" height="6" rx="1" fill="#fff" />
      <rect x="10.5" y="9" width="3" height="9" rx="1" fill="#fff" />
      <rect x="15" y="6" width="3" height="12" rx="1" fill="#fff" opacity=".55" />
      <circle cx="18.5" cy="5.5" r="2.5" fill="#FACC15" stroke="#1B5FD1" strokeWidth="1" />
    </svg>
  );
}

// Toasts: short confirmations and errors that don't take focus.

type Toast = { id: number; text: string; kind: "ok" | "error" | "info" };
const ToastContext = createContext<(text: string, kind?: Toast["kind"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(0);
  const show = useCallback((text: string, kind: Toast["kind"] = "ok") => {
    const id = ++next.current;
    setToasts((current) => [...current.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <span>{t.text}</span>
            <button className="icon" aria-label="Dismiss" onClick={() => setToasts((c) => c.filter((x) => x.id !== t.id))}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

// Dialogs: confirm and name prompts as real, keyboard-friendly dialogs.

type ConfirmOptions = { title: string; body?: ReactNode; confirmLabel: string; danger?: boolean };
type PromptOptions = { title: string; label: string; initial?: string; confirmLabel: string; hint?: string };
type Request =
  | ({ kind: "confirm"; resolve: (ok: boolean) => void } & ConfirmOptions)
  | ({ kind: "prompt"; resolve: (value: string | null) => void } & PromptOptions);

type Dialogs = {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  prompt: (options: PromptOptions) => Promise<string | null>;
};

const DialogContext = createContext<Dialogs>({ confirm: async () => false, prompt: async () => null });

export function DialogProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<Request | null>(null);
  const api = useMemo<Dialogs>(
    () => ({
      confirm: (options) => new Promise((resolve) => setRequest({ kind: "confirm", resolve, ...options })),
      prompt: (options) => new Promise((resolve) => setRequest({ kind: "prompt", resolve, ...options })),
    }),
    [],
  );
  const close = (value: boolean | string | null) => {
    if (!request) return;
    if (request.kind === "confirm") request.resolve(value === true);
    else request.resolve(typeof value === "string" ? value : null);
    setRequest(null);
  };
  return (
    <DialogContext.Provider value={api}>
      {children}
      {request && <DialogView request={request} onClose={close} />}
    </DialogContext.Provider>
  );
}

export function useDialogs() {
  return useContext(DialogContext);
}

function DialogView({ request, onClose }: { request: Request; onClose: (value: boolean | string | null) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState(request.kind === "prompt" ? request.initial ?? "" : "");

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (request.kind === "prompt") {
      if (value.trim()) onClose(value.trim());
    } else onClose(true);
  }

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby="dialog-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose(request.kind === "confirm" ? false : null);
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose(request.kind === "confirm" ? false : null);
      }}
    >
      <form method="dialog" onSubmit={submit}>
        <h2 id="dialog-title">{request.title}</h2>
        {request.kind === "confirm" && request.body && <div className="dialog-body">{request.body}</div>}
        {request.kind === "prompt" && (
          <label className="field">
            <span>{request.label}</span>
            <input autoFocus value={value} onChange={(e) => setValue(e.target.value)} onFocus={(e) => e.target.select()} />
            {request.hint && <small className="muted">{request.hint}</small>}
          </label>
        )}
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={() => onClose(request.kind === "confirm" ? false : null)}>
            Cancel
          </button>
          <button
            type="submit"
            className={request.kind === "confirm" && request.danger ? "danger-solid" : ""}
            autoFocus={request.kind === "confirm"}
            disabled={request.kind === "prompt" && !value.trim()}
          >
            {request.confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}

// Breadcrumbs: pages say where they are; the top bar shows it.

export type Crumb = { label: string; to?: string };
const CrumbContext = createContext<{ crumbs: Crumb[]; setCrumbs: (c: Crumb[]) => void }>({ crumbs: [], setCrumbs: () => {} });

export function CrumbProvider({ children }: { children: ReactNode }) {
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  return <CrumbContext.Provider value={{ crumbs, setCrumbs }}>{children}</CrumbContext.Provider>;
}

export function useCrumbs(crumbs: Crumb[] | null) {
  const { setCrumbs } = useContext(CrumbContext);
  const key = JSON.stringify(crumbs);
  useEffect(() => {
    if (crumbs) setCrumbs(crumbs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setCrumbs]);
}

export function useCurrentCrumbs() {
  return useContext(CrumbContext).crumbs;
}

// Menus: secondary actions behind one "More" button.

export type MenuItem = { label: string; icon?: ReactNode; onSelect?: () => void; to?: string; danger?: boolean };

export function Menu({ label, icon, items }: { label: string; icon: ReactNode; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const [alignLeft, setAlignLeft] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    // Open towards the side with room, so the list never leaves the screen.
    setAlignLeft((root.current?.getBoundingClientRect().right ?? 999) < 240);
    first.current?.focus();
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="menu" ref={root}>
      <button className="secondary icon-button" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        {icon}
      </button>
      {open && (
        <div className={`menu-list ${alignLeft ? "align-left" : ""}`} role="menu">
          {items.map((item, i) => {
            const content = (
              <>
                {item.icon}
                {item.label}
              </>
            );
            const ref = i === 0 ? (el: HTMLElement | null) => void (first.current = el) : undefined;
            return item.to ? (
              <a key={item.label} ref={ref} role="menuitem" href={item.to} className={item.danger ? "danger" : ""} onClick={() => setOpen(false)}>
                {content}
              </a>
            ) : (
              <button
                key={item.label}
                ref={ref}
                role="menuitem"
                className={item.danger ? "danger" : ""}
                onClick={() => {
                  setOpen(false);
                  item.onSelect?.();
                }}
              >
                {content}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Empty states and loading placeholders

export function EmptyState(props: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      {props.icon && <div className="empty-icon">{props.icon}</div>}
      <h2>{props.title}</h2>
      {props.children && <div className="muted">{props.children}</div>}
      {props.action && <div className="empty-action">{props.action}</div>}
    </div>
  );
}

export function Skeleton({ lines = 3, height = 14 }: { lines?: number; height?: number }) {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} style={{ height, width: `${92 - i * 14}%` }} />
      ))}
    </div>
  );
}

// The top bar's right side: editors put their tabs and actions there (Layout provides the spot).

export const TopbarContext = createContext<HTMLElement | null>(null);

export function TopbarActions({ children }: { children: ReactNode }) {
  const slot = useContext(TopbarContext);
  return slot ? createPortal(children, slot) : null;
}

/** Ctrl (or Cmd) + a letter, unless the page has a dialog open. */
export function useShortcut(key: string, handler: () => void) {
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === key && !document.querySelector("dialog[open]")) {
        e.preventDefault();
        latest.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [key]);
}

/** A menu opened at a point (a column header, a right-click), kept on screen; Esc or a click outside closes it. */
export function PopMenu({ x, y, onClose, label, children }: { x: number; y: number; onClose: () => void; label: string; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ left: x, top: y });

  useEffect(() => {
    const el = box.current;
    if (el) {
      const { width, height } = el.getBoundingClientRect();
      setPlace({ left: Math.max(8, Math.min(x, window.innerWidth - width - 8)), top: Math.max(8, Math.min(y, window.innerHeight - height - 8)) });
      el.querySelector<HTMLElement>("button:not(:disabled), input, select")?.focus();
    }
    const onDown = (e: MouseEvent) => !box.current?.contains(e.target as Node) && onClose();
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [x, y, onClose]);

  return createPortal(
    <div ref={box} className="pop-menu" role="menu" aria-label={label} style={place}>
      {children}
    </div>,
    document.body,
  );
}
