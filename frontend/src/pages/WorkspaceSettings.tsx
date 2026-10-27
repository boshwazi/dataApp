import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { accountApi } from "../api";
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

      <p className="muted small">
        People, groups and the audit log are in <Link to="/members">Members</Link>.
      </p>
    </div>
  );
}
