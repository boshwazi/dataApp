import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Circle, X } from "lucide-react";
import { accountApi, type DashboardSummary, type Dataset } from "../api";
import { useAccount } from "./account";
import { onboardingFlag, setOnboardingFlag } from "./onboarding";
import { t } from "../i18n";

/** Home's short list of first things to try in a new workspace. It ticks itself off and can be hidden. */
export function NextSteps({ datasets, dashboards }: { datasets: Dataset[]; dashboards: DashboardSummary[] }) {
  const account = useAccount();
  const workspace = account.workspace?.id;
  const admin = account.can("admin");
  const [hidden, setHidden] = useState(() => onboardingFlag(workspace, "hidden"));
  // Whether the team has been invited; "n/a" for non-admins and without sign-in (local mode), where nobody can be.
  const [invited, setInvited] = useState<boolean | "n/a" | null>(admin ? null : "n/a");

  useEffect(() => {
    if (!admin) return;
    accountApi
      .config()
      .then(async (c) => {
        if (c.mode === "local") return setInvited("n/a");
        const people = await accountApi.people();
        setInvited(people.members.length > 1 || people.invites.length > 0);
      })
      .catch(() => setInvited("n/a"));
  }, [admin]);

  const first = dashboards[0];
  const steps = [
    { label: t("Bring in data"), done: datasets.length > 0, to: "/start?step=data" },
    { label: t("Get a first dashboard"), done: dashboards.length > 0, to: "/start?step=data" },
    { label: t("Publish a dashboard for your team"), done: dashboards.some((d) => d.published), to: first ? `/dashboards/${first.id}` : "/" },
    { label: t("Ask your data a question"), done: onboardingFlag(workspace, "asked"), to: first ? `/dashboards/${first.id}` : "/" },
    ...(invited === "n/a" ? [] : [{ label: t("Invite your team"), done: invited === true, to: "/members" }]),
  ];
  const left = steps.filter((s) => !s.done).length;
  if (hidden || left === 0 || invited === null) return null;

  return (
    <section className="next-steps" aria-labelledby="next-steps-title">
      <div className="next-steps-head">
        <h2 id="next-steps-title">{t("Next steps")}</h2>
        <span className="muted small">{t("{done} of {all} done", { done: steps.length - left, all: steps.length })}</span>
        <button
          type="button"
          className="icon"
          aria-label={t("Hide next steps")}
          onClick={() => {
            setOnboardingFlag(workspace, "hidden");
            setHidden(true);
          }}
        >
          <X size={16} />
        </button>
      </div>
      <ol>
        {steps.map((s) => (
          <li key={s.label} className={s.done ? "done" : ""}>
            {s.done ? <CheckCircle2 size={18} aria-hidden="true" /> : <Circle size={18} aria-hidden="true" />}
            {s.done ? <span>{s.label}</span> : <Link to={s.to}>{s.label}</Link>}
            <span className="sr-only">{s.done ? t("Done") : t("To do")}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
