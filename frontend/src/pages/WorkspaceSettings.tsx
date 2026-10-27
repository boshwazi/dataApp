import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { accountApi, downloadFile } from "../api";
import { useAccount } from "../components/account";
import { InstructionsDialog } from "../components/InstructionsDialog";
import { useCrumbs, useToast } from "../components/ui";

/** Workspace settings for admins: the name, what the AI may see, and the AI instructions for everything here. */
export function WorkspaceSettings() {
  useCrumbs([{ label: "Workspace" }]);
  const account = useAccount();
  const toast = useToast();
  const current = account.workspace?.name ?? "";
  const [name, setName] = useState(current);
  const [saving, setSaving] = useState(false);
  const [samples, setSamples] = useState<boolean | null>(null);
  const [instructions, setInstructions] = useState(false);

  useEffect(() => {
    accountApi.aiPrivacy().then((r) => setSamples(r.samples), (e) => toast((e as Error).message, "error"));
  }, [toast]);

  async function rename(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await accountApi.renameWorkspace(name.trim());
      await account.refresh();
      toast("Workspace renamed");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  }

  async function toggleSamples(next: boolean) {
    setSamples(next);
    try {
      setSamples((await accountApi.setAiPrivacy(next)).samples);
      toast(next ? "The AI can see a few sample rows again" : "The AI now works from summaries only");
    } catch (e) {
      setSamples(!next);
      toast((e as Error).message, "error");
    }
  }

  return (
    <div className="page workspace-settings">
      {instructions && (
        <InstructionsDialog
          title="AI instructions for the workspace"
          scope="anything in this workspace"
          load={async () => (await accountApi.instructions()).instructions}
          save={(text) => accountApi.setInstructions(text)}
          onClose={() => setInstructions(false)}
        />
      )}
      <h1>Workspace</h1>
      <p className="muted">Settings for everyone in {current}. Only admins see this page.</p>

      <section className="card settings-section">
        <h2 className="section-title">Name</h2>
        <form className="settings-row" onSubmit={rename}>
          <label className="field grow">
            <span className="sr-only">Workspace name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="organization" />
          </label>
          {name.trim() && name.trim() !== current && (
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Rename"}
            </button>
          )}
        </form>
      </section>

      <section className="card settings-section">
        <h2 className="section-title">What the AI may see</h2>
        <label className="switch-row">
          <input
            type="checkbox"
            role="switch"
            checked={samples ?? true}
            disabled={samples === null}
            onChange={(e) => void toggleSamples(e.target.checked)}
          />
          <span>
            <strong>Let the AI see a few sample rows</strong>
            <span className="muted small">
              On: when it describes and cleans a table, the AI looks at up to 5 of its rows, which makes its suggestions
              better. Off: it works from column summaries and counts only (types, ranges, common values), never whole
              rows. Either way your full data stays in the workspace.
            </span>
          </span>
        </label>
      </section>

      <section className="card settings-section">
        <h2 className="section-title">AI instructions</h2>
        <p className="muted small">
          House rules every AI step follows here, such as your fiscal year, currency or naming. Datasets and dashboards can
          add their own.
        </p>
        <div>
          <button className="secondary" onClick={() => setInstructions(true)}>
            Edit instructions
          </button>
        </div>
      </section>

      {account.me.mode === "local" ? (
        <p className="muted small">
          Sign-in rules, joining by email domain and deleting the workspace come with the hosted version, where people
          sign in.
        </p>
      ) : (
        <>
          <SignInRules />
          <JoinByDomain />
          <DeleteWorkspace />
        </>
      )}

      <p className="muted small">
        People, groups and the audit log are in <Link to="/members">Members</Link>.
      </p>
    </div>
  );
}

/** Run a settings change, refresh the account (settings ride on it), and say how it went. */
function useSettingChange() {
  const account = useAccount();
  const toast = useToast();
  return async (change: () => Promise<unknown>, done: string) => {
    try {
      await change();
      await account.refresh();
      toast(done);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
}

function SignInRules() {
  const account = useAccount();
  const change = useSettingChange();
  const on = !!account.workspace?.settings?.require_mfa;
  return (
    <section className="card settings-section">
      <h2 className="section-title">Sign-in</h2>
      <label className="switch-row">
        <input
          type="checkbox"
          role="switch"
          checked={on}
          onChange={(e) =>
            void change(
              () => accountApi.setSignInRules(e.target.checked),
              e.target.checked ? "Two-step sign-in is now required" : "Two-step sign-in is optional again",
            )
          }
        />
        <span>
          <strong>Require two-step sign-in</strong>
          <span className="muted small">
            Everyone here, you included, needs a code from an authenticator app to open this workspace; people without
            one are asked to set it up. Turn it on for your own account first, in{" "}
            <Link to="/account">Your account</Link>.
          </span>
        </span>
      </label>
    </section>
  );
}

const FREE_MAIL = /^(gmail|googlemail|yahoo|ymail|hotmail|outlook|live|msn|icloud|me|aol|proton|protonmail|gmx|mail|yandex|zoho|qq|163)\./i;

function JoinByDomain() {
  const account = useAccount();
  const change = useSettingChange();
  const domain = account.me.user.email.split("@")[1]?.toLowerCase() ?? "";
  const on = !!account.workspace?.settings?.domains?.includes(domain);
  return (
    <section className="card settings-section">
      <h2 className="section-title">Who may join</h2>
      {FREE_MAIL.test(domain) ? (
        <p className="muted small">
          Your email is on {domain}, a free email service, so joining by email domain isn't available. Invite people in
          Members instead.
        </p>
      ) : (
        <label className="switch-row">
          <input
            type="checkbox"
            role="switch"
            checked={on}
            onChange={(e) =>
              void change(
                () => accountApi.setDomains(e.target.checked ? [domain] : []),
                e.target.checked ? `People at ${domain} can now ask to join` : "Joining is by invite only again",
              )
            }
          />
          <span>
            <strong>Let anyone with an @{domain} email join</strong>
            <span className="muted small">
              They're offered this workspace when they sign in, and join as viewers. Only sign-ins that prove the
              address count (an email code, or Google); Microsoft sign-ins and other domains still need an invite.
            </span>
          </span>
        </label>
      )}
    </section>
  );
}

const FRESH_HOURS = 24;

function DeleteWorkspace() {
  const account = useAccount();
  const toast = useToast();
  const name = account.workspace?.name ?? "";
  const exportedAt = account.workspace?.settings?.exported_at;
  const fresh = !!exportedAt && Date.now() - Date.parse(exportedAt) < FRESH_HOURS * 3600 * 1000;
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState<"export" | "delete" | null>(null);

  async function exportAll() {
    setBusy("export");
    try {
      const safe = name.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "") || "workspace";
      await downloadFile("/api/workspaces/current/export", `datawazi-${safe}.zip`);
      await account.refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  async function remove(event: FormEvent) {
    event.preventDefault();
    setBusy("delete");
    try {
      await accountApi.deleteWorkspace(typed);
      await account.refresh(); // the workspace now shows as closed
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(null);
    }
  }

  return (
    <section className="card settings-section account-danger">
      <h2 className="section-title">Delete workspace</h2>
      <p className="muted small">
        Closes the workspace for everyone straight away, and deletes it with all its data after 7 days. Until then an
        admin can cancel. Download the export first, so nothing is lost by accident.
      </p>
      <ol className="delete-steps">
        <li>
          <button className="secondary" onClick={() => void exportAll()} disabled={busy !== null}>
            {busy === "export" ? "Preparing the export…" : "Download the export"}
          </button>
          {fresh && (
            <span className="small ok-text">
              Downloaded {new Date(exportedAt!).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
            </span>
          )}
        </li>
        <li>
          <form className="settings-row" onSubmit={remove}>
            <label className="field grow">
              <span className="sr-only">Type the workspace name to confirm</span>
              <input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder={`Type "${name}" to confirm`}
                disabled={!fresh}
                autoComplete="off"
              />
            </label>
            <button type="submit" className="danger-solid" disabled={!fresh || typed.trim() !== name || busy !== null}>
              {busy === "delete" ? "Deleting…" : "Delete workspace"}
            </button>
          </form>
        </li>
      </ol>
    </section>
  );
}
