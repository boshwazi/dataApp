import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { Factor, SupabaseClient } from "@supabase/supabase-js";
import { Copy, KeyRound, LogOut, ShieldCheck } from "lucide-react";
import { accountApi } from "../api";
import { useAccount } from "../components/account";
import { useCrumbs, useDialogs, useToast } from "../components/ui";

/**
 * Your account: name, email, two-step sign-in, signing out everywhere, and deleting the account. Sign-in lives
 * in Supabase, so these talk to it directly; deleting goes through the server, which keeps every workspace's
 * last admin.
 */
export function Account() {
  useCrumbs([{ label: "Your account" }]);
  const account = useAccount();
  const client = account.client;

  return (
    <div className="page account-page">
      <h1>Your account</h1>
      {client ? (
        <>
          <p className="muted">Signed in as {account.me.user.email}.</p>
          <Profile client={client} />
          <EmailChange client={client} email={account.me.user.email} />
          <TwoStep client={client} />
          <Sessions client={client} />
          <DeleteAccount />
        </>
      ) : (
        <p className="notice">
          On this computer there's one person and no sign-in, so there's no account to change. Name, email, two-step
          sign-in and deleting an account come with the hosted version.
        </p>
      )}
    </div>
  );
}

function Profile({ client }: { client: SupabaseClient }) {
  const toast = useToast();
  const [saved, setSaved] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    client.auth.getUser().then(({ data }) => {
      const current = String(data.user?.user_metadata?.full_name ?? "");
      setSaved(current);
      setName(current);
    });
  }, [client]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const { error } = await client.auth.updateUser({ data: { full_name: name.trim() } });
    setBusy(false);
    if (error) return toast(error.message, "error");
    setSaved(name.trim());
    toast("Name saved");
  }

  return (
    <section className="card settings-section">
      <h2 className="section-title">Name</h2>
      <form className="settings-row" onSubmit={save}>
        <span className="account-avatar" aria-hidden="true">
          {(name.trim() || "?")[0].toUpperCase()}
        </span>
        <label className="field grow">
          <span className="sr-only">Your name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" autoComplete="name" disabled={saved === null} />
        </label>
        {saved !== null && name.trim() !== saved && (
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        )}
      </form>
    </section>
  );
}

function EmailChange({ client, email }: { client: SupabaseClient; email: string }) {
  const [open, setOpen] = useState(false);
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await client.auth.updateUser({ email: next.trim() }, { emailRedirectTo: `${window.location.origin}/account` });
    setBusy(false);
    if (error) return setError(error.message);
    setSent(next.trim());
    setOpen(false);
  }

  return (
    <section className="card settings-section">
      <h2 className="section-title">Email</h2>
      <div className="settings-row">
        <span className="grow">{email}</span>
        {!open && (
          <button className="secondary" onClick={() => setOpen(true)}>
            Change email
          </button>
        )}
      </div>
      {sent && (
        <p className="notice reveal" role="status">
          We sent a confirmation link to {email} and to {sent}. Your email changes once both are opened.
        </p>
      )}
      {open && (
        <form className="settings-row reveal" onSubmit={send}>
          <label className="field grow">
            <span className="sr-only">New email</span>
            <input
              type="email"
              required
              autoFocus
              spellCheck={false}
              autoComplete="email"
              placeholder="new@company.com"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              aria-describedby={error ? "email-error" : undefined}
            />
          </label>
          <button type="submit" disabled={busy}>
            {busy ? "Sending…" : "Send confirmation"}
          </button>
          <button type="button" className="link" onClick={() => setOpen(false)}>
            Cancel
          </button>
        </form>
      )}
      {error && (
        <p id="email-error" className="signin-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

type Setup = { factorId: string; qr: string; secret: string };

/** The QR code as an image source that always loads: an SVG data URI with a raw "#" (say, fill="#000") is cut
    off there, so the SVG is decoded and encoded again. */
function qrSource(qr: string) {
  const comma = qr.indexOf(",");
  if (!qr.startsWith("data:image/svg+xml") || comma < 0) return qr;
  let svg = qr.slice(comma + 1);
  try {
    svg = decodeURIComponent(svg);
  } catch {
    // already plain text
  }
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function TwoStep({ client, onDone }: { client: SupabaseClient; onDone?: () => void }) {
  const toast = useToast();
  const dialogs = useDialogs();
  const [factors, setFactors] = useState<Factor[] | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data } = await client.auth.mfa.listFactors();
    setFactors(data?.totp ?? []);
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const active = factors?.find((f) => f.status === "verified");

  async function begin() {
    setBusy(true);
    setError(null);
    // An unfinished earlier setup would block a new one; clear it first.
    for (const f of factors ?? []) if (f.status !== "verified") await client.auth.mfa.unenroll({ factorId: f.id });
    const { data, error } = await client.auth.mfa.enroll({ factorType: "totp", friendlyName: "Authenticator app" });
    setBusy(false);
    if (error || !data) return setError(error?.message ?? "Couldn't start the setup.");
    setSetup({ factorId: data.id, qr: qrSource(data.totp.qr_code), secret: data.totp.secret });
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    if (!setup) return;
    setBusy(true);
    setError(null);
    const { error } = await client.auth.mfa.challengeAndVerify({ factorId: setup.factorId, code: code.trim() });
    setBusy(false);
    if (error) return setError("That code didn't work. Codes change every 30 seconds; enter the one showing now.");
    setSetup(null);
    setCode("");
    await load();
    toast("Two-step sign-in is on");
    onDone?.();
  }

  async function turnOff() {
    if (!active) return;
    const ok = await dialogs.confirm({
      title: "Turn off two-step sign-in?",
      body: "Signing in will only need your email again.",
      confirmLabel: "Turn off",
      danger: true,
    });
    if (!ok) return;
    const { error } = await client.auth.mfa.unenroll({ factorId: active.id });
    if (error) return toast(error.message, "error");
    await load();
    toast("Two-step sign-in is off");
  }

  return (
    <section className="card settings-section">
      <h2 className="section-title">Two-step sign-in</h2>
      <div className="settings-row">
        <span className={`account-status ${active ? "on" : ""}`}>
          {active ? <ShieldCheck size={16} aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />}
          {factors === null ? "Checking…" : active ? "On: a code from your authenticator app is needed to sign in" : "Off"}
        </span>
        {factors !== null && !setup && (active ? (
          <button className="secondary danger" onClick={() => void turnOff()}>Turn off</button>
        ) : (
          <button className="secondary" onClick={() => void begin()} disabled={busy}>
            {busy ? "Starting…" : "Turn on"}
          </button>
        ))}
      </div>
      {!active && !setup && (
        <p className="muted small">
          After your email, signing in also asks for a code from an app such as Google Authenticator or Microsoft
          Authenticator, so a stolen inbox isn't enough to get in.
        </p>
      )}
      {setup && (
        <div className="mfa-setup reveal">
          <img src={setup.qr} alt="QR code to add datawazi to your authenticator app" width={168} height={168} />
          <form onSubmit={confirm} className="mfa-steps">
            <ol>
              <li>Scan the code with your authenticator app.</li>
              <li>
                Can't scan? Enter this key instead:
                <span className="mfa-secret">
                  <code>{setup.secret}</code>
                  <button
                    type="button"
                    className="icon"
                    aria-label="Copy the key"
                    onClick={() => void navigator.clipboard?.writeText(setup.secret).then(() => toast("Key copied"))}
                  >
                    <Copy size={14} aria-hidden="true" />
                  </button>
                </span>
              </li>
              <li>Type the 6-digit code it shows.</li>
            </ol>
            <div className="settings-row">
              <label className="field">
                <span className="sr-only">Code from the app</span>
                <input
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  className="code-input mfa-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  aria-describedby={error ? "mfa-error" : undefined}
                />
              </label>
              <button type="submit" disabled={busy || code.length < 6}>
                {busy ? "Checking…" : "Turn on"}
              </button>
              <button
                type="button"
                className="link"
                onClick={() => {
                  void client.auth.mfa.unenroll({ factorId: setup.factorId });
                  setSetup(null);
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
      {error && (
        <p id="mfa-error" className="signin-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function Sessions({ client }: { client: SupabaseClient }) {
  const dialogs = useDialogs();
  async function everywhere() {
    const ok = await dialogs.confirm({
      title: "Sign out everywhere?",
      body: "Every browser and device signed in to your account is signed out, this one too.",
      confirmLabel: "Sign out everywhere",
    });
    if (ok) await client.auth.signOut({ scope: "global" });
  }
  return (
    <section className="card settings-section">
      <h2 className="section-title">Where you're signed in</h2>
      <div className="settings-row">
        <span className="grow muted small">Lost a laptop, or signed in on a shared computer? Sign out of all of them at once.</span>
        <button className="secondary" onClick={() => void everywhere()}>
          <LogOut size={15} aria-hidden="true" /> Sign out everywhere
        </button>
      </div>
    </section>
  );
}

function DeleteAccount() {
  const account = useAccount();
  const dialogs = useDialogs();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function remove() {
    const ok = await dialogs.confirm({
      title: "Delete your account?",
      body: `You'll leave every workspace and can't sign in as ${account.me.user.email} again. Workspaces and their data stay with their other members. This can't be undone.`,
      confirmLabel: "Delete my account",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await accountApi.deleteAccount();
      await account.signOut?.();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  }

  return (
    <section className="card settings-section account-danger">
      <h2 className="section-title">Delete account</h2>
      <div className="settings-row">
        <span className="grow muted small">
          You leave every workspace. If you're the only admin of one, make someone else an admin in Members first.
        </span>
        <button className="secondary danger" onClick={() => void remove()} disabled={busy}>
          {busy ? "Deleting…" : "Delete account"}
        </button>
      </div>
    </section>
  );
}
