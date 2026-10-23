import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, FileUp, Loader2, Plus, Sparkles, Upload, X } from "lucide-react";
import { accountApi, api, powerbiApi, type Role } from "../api";
import { useAccount } from "../components/account";
import { OnboardingShell } from "../components/OnboardingSteps";
import { useToast } from "../components/ui";
import { t } from "../i18n";

/**
 * A new workspace's first steps after naming it (step 1, in account.tsx): invite the team, then start with data.
 * Sample data and uploads go straight to Autopilot, so the first dashboard is built while the person watches.
 */

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xlsm,.xls";
type Invitee = { email: string; role: Role };
type Sent = { email: string; emailed: boolean; link: string };

function InviteStep({ onNext }: { onNext: () => void }) {
  const [rows, setRows] = useState<Invitee[]>([
    { email: "", role: "creator" },
    { email: "", role: "viewer" },
  ]);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<Sent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pending = rows.some((r) => r.email.trim());

  function update(i: number, change: Partial<Invitee>) {
    setRows((all) => all.map((row, k) => (k === i ? { ...row, ...change } : row)));
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!pending) return onNext();
    setBusy(true);
    setError(null);
    const done: Sent[] = [];
    for (const row of rows.filter((r) => r.email.trim())) {
      try {
        const result = await accountApi.invite(row.email.trim(), row.role);
        done.push({ email: row.email.trim(), emailed: result.emailed, link: result.link });
      } catch (e) {
        setError(`${row.email.trim()}: ${(e as Error).message}`);
        break;
      }
    }
    setSent((s) => [...s, ...done]);
    const left = rows.filter((r) => r.email.trim() && !done.some((d) => d.email === r.email.trim()));
    setRows(left.length ? left : [{ email: "", role: "viewer" }]);
    setBusy(false);
  }

  return (
    <>
      <h1>{t("Invite your team")}</h1>
      <p className="muted">{t("They'll get an email to join this workspace. You can change what each person can do later in Members.")}</p>
      <form onSubmit={send} className="onboard-invites">
        <div className="onboard-invite-labels" aria-hidden="true">
          <span>{t("Email")}</span>
          <span>{t("Can")}</span>
        </div>
        {rows.map((row, i) => (
          <div key={i} className="onboard-invite-row">
            <input
              type="email"
              autoComplete="off"
              spellCheck={false}
              placeholder="name@company.com"
              aria-label={t("Email {n}", { n: i + 1 })}
              value={row.email}
              onChange={(e) => update(i, { email: e.target.value })}
            />
            <select value={row.role} onChange={(e) => update(i, { role: e.target.value as Role })} aria-label={t("What person {n} can do", { n: i + 1 })}>
              <option value="viewer">{t("View")}</option>
              <option value="creator">{t("Build")}</option>
              <option value="admin">{t("Admin")}</option>
            </select>
            <button
              type="button"
              className="icon"
              aria-label={t("Remove email {n}", { n: i + 1 })}
              onClick={() => setRows((all) => (all.length > 1 ? all.filter((_, k) => k !== i) : [{ email: "", role: "viewer" }]))}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        ))}
        <button type="button" className="link onboard-add" onClick={() => setRows((all) => [...all, { email: "", role: "viewer" }])}>
          <Plus size={14} aria-hidden="true" /> {t("Add another")}
        </button>
        {sent.length > 0 && (
          <ul className="onboard-sent" role="status">
            {sent.map((s) => (
              <li key={s.email}>
                <strong>{s.email}</strong>{" "}
                {s.emailed ? (
                  t("was emailed an invite.")
                ) : (
                  <>
                    {t("wasn't emailed. Send them this link:")}
                    <input readOnly value={s.link} onFocus={(e) => e.target.select()} aria-label={t("Invite link")} />
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        {error && <p className="signin-error" role="alert">{error}</p>}
        <div className="onboard-actions">
          <button type="submit" disabled={busy}>
            {busy ? t("Sending…") : pending ? t("Send invites") : t("Continue")}
          </button>
          {pending && (
            <button type="button" className="link" onClick={onNext}>{t("Skip for now")}</button>
          )}
        </div>
      </form>
    </>
  );
}

function Choice(props: {
  icon: ReactNode;
  title: string;
  text: string;
  tag?: string;
  busy: string | null;
  disabled: boolean;
  onPick: () => void;
}) {
  return (
    <button type="button" className={`onboard-choice${props.busy ? " is-busy" : ""}`} onClick={props.onPick} disabled={props.disabled} aria-busy={!!props.busy}>
      <span className="onboard-choice-icon" aria-hidden="true">{props.busy ? <Loader2 size={20} className="onboard-spin" /> : props.icon}</span>
      <span className="onboard-choice-text">
        <strong>
          {props.title}
          {props.tag && <span className="onboard-tag">{props.tag}</span>}
        </strong>
        <span>{props.busy ?? props.text}</span>
      </span>
    </button>
  );
}

function DataStep() {
  const navigate = useNavigate();
  const toast = useToast();
  const [busy, setBusy] = useState<{ choice: string; text: string } | null>(null);
  const [aiReady, setAiReady] = useState<boolean | null>(null);
  const files = useRef<HTMLInputElement>(null);
  const pbix = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getSettings().then((s) => setAiReady(s.has_api_key), () => setAiReady(false));
  }, []);

  async function start(choice: string, name: string, load: (id: string) => Promise<boolean>, after: "autopilot" | "model") {
    if (busy) return;
    const say = (text: string) => setBusy({ choice, text });
    try {
      say(t("Creating the dataset…"));
      const dataset = await api.createDataset(name);
      say(t("Reading your data…"));
      if (!(await load(dataset.id))) return navigate(`/datasets/${dataset.id}/get-data`);
      if (after === "model") return navigate(`/datasets/${dataset.id}/model`);
      if (!aiReady) return navigate(`/datasets/${dataset.id}/get-data`);
      say(t("Starting Autopilot…"));
      await api.startAutopilot(dataset.id);
      navigate(`/datasets/${dataset.id}/autopilot`);
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(null);
    }
  }

  const upload = (chosen: File[]) =>
    chosen.length > 0 &&
    void start("upload", chosen[0].name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim() || t("My data"), async (id) => {
      const added = await api.uploadFiles(id, chosen);
      if (!added.added.length) toast(added.errors[0] ?? t("None of those files could be read."), "error");
      return added.added.length > 0;
    }, "autopilot");

  const powerbi = (file: File | undefined) =>
    file && void start("powerbi", file.name.replace(/\.[^.]+$/, ""), async (id) => (await powerbiApi.import(id, file)).added.length > 0, "model");

  const busyFor = (choice: string) => (busy?.choice === choice ? busy.text : null);

  return (
    <>
      <h1>{t("Start with data")}</h1>
      <p className="muted">
        {aiReady === false
          ? t("The AI isn't connected yet, so you'll clean and model the data yourself in Get Data.")
          : t("Autopilot cleans it, models it and designs a first dashboard while you watch. You can change every step.")}
      </p>
      <div className="onboard-choices">
        <Choice
          icon={<Sparkles size={20} />}
          title={t("Try the sample data")}
          tag={t("Recommended")}
          text={t("A messy sales file with products and customers. The quickest way to see it work.")}
          busy={busyFor("sample")}
          disabled={!!busy}
          onPick={() => void start("sample", t("Sample sales"), async (id) => (await api.addSample(id)).added.length > 0, "autopilot")}
        />
        <Choice
          icon={<Upload size={20} />}
          title={t("Upload your own files")}
          text={t("CSV or Excel, one file or several that belong together.")}
          busy={busyFor("upload")}
          disabled={!!busy}
          onPick={() => files.current?.click()}
        />
        <Choice
          icon={<FileUp size={20} />}
          title={t("Import a Power BI file")}
          text={t("A .pbix file, or a .pbip project as .zip. Its tables, relationships and measures come across.")}
          busy={busyFor("powerbi")}
          disabled={!!busy}
          onPick={() => pbix.current?.click()}
        />
      </div>
      <input ref={files} type="file" accept={ACCEPT} multiple hidden onChange={(e) => upload([...(e.target.files ?? [])])} />
      <input ref={pbix} type="file" accept=".pbix,.zip" hidden onChange={(e) => powerbi(e.target.files?.[0])} />
      <p className="muted small">{t("Other sources (shared links, Google Sheets) are in Get Data.")}</p>
      <div className="onboard-actions">
        <Link to="/" className="onboard-skip">{t("Skip for now")}</Link>
      </div>
    </>
  );
}

export function Onboarding() {
  const account = useAccount();
  const [params, setParams] = useSearchParams();
  // Invites need sign-in (the hosted version); without it, or for non-admins, go straight to data.
  const [canInvite, setCanInvite] = useState<boolean | null>(account.can("admin") ? null : false);
  useEffect(() => {
    if (canInvite === null) accountApi.config().then((c) => setCanInvite(c.mode !== "local"), () => setCanInvite(false));
  }, [canInvite]);
  if (canInvite === null) return null;
  const step = params.get("step") === "data" || !canInvite ? "data" : "invite";

  return (
    <OnboardingShell step={step === "invite" ? 2 : 3}>
      {step === "data" && canInvite && (
        <button type="button" className="link onboard-back" onClick={() => setParams({})}>
          <ArrowLeft size={14} aria-hidden="true" /> {t("Back")}
        </button>
      )}
      {step === "invite" ? <InviteStep onNext={() => setParams({ step: "data" })} /> : <DataStep />}
    </OnboardingShell>
  );
}
