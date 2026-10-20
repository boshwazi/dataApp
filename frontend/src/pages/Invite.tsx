import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { accountApi, type Role } from "../api";
import { useAccount } from "../components/account";
import { BrandMark } from "../components/ui";

/** Opening an invite link: say which workspace it is for, then join with one click. */
export function Invite() {
  const { token = "" } = useParams();
  const account = useAccount();
  const navigate = useNavigate();
  const [invite, setInvite] = useState<{ workspace: string; email: string; role: Role; invited_by: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    accountApi.showInvite(token).then(setInvite).catch((e) => setError(e.message));
  }, [token]);

  async function accept() {
    setBusy(true);
    try {
      const workspace = await accountApi.acceptInvite(token);
      await account.refresh();
      account.switchTo(workspace.id);
      navigate("/", { replace: true });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <main className="auth-card">
        <div className="auth-brand">
          <BrandMark size={28} /> datawazi
        </div>
        {error ? (
          <>
            <h1>This invite can't be used</h1>
            <p className="bad">{error}</p>
            <button className="secondary" onClick={() => navigate("/", { replace: true })}>
              Go to datawazi
            </button>
          </>
        ) : !invite ? (
          <p className="muted">Checking the invite…</p>
        ) : (
          <>
            <h1>Join {invite.workspace}</h1>
            <p className="muted">
              {invite.invited_by} invited {invite.email} as {invite.role === "admin" ? "an" : "a"} {invite.role}.
            </p>
            {invite.email !== account.me.user.email.toLowerCase() && (
              <p className="notice small">
                You are signed in as {account.me.user.email}. Sign in with {invite.email} to accept.
              </p>
            )}
            <button onClick={accept} disabled={busy || invite.email !== account.me.user.email.toLowerCase()}>
              {busy ? "Joining…" : `Join ${invite.workspace}`}
            </button>
            {account.signOut && invite.email !== account.me.user.email.toLowerCase() && (
              <button className="link small" onClick={() => void account.signOut!()}>
                Sign out and switch account
              </button>
            )}
          </>
        )}
      </main>
    </div>
  );
}
