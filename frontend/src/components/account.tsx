import {
  Suspense, createContext, lazy, useCallback, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { SupabaseClient } from "@supabase/supabase-js";
import { Mail } from "lucide-react";
import { accountApi, ApiError, configureSession, downloadFile, type Me, type Role, type WorkspaceRef } from "../api";
import { BrandMark } from "./ui";
import { SignInChart } from "./SignInChart";
import { OnboardingShell } from "./OnboardingSteps";
import { t } from "../i18n";

const WORKSPACE_KEY = "workspace";
const Landing = lazy(() => import("../pages/Landing").then((m) => ({ default: m.Landing })));

type Account = {
  me: Me;
  workspace: WorkspaceRef | null;
  role: Role;
  can: (role: Role) => boolean;
  switchTo: (id: string) => void;
  refresh: () => Promise<Me>;
  signOut: (() => Promise<void>) | null;
  /** The sign-in service, for the account page (null in local mode, where there is no account). */
  client: SupabaseClient | null;
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
  | { status: "second-step"; client: SupabaseClient; note?: string }
  | { status: "two-step-setup"; client: SupabaseClient; note: string }
  | { status: "ready"; me: Me; workspaceId: string | null }
  | { status: "error"; message: string };

export function AccountProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const location = useLocation();
  const client = useRef<SupabaseClient | null>(null);

  const load = useCallback(async () => {
    const me = await accountApi.me();
    const saved = savedWorkspace();
    const workspaceId = me.workspaces.find((w) => w.id === saved)?.id ?? me.workspaces[0]?.id ?? null;
    configureSession({ workspace: workspaceId });
    setState({ status: "ready", me, workspaceId });
    return me;
  }, []);

  /** People with an authenticator app confirm a code first (Supabase's assurance level 2); everyone else goes on. */
  const secondStepOrLoad = useCallback(
    async (supabase: SupabaseClient) => {
      const { data } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
      if (data && data.nextLevel === "aal2" && data.currentLevel !== "aal2") setState({ status: "second-step", client: supabase });
      else await load();
    },
    [load],
  );

  useEffect(() => {
    let unsubscribe = () => {};
    (async () => {
      const config = await accountApi.config();
      if (config.mode === "local" || !config.supabase_url || !config.supabase_key) {
        await load();
        return;
      }
      // The sign-in library loads only where sign-in is on (not in local mode).
      const { createClient } = await import("@supabase/supabase-js");
      const supabase = createClient(config.supabase_url, config.supabase_key, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false, flowType: "pkce" },
      });
      client.current = supabase;
      configureSession({
        token: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
        onSignedOut: () => setState({ status: "signed-out", client: supabase }),
        // The workspace requires two-step sign-in: the code if they have an authenticator, else setting one up.
        onTwoStepRequired: (note) =>
          void supabase.auth.mfa.listFactors().then(({ data }) =>
            setState(
              data?.totp.some((f) => f.status === "verified")
                ? { status: "second-step", client: supabase, note }
                : { status: "two-step-setup", client: supabase, note },
            ),
          ),
      });

      // Coming back from a sign-in link or Google/Microsoft: swap the one-time code for a session here,
      // so a failure is shown instead of silently landing on the sign-in page again.
      const url = new URL(window.location.href);
      const code = url.searchParams.get("code");
      const linkError = url.searchParams.get("error_description") ?? new URLSearchParams(url.hash.slice(1)).get("error_description");
      let problem: string | undefined = linkError ?? undefined;
      // Invite emails sent by the server sign people in with tokens in the address's # part.
      const hash = new URLSearchParams(url.hash.slice(1));
      const access_token = hash.get("access_token");
      const refresh_token = hash.get("refresh_token");
      if (access_token && refresh_token) {
        const { error } = await supabase.auth.setSession({ access_token, refresh_token });
        if (error) problem = error.message;
        window.history.replaceState(null, "", url.pathname + url.search);
      }
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
        else if (event === "SIGNED_IN" || event === "INITIAL_SESSION" || event === "MFA_CHALLENGE_VERIFIED") {
          setTimeout(() => void secondStepOrLoad(supabase), 0);
        }
      });
      unsubscribe = () => data.subscription.unsubscribe();
    })().catch((e) => setState({ status: "error", message: (e as Error).message }));
    return () => unsubscribe();
  }, [load, secondStepOrLoad]);

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
      client: client.current,
    };
  }, [state, load]);

  if (state.status === "loading") return <Splash />;
  if (state.status === "error") return <Splash message={`Can't reach datawazi: ${state.message}`} />;
  if (state.status === "second-step") return <SecondStep client={state.client} note={state.note} />;
  if (state.status === "two-step-setup") return <TwoStepSetup client={state.client} note={state.note} />;
  if (state.status === "signed-out") {
    // Visitors see the landing page at /; every other address (and its Sign in links) asks them to sign in.
    if (location.pathname === "/" && !state.problem) {
      return (
        <Suspense fallback={<Splash />}>
          <Landing />
        </Suspense>
      );
    }
    return <SignIn client={state.client} problem={state.problem} />;
  }
  return <AccountContext.Provider value={account}>{children}</AccountContext.Provider>;
}

/** Content needs a workspace; without one, offer to create one (an invite link works as well). */
export function WorkspaceGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const location = useLocation();
  if (account.workspace?.settings?.delete_after && !location.pathname.startsWith("/invite/")) {
    return <WorkspaceClosed />;
  }
  if (account.workspace || location.pathname.startsWith("/invite/")) {
    // A new workspace starts a fresh tree, so no page keeps the previous company's data.
    return <div key={account.workspace?.id ?? "none"}>{children}</div>;
  }
  return <CreateWorkspace />;
}

/** A workspace waiting to be deleted: closed to everyone; an admin can still download the export or cancel. */
function WorkspaceClosed() {
  const account = useAccount();
  const [busy, setBusy] = useState<"export" | "restore" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const workspace = account.workspace!;
  const when = new Date(workspace.settings!.delete_after!).toLocaleDateString(undefined, { dateStyle: "long" });
  const admin = account.can("admin");
  const others = account.me.workspaces.filter((w) => w.id !== workspace.id && !w.settings?.delete_after);

  async function act(kind: "export" | "restore") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "export") {
        const safe = workspace.name.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "") || "workspace";
        await downloadFile("/api/workspaces/current/export", `datawazi-${safe}.zip`);
      } else {
        await accountApi.restoreWorkspace();
        await account.refresh();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="auth-page">
      <main className="auth-card closed-card">
        <div className="auth-brand">
          <BrandMark size={28} /> datawazi
        </div>
        <h1>{t("{name} is closed", { name: workspace.name })}</h1>
        <p className="muted">
          {admin
            ? t("It will be deleted with all its data on {when}. Until then you can download the export, or cancel and everything comes back as it was.", { when })
            : t("It will be deleted on {when}. If this is a mistake, ask one of its admins to cancel.", { when })}
        </p>
        {admin && (
          <div className="closed-actions">
            <button onClick={() => void act("restore")} disabled={busy !== null}>
              {busy === "restore" ? t("Cancelling…") : t("Cancel the deletion")}
            </button>
            <button className="secondary" onClick={() => void act("export")} disabled={busy !== null}>
              {busy === "export" ? t("Preparing the export…") : t("Download the export")}
            </button>
          </div>
        )}
        {error && <p className="signin-error" role="alert">{error}</p>}
        {others.length > 0 && (
          <div className="closed-switch">
            <p className="muted small">{t("Your other workspaces")}</p>
            {others.map((w) => (
              <button key={w.id} className="secondary" onClick={() => account.switchTo(w.id)}>
                {w.name}
              </button>
            ))}
          </div>
        )}
        {account.signOut && (
          <button className="link small" onClick={() => void account.signOut!()}>
            {t("Sign out")}
          </button>
        )}
      </main>
    </div>
  );
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

type SignInStep = "link" | "code" | "send" | "google" | "azure";

const PROVIDER_NAMES = { google: "Google", azure: "Microsoft" } as const;
const PERSONAL_MAIL = /@(gmail|googlemail|yahoo|ymail|hotmail|outlook|live|icloud|me|proton|protonmail)\./i;
const RESEND_SECONDS = 60;

/** Supabase's sign-in errors, said plainly with what to do next. */
export function explainSignIn(message: string, step: SignInStep): string {
  const m = message.toLowerCase();
  const provider = step === "google" || step === "azure" ? PROVIDER_NAMES[step] : "Google or Microsoft";
  // Supabase reports a provider that isn't set up by sending the person back with it in the address.
  if (m.includes("not enabled") || m.includes("unsupported provider")) {
    return `Signing in with ${provider} isn't switched on yet. Use your email for now.`;
  }
  if (step === "google" || step === "azure") {
    return `${provider} couldn't sign you in (${message}). Try again, or use your email.`;
  }
  if (m.includes("rate limit") || m.includes("security purposes") || m.includes("too many")) {
    return "Too many sign-in emails were asked for. Wait a minute, then try again.";
  }
  if (step === "link") {
    if (m.includes("code verifier") || m.includes("both auth code")) {
      return "This link was opened in a different browser from the one you asked in. Type the code from the email instead, or open the link in that browser.";
    }
    if (m.includes("expired") || m.includes("invalid")) {
      return "That sign-in link has expired or was already used. Each link works once. Ask for a new one below.";
    }
    return `That sign-in link didn't work (${message}). Ask for a new one below.`;
  }
  if (step === "code" && (m.includes("expired") || m.includes("invalid") || m.includes("token"))) {
    return "That code is wrong or has expired. Check the newest email, or send a new one.";
  }
  if (step === "send" && m.includes("invalid") && m.includes("email")) {
    return "That doesn't look like an email address. Check it and try again.";
  }
  return message;
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">
      <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
      <path fill="#FBBC05" d="M3.97 10.72A5.4 5.4 0 0 1 3.68 9c0-.6.1-1.18.29-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.05l3.01-2.33z" />
      <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
    </svg>
  );
}

function MicrosoftIcon() {
  return (
    <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">
      <rect x="0" y="0" width="8.5" height="8.5" fill="#F25022" />
      <rect x="9.5" y="0" width="8.5" height="8.5" fill="#7FBA00" />
      <rect x="0" y="9.5" width="8.5" height="8.5" fill="#00A4EF" />
      <rect x="9.5" y="9.5" width="8.5" height="8.5" fill="#FFB900" />
    </svg>
  );
}

/** The right half of the sign-in screen: a short line over a chart that answers the pointer. */
function SignInAside() {
  return (
    <aside className="signin-aside">
      <div className="signin-aside-text">
        <h2>Clean data. Checked numbers.</h2>
        <p>Your data becomes dashboards your team can trust.</p>
      </div>
      <SignInChart />
    </aside>
  );
}

function SignInHelp() {
  return (
    <details className="signin-help">
      <summary>Trouble signing in?</summary>
      <ul>
        <li>The email comes from datawazi. If it isn't in your inbox after a minute, check spam or junk.</li>
        <li>The code works in any browser. The link only works in the browser where you asked for it.</li>
        <li>Each link and code works once. Asking again sends a new one and the old one stops working.</li>
        <li>Invited by your company? Sign in with the email address the invite was sent to.</li>
      </ul>
    </details>
  );
}

function SignIn({ client, problem }: { client: SupabaseClient; problem?: string }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(problem ? explainSignIn(problem, "link") : null);
  const [code, setCode] = useState("");
  const [wait, setWait] = useState(0);
  const back = window.location.href;

  useEffect(() => {
    if (wait <= 0) return;
    const timer = setTimeout(() => setWait((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);

  async function verifyCode(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    // The code works in any browser or device, unlike the link, which needs the browser that asked for it.
    const { error } = await client.auth.verifyOtp({ email: email.trim(), token: code.trim(), type: "email" });
    setBusy(false);
    if (error) setError(explainSignIn(error.message, "code"));
  }

  async function sendLink(event?: FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await client.auth.signInWithOtp({ email: email.trim(), options: { emailRedirectTo: back } });
    setBusy(false);
    if (error) {
      setError(explainSignIn(error.message, "send"));
    } else {
      setSent(true);
      setCode("");
      setWait(RESEND_SECONDS);
    }
  }

  async function oauth(provider: "google" | "azure") {
    setError(null);
    const { error } = await client.auth.signInWithOAuth({
      provider,
      options: { redirectTo: back, scopes: provider === "azure" ? "email" : undefined },
    });
    if (error) setError(explainSignIn(error.message, provider));
  }

  const personal = PERSONAL_MAIL.test(email.trim());

  return (
    <div className="signin">
      <main className="signin-main">
        <div className="signin-panel">
          <a href="/" className="auth-brand signin-brand" aria-label="datawazi home">
            <BrandMark size={28} /> datawazi
          </a>
          {sent ? (
            <>
              <h1>Check your inbox</h1>
              <p className="muted">
                We sent a sign-in email to <strong>{email}</strong>. Type the code from it here, or open its link in
                this browser.
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
                    aria-describedby={error ? "signin-error" : undefined}
                  />
                </label>
                <button type="submit" disabled={busy || code.length < 6}>
                  {busy ? "Checking…" : "Sign in"}
                </button>
              </form>
              <div className="signin-row">
                <button className="link" onClick={() => void sendLink()} disabled={busy || wait > 0}>
                  {wait > 0 ? `Send a new email in ${wait}s` : "Send a new email"}
                </button>
                <button className="link" onClick={() => { setSent(false); setError(null); }}>
                  Use a different email
                </button>
              </div>
            </>
          ) : (
            <>
              <h1>Sign in to datawazi</h1>
              <p className="muted">New here? Signing in creates your account.</p>
              <div className="auth-providers">
                <button className="secondary signin-provider" onClick={() => oauth("google")}>
                  <GoogleIcon /> Continue with Google
                </button>
                <button className="secondary signin-provider" onClick={() => oauth("azure")}>
                  <MicrosoftIcon /> Continue with Microsoft
                </button>
              </div>
              <div className="auth-divider">
                <span>or with your email</span>
              </div>
              <form onSubmit={sendLink} className="auth-form">
                <label className="field">
                  <span>Work email</span>
                  <input
                    type="email"
                    required
                    autoComplete="email"
                    spellCheck={false}
                    placeholder="name@company.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    aria-describedby={personal ? "signin-hint" : error ? "signin-error" : undefined}
                  />
                </label>
                {personal && (
                  <p id="signin-hint" className="signin-hint">
                    Joining your company's workspace? Use the work email your invite was sent to.
                  </p>
                )}
                <button type="submit" disabled={busy}>
                  <Mail size={16} aria-hidden="true" /> {busy ? "Sending…" : "Email me a code"}
                </button>
              </form>
            </>
          )}
          {error && (
            <p id="signin-error" className="signin-error" role="alert">
              {error}
            </p>
          )}
          <SignInHelp />
        </div>
      </main>
      <SignInAside />
    </div>
  );
}

/** Signing in with two-step sign-in on: the code from the authenticator app, before anything else loads. */
function SecondStep({ client, note }: { client: SupabaseClient; note?: string }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function verify(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const factors = await client.auth.mfa.listFactors();
    const factor = factors.data?.totp.find((f) => f.status === "verified");
    if (!factor) {
      setBusy(false);
      return setError("No authenticator app is set up for this account. Sign out and ask your admin for help.");
    }
    const { error } = await client.auth.mfa.challengeAndVerify({ factorId: factor.id, code: code.trim() });
    setBusy(false);
    if (error) setError("That code didn't work. Codes change every 30 seconds; enter the one showing now.");
    // On success Supabase raises MFA_CHALLENGE_VERIFIED, and the app loads.
  }

  return (
    <div className="signin">
      <main className="signin-main">
        <div className="signin-panel">
          <div className="auth-brand signin-brand">
            <BrandMark size={28} /> datawazi
          </div>
          <h1>Enter your code</h1>
          {note && <p className="notice">{note}</p>}
          <p className="muted">Open your authenticator app and type the 6-digit code for datawazi.</p>
          <form onSubmit={verify} className="auth-form">
            <label className="field">
              <span>Code</span>
              <input
                autoFocus
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                maxLength={6}
                className="code-input"
                aria-describedby={error ? "second-step-error" : undefined}
              />
            </label>
            <button type="submit" disabled={busy || code.length < 6}>
              {busy ? "Checking…" : "Continue"}
            </button>
          </form>
          {error && (
            <p id="second-step-error" className="signin-error" role="alert">
              {error}
            </p>
          )}
          <button className="link" onClick={() => void client.auth.signOut()}>
            Sign out
          </button>
        </div>
      </main>
      <aside className="signin-aside">
        <div className="signin-aside-text">
          <h2>Clean data. Checked numbers.</h2>
          <p>Your data becomes dashboards your team can trust.</p>
        </div>
        <SignInChart />
      </aside>
    </div>
  );
}

const TwoStep = lazy(() => import("../pages/Account").then((m) => ({ default: m.TwoStep })));

/** The workspace requires two-step sign-in and this person has no authenticator yet: set one up, then go on. */
function TwoStepSetup({ client, note }: { client: SupabaseClient; note: string }) {
  return (
    <div className="signin">
      <main className="signin-main">
        <div className="signin-panel two-step-panel">
          <div className="auth-brand signin-brand">
            <BrandMark size={28} /> datawazi
          </div>
          <h1>Set up two-step sign-in</h1>
          <p className="notice">{note}</p>
          <Suspense fallback={<p className="muted">Loading…</p>}>
            <TwoStep client={client} onDone={() => window.location.reload()} />
          </Suspense>
          <button className="link" onClick={() => void client.auth.signOut()}>
            Sign out
          </button>
        </div>
      </main>
      <aside className="signin-aside">
        <div className="signin-aside-text">
          <h2>Clean data. Checked numbers.</h2>
          <p>Your data becomes dashboards your team can trust.</p>
        </div>
        <SignInChart />
      </aside>
    </div>
  );
}

function CreateWorkspace() {
  const account = useAccount();
  const navigate = useNavigate();
  const [open, setOpen] = useState<{ id: string; name: string }[]>([]);
  const [joining, setJoining] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (account.me.mode !== "local") accountApi.joinable().then((r) => setOpen(r.workspaces), () => setOpen([]));
  }, [account.me.mode]);

  async function join(id: string) {
    setJoining(id);
    setError(null);
    try {
      await accountApi.join(id);
      await account.refresh();
      account.switchTo(id);
      navigate("/");
    } catch (e) {
      setError((e as Error).message);
      setJoining(null);
    }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return setError(t("Give the workspace a name, such as your company's."));
    setBusy(true);
    setError(null);
    try {
      const workspace = await accountApi.createWorkspace(name);
      await account.refresh();
      account.switchTo(workspace.id);
      navigate("/start"); // then invite the team and start with data
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
      setBusy(false);
    }
  }

  return (
    <OnboardingShell step={1}>
      {open.length > 0 && (
        <div className="join-offer">
          <p>{t("Your colleagues are already on datawazi.")}</p>
          {open.map((w) => (
            <button key={w.id} className="secondary join-button" onClick={() => void join(w.id)} disabled={joining !== null}>
              {joining === w.id ? t("Joining…") : t("Join {name}", { name: w.name })}
            </button>
          ))}
          <div className="auth-divider">
            <span>{t("or make a new one")}</span>
          </div>
        </div>
      )}
      <h1>{t("Name your workspace")}</h1>
      <p className="muted">
        {t("A workspace holds your company's data and dashboards. Joining a colleague's workspace instead? Open the invite link they sent you.")}
      </p>
      <form onSubmit={create} className="auth-form">
        <label className="field">
          <span>{t("Company or team name")}</span>
          <input autoFocus autoComplete="organization" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("For example: Ana Trading")} maxLength={60} />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? t("Creating…") : t("Continue")}
        </button>
      </form>
      {error && <p className="signin-error" role="alert">{error}</p>}
      <p className="muted small onboard-signed-in">
        {t("Signed in as {email}.", { email: account.me.user.email })}{" "}
        {account.signOut && (
          <button className="link small" onClick={() => void account.signOut!()}>
            {t("Sign out")}
          </button>
        )}
      </p>
    </OnboardingShell>
  );
}
