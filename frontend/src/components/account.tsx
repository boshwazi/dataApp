import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { Mail } from "lucide-react";
import { accountApi, ApiError, configureSession, type Me, type Role, type WorkspaceRef } from "../api";
import { BrandMark } from "./ui";

const WORKSPACE_KEY = "workspace";

type Account = {
  me: Me;
  workspace: WorkspaceRef | null;
  role: Role;
  can: (role: Role) => boolean;
  switchTo: (id: string) => void;
  refresh: () => Promise<Me>;
  signOut: (() => Promise<void>) | null;
};

const AccountContext = createContext<Account | null>(null);

/** The signed-in person and their current workspace. Local mode has one person and one workspace. */
export function useAccount(): Account {
  const account = useContext(AccountContext);
  if (!account) throw new Error("useAccount outside AccountProvider");
  return account;
}

const ORDER: Role[] = ["viewer", "creator", "admin"];

function savedWorkspace(): string | null {
  try {
    return localStorage.getItem(WORKSPACE_KEY);
  } catch {
    return null;
  }
}

type State =
  | { status: "loading" }
  | { status: "signed-out"; client: SupabaseClient; problem?: string }
  | { status: "ready"; me: Me; workspaceId: string | null }
  | { status: "error"; message: string };

export function AccountProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const client = useRef<SupabaseClient | null>(null);

  const load = useCallback(async () => {
    const me = await accountApi.me();
    const saved = savedWorkspace();
    const workspaceId = me.workspaces.find((w) => w.id === saved)?.id ?? me.workspaces[0]?.id ?? null;
    configureSession({ workspace: workspaceId });
    setState({ status: "ready", me, workspaceId });
    return me;
  }, []);

  useEffect(() => {
    let unsubscribe = () => {};
    (async () => {
      const config = await accountApi.config();
      if (config.mode === "local" || !config.supabase_url || !config.supabase_key) {
        await load();
        return;
      }
      const supabase = createClient(config.supabase_url, config.supabase_key, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false, flowType: "pkce" },
      });
      client.current = supabase;
      configureSession({
        token: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
        onSignedOut: () => setState({ status: "signed-out", client: supabase }),
      });

      // Coming back from a sign-in link or Google/Microsoft: swap the one-time code for a session here,
      // so a failure is shown instead of silently landing on the sign-in page again.
      const url = new URL(window.location.href);
      const code = url.searchParams.get("code");
      const linkError = url.searchParams.get("error_description") ?? new URLSearchParams(url.hash.slice(1)).get("error_description");
      let problem: string | undefined = linkError ?? undefined;
      if (code || linkError) {
        if (code) {
          const { error } = await supabase.auth.exchangeCodeForSession(code);
          if (error) problem = error.message;
        }
        ["code", "error", "error_code", "error_description"].forEach((k) => url.searchParams.delete(k));
        window.history.replaceState(null, "", url.pathname + url.search);
      }

      const { data } = supabase.auth.onAuthStateChange((event, session) => {
        if (!session) setState({ status: "signed-out", client: supabase, problem });
        // Run outside the auth event: calling the API from inside it waits on the auth lock and hangs.
        else if (event === "SIGNED_IN" || event === "INITIAL_SESSION") setTimeout(() => void load(), 0);
      });
      unsubscribe = () => data.subscription.unsubscribe();
    })().catch((e) => setState({ status: "error", message: (e as Error).message }));
    return () => unsubscribe();
  }, [load]);

  const account = useMemo<Account | null>(() => {
    if (state.status !== "ready") return null;
    const workspace = state.me.workspaces.find((w) => w.id === state.workspaceId) ?? null;
    const role = workspace?.role ?? "viewer";
    return {
      me: state.me,
      workspace,
      role,
      can: (needed) => ORDER.indexOf(role) >= ORDER.indexOf(needed),
      switchTo: (id) => {
        try {
          localStorage.setItem(WORKSPACE_KEY, id);
        } catch {
          // private mode: the choice lasts for this tab
        }
        configureSession({ workspace: id });
        setState((s) => (s.status === "ready" ? { ...s, workspaceId: id } : s));
      },
      refresh: load,
      signOut: client.current
        ? async () => {
            await client.current!.auth.signOut();
          }
        : null,
    };
  }, [state, load]);

  if (state.status === "loading") return <Splash />;
  if (state.status === "error") return <Splash message={`Can't reach datawazi: ${state.message}`} />;
  if (state.status === "signed-out") return <SignIn client={state.client} problem={state.problem} />;
  return <AccountContext.Provider value={account}>{children}</AccountContext.Provider>;
}

/** Content needs a workspace; without one, offer to create one (an invite link works as well). */
export function WorkspaceGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const location = useLocation();
  if (account.workspace || location.pathname.startsWith("/invite/")) {
    // A new workspace starts a fresh tree, so no page keeps the previous company's data.
    return <div key={account.workspace?.id ?? "none"}>{children}</div>;
  }
  return <CreateWorkspace />;
}

function Splash({ message }: { message?: string }) {
  return (
    <div className="auth-page">
      <div className="auth-card center">
        <BrandMark size={32} />
        <p className={message ? "bad" : "muted"}>{message ?? "Loading…"}</p>
      </div>
    </div>
  );
}

function SignIn({ client, problem }: { client: SupabaseClient; problem?: string }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(
    problem
      ? `That sign-in link didn't work (${problem}). Links work once, in the browser where you asked for them. Request a new one below.`
      : null,
  );
  const back = window.location.href;

  const [code, setCode] = useState("");

  async function verifyCode(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    // The code works in any browser or device, unlike the link, which needs the browser that asked for it.
    const { error } = await client.auth.verifyOtp({ email: email.trim(), token: code.trim(), type: "email" });
    setBusy(false);
    if (error) setError(error.message);
  }

  async function sendLink(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await client.auth.signInWithOtp({ email: email.trim(), options: { emailRedirectTo: back } });
    setBusy(false);
    if (error) setError(error.message);
    else setSent(true);
  }

  async function oauth(provider: "google" | "azure") {
    setError(null);
    const { error } = await client.auth.signInWithOAuth({
      provider,
      options: { redirectTo: back, scopes: provider === "azure" ? "email" : undefined },
    });
    if (error) setError(error.message);
  }

  return (
    <div className="auth-page">
      <main className="auth-card">
        <div className="auth-brand">
          <BrandMark size={28} /> datawazi
        </div>
        {sent ? (
          <>
            <h1>Check your inbox</h1>
            <p className="muted">
              We sent a sign-in email to <strong>{email}</strong>. Type the code from it here, or open its link in this
              browser.
            </p>
            <form onSubmit={verifyCode} className="auth-form">
              <label className="field">
                <span>Code from the email</span>
                <input
                  autoFocus
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  maxLength={10}
                  className="code-input"
                />
              </label>
              <button type="submit" disabled={busy || code.length < 6}>
                {busy ? "Checking…" : "Sign in"}
              </button>
            </form>
            <button className="link" onClick={() => setSent(false)}>
              Use a different email
            </button>
          </>
        ) : (
          <>
            <h1>Sign in</h1>
            <p className="muted">Use your work email. New here? Signing in creates your account.</p>
            <div className="auth-providers">
              <button className="secondary" onClick={() => oauth("google")}>
                Continue with Google
              </button>
              <button className="secondary" onClick={() => oauth("azure")}>
                Continue with Microsoft
              </button>
            </div>
            <div className="auth-divider">
              <span>or</span>
            </div>
            <form onSubmit={sendLink} className="auth-form">
              <label className="field">
                <span>Work email</span>
                <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
              </label>
              <button type="submit" disabled={busy || !email.trim()}>
                <Mail size={16} aria-hidden="true" /> {busy ? "Sending…" : "Email me a sign-in link"}
              </button>
            </form>
          </>
        )}
        {error && <p className="bad small">{error}</p>}
      </main>
    </div>
  );
}

function CreateWorkspace() {
  const account = useAccount();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const workspace = await accountApi.createWorkspace(name);
      await account.refresh();
      account.switchTo(workspace.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <main className="auth-card">
        <div className="auth-brand">
          <BrandMark size={28} /> datawazi
        </div>
        <h1>Set up your workspace</h1>
        <p className="muted">
          A workspace holds your company's data and dashboards. You can invite your team next. Joining a colleague's
          workspace instead? Open the invite link they sent you.
        </p>
        <form onSubmit={create} className="auth-form">
          <label className="field">
            <span>Company or team name</span>
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Ana Trading" />
          </label>
          <button type="submit" disabled={busy || !name.trim()}>
            {busy ? "Creating…" : "Create workspace"}
          </button>
        </form>
        {error && <p className="bad small">{error}</p>}
        <p className="muted small">Signed in as {account.me.user.email}.</p>
        {account.signOut && (
          <button className="link small" onClick={() => void account.signOut!()}>
            Sign out
          </button>
        )}
      </main>
    </div>
  );
}
