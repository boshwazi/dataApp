import { useEffect, useState, type FormEvent } from "react";
import { Send, Trash2, UserPlus } from "lucide-react";
import { accountApi, type Delivery, type People, type Role } from "../api";
import { useAccount } from "../components/account";
import { Skeleton, useCrumbs, useDialogs, useToast } from "../components/ui";

const ROLE_HELP: Record<Role, string> = {
  admin: "Everything, plus inviting people and changing roles",
  creator: "Builds and publishes datasets and dashboards",
  viewer: "Sees published dashboards and asks questions about them",
};

export function Members() {
  const account = useAccount();
  const toast = useToast();
  const dialogs = useDialogs();
  const [people, setPeople] = useState<People | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [busy, setBusy] = useState(false);
  const local = account.me.mode === "local";

  useCrumbs([{ label: "Members" }]);

  useEffect(() => {
    accountApi.people().then(setPeople).catch((e) => toast(e.message, "error"));
  }, [toast]);

  async function change(action: () => Promise<People>, done?: string) {
    try {
      setPeople(await action());
      if (done) toast(done);
      void account.refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  async function invite(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await accountApi.invite(email, role);
      setPeople(result);
      setEmail("");
      await delivered(result);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function delivered(result: Delivery) {
    if (result.emailed) {
      toast(`Invite emailed to ${result.invite.email}. The link is copied too, in case it lands in spam.`);
      await copy(result.link, null);
    } else {
      await copy(
        result.link,
        result.email_problem
          ? `The email couldn't be sent (${result.email_problem}). The link is copied; send it to ${result.invite.email} yourself.`
          : `Invite created. The link for ${result.invite.email} is copied; send it to them.`,
      );
    }
  }

  async function resend(id: string) {
    try {
      const result = await accountApi.resendInvite(id);
      setPeople(result);
      await delivered(result);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  async function copy(link: string, message: string | null = "Invite link copied") {
    try {
      await navigator.clipboard.writeText(link);
      if (message) toast(message);
    } catch {
      await dialogs.prompt({ title: "Invite link", label: "Copy this link and send it", initial: link, confirmLabel: "Done" });
    }
  }

  async function remove(userId: string, who: string) {
    const ok = await dialogs.confirm({
      title: `Remove ${who}?`,
      body: "They lose access to this workspace at once. Their work stays.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok) await change(() => accountApi.removeMember(userId), `Removed ${who}`);
  }

  return (
    <div className="page wide members">
      <div className="home-head">
        <h1>Members</h1>
      </div>
      <p className="muted">People in {account.workspace?.name}. Only admins see this page.</p>

      {local ? (
        <div className="notice small">Inviting people needs sign-in, which the hosted version of datawazi adds.</div>
      ) : (
        <form className="card invite-form" onSubmit={invite}>
          <h2>Invite someone</h2>
          <div className="inline-form">
            <label className="field grow">
              <span>Email</span>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
            </label>
            <label className="field">
              <span>Role</span>
              <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                <option value="viewer">Viewer</option>
                <option value="creator">Creator</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <button type="submit" className="align-end" disabled={busy || !email.trim()}>
              <UserPlus size={16} aria-hidden="true" /> {busy ? "Inviting…" : "Send invite"}
            </button>
          </div>
          <p className="muted small">{ROLE_HELP[role]}. We email them a sign-in link that works for 14 days, for that address only.</p>
        </form>
      )}

      <section className="home-section">
        <h2 className="section-title">People</h2>
        {!people ? (
          <Skeleton lines={2} height={48} />
        ) : (
          <ul className="project-list">
            {people.members.map((m) => {
              const you = m.user_id === account.me.user.id;
              return (
                <li key={m.user_id}>
                  <span className="avatar" aria-hidden="true">
                    {m.email[0]?.toUpperCase()}
                  </span>
                  <span className="project-link">
                    <strong>
                      {m.email}
                      {you && <span className="muted"> (you)</span>}
                    </strong>
                    <span className="muted small">Joined {new Date(m.joined_at).toLocaleDateString()}</span>
                  </span>
                  <select
                    aria-label={`Role of ${m.email}`}
                    value={m.role}
                    onChange={(e) => change(() => accountApi.setRole(m.user_id, e.target.value as Role), `${m.email} is now ${e.target.value}`)}
                    disabled={local}
                  >
                    <option value="viewer">Viewer</option>
                    <option value="creator">Creator</option>
                    <option value="admin">Admin</option>
                  </select>
                  <button className="icon danger" aria-label={`Remove ${m.email}`} onClick={() => remove(m.user_id, m.email)} disabled={local}>
                    <Trash2 size={16} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {people && people.invites.length > 0 && (
        <section className="home-section">
          <h2 className="section-title">Waiting to join</h2>
          <ul className="project-list">
            {people.invites.map((i) => (
              <li key={i.id}>
                <span className="avatar pending" aria-hidden="true">
                  {i.email[0]?.toUpperCase()}
                </span>
                <span className="project-link">
                  <strong>{i.email}</strong>
                  <span className="muted small">
                    {i.role[0].toUpperCase() + i.role.slice(1)}, invited by {i.invited_by}, link works until{" "}
                    {new Date(i.expires_at).toLocaleDateString()}
                  </span>
                </span>
                <button className="secondary" onClick={() => resend(i.id)} title="Sends a new link; the old one stops working">
                  <Send size={15} aria-hidden="true" /> Send again
                </button>
                <button className="icon danger" aria-label={`Withdraw the invite for ${i.email}`} onClick={() => change(() => accountApi.revokeInvite(i.id), "Invite withdrawn")}>
                  <Trash2 size={16} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
