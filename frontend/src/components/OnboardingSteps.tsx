import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { BrandMark } from "./ui";
import { t } from "../i18n";

const STEPS = ["Name your workspace", "Invite your team", "Start with data"] as const;

/**
 * The frame for a new workspace's first steps: the form on the left, and on the right the three steps with
 * the current one marked (the same two-part screen as sign-in). Hidden on phones, where the form shows
 * "Step n of 3" instead.
 */
export function OnboardingShell({ step, children }: { step: 1 | 2 | 3; children: ReactNode }) {
  return (
    <div className="signin onboard">
      <main className="signin-main">
        <div className="signin-panel onboard-panel">
          <div className="auth-brand">
            <BrandMark size={28} /> datawazi
          </div>
          <p className="onboard-count">{t("Step {n} of 3", { n: step })}</p>
          {children}
        </div>
      </main>
      <aside className="signin-aside onboard-aside">
        <div className="onboard-aside-inner">
          <h2>{t("Set up datawazi")}</h2>
          <p>{t("A few quick steps, and you can skip any of them.")}</p>
          <ol className="onboard-stepper">
            {STEPS.map((label, i) => {
              const n = i + 1;
              const state = n < step ? "done" : n === step ? "current" : "next";
              return (
                <li key={label} className={state} aria-current={state === "current" ? "step" : undefined}>
                  <span className="onboard-dot" aria-hidden="true">
                    {state === "done" ? <Check size={14} strokeWidth={3} /> : n}
                  </span>
                  <span>
                    {t(label)}
                    <span className="sr-only">{state === "done" ? `, ${t("Done")}` : state === "current" ? `, ${t("Current step")}` : ""}</span>
                  </span>
                </li>
              );
            })}
          </ol>
          <p className="onboard-aside-note">{t("Then Autopilot builds your first dashboard while you watch.")}</p>
        </div>
      </aside>
    </div>
  );
}
