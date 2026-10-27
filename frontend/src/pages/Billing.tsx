import { useEffect, useState } from "react";
import { billingApi, type BillingView, type Plan, type Usage, type UsageRow } from "../api";
import { useAccount } from "../components/account";
import { useCrumbs, useToast } from "../components/ui";

/** Billing for admins: this month's AI credits, the plan and how plans compare, and payments. Until billing is
    switched on at launch, nothing is limited or charged; the page says so. */
export function Billing() {
  useCrumbs([{ label: "Billing" }]);
  const account = useAccount();
  const toast = useToast();
  const [view, setView] = useState<BillingView | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);

  useEffect(() => {
    billingApi.get().then(setView, (e) => toast((e as Error).message, "error"));
    billingApi.usage().then(setUsage, (e) => toast((e as Error).message, "error"));
  }, [toast]);

  return (
    <div className="page billing-page">
      <h1>Billing</h1>
      <p className="muted">The plan and this month's AI use in {account.workspace?.name}. Only admins see this page.</p>
      {view && !view.enforced && (
        <p className="notice">
          Billing starts at launch. Until then nothing is limited or charged, and the plan numbers below may still
          change.
        </p>
      )}
      {view ? (
        <>
          <Credits view={view} usage={usage} />
          <Plans view={view} />
          <Payments view={view} />
        </>
      ) : (
        <div className="skeleton" aria-label="Loading">
          <span style={{ height: 160 }} />
          <span style={{ height: 240 }} />
        </div>
      )}
    </div>
  );
}

const number = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });

function monthName(month: string) {
  return new Date(`${month}-01T00:00:00Z`).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
}

function resetDate(month: string) {
  const [y, m] = month.split("-").map(Number);
  const next = new Date(Date.UTC(m === 12 ? y + 1 : y, m === 12 ? 0 : m, 1));
  return next.toLocaleDateString(undefined, { day: "numeric", month: "long", timeZone: "UTC" });
}

function Credits({ view, usage }: { view: BillingView; usage: Usage | null }) {
  const { used, allowance, percent, month } = view.credits;
  const level = percent >= 100 ? "bad" : percent >= 80 ? "warn" : "";
  return (
    <section className="card settings-section" aria-labelledby="credits-title">
      <h2 className="section-title" id="credits-title">
        AI credits in {monthName(month)}
      </h2>
      <div className="credit-head">
        <strong className="credit-used">{number(used)}</strong>
        <span className="muted">of {number(allowance)} credits used</span>
        <span className={`credit-percent ${level}`}>{percent}%</span>
      </div>
      <div
        className={`credit-bar ${level}`}
        role="progressbar"
        aria-label="AI credits used this month"
        aria-valuemin={0}
        aria-valuemax={allowance}
        aria-valuenow={Math.min(used, allowance)}
      >
        <i style={{ transform: `scaleX(${Math.min(percent, 100) / 100})` }} />
      </div>
      <p className="muted small">
        {percent >= 100
          ? view.enforced
            ? "AI steps are paused until the credits reset; cleaning, modelling and dashboards by hand still work."
            : "Over this plan's credits. Once billing starts, AI steps would pause here until the credits reset."
          : `Credits reset on ${resetDate(month)}. Admins are told at 80%.`}{" "}
        A credit is roughly 750 words the AI reads, or 190 it writes.
      </p>
      {usage && usage.total.calls > 0 && (
        <div className="usage-split">
          <UsageTable title="By feature" rows={usage.by_feature.map((r) => ({ ...r, label: r.feature }))} />
          <UsageTable title="By person" rows={usage.by_person.map((r) => ({ ...r, label: r.person }))} />
        </div>
      )}
      {usage && usage.total.calls === 0 && <p className="muted small">No AI steps yet this month.</p>}
    </section>
  );
}

function UsageTable({ title, rows }: { title: string; rows: (UsageRow & { label: string })[] }) {
  return (
    <table className="usage-table">
      <caption>{title}</caption>
      <thead>
        <tr>
          <th scope="col">
            <span className="sr-only">{title}</span>
          </th>
          <th scope="col" className="num">
            AI steps
          </th>
          <th scope="col" className="num">
            Credits
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 8).map((r) => (
          <tr key={r.label}>
            <td className="usage-label" title={r.label}>
              {r.label}
            </td>
            <td className="num">{number(r.calls)}</td>
            <td className="num">{r.credits.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const REFRESH = { manual: "By hand", daily: "Daily", hourly: "Hourly" };

function cell(value: number | "seats" | null, seatsNote = "Per seat") {
  if (value === null) return "Unlimited";
  if (value === "seats") return seatsNote;
  return number(value);
}

function Plans({ view }: { view: BillingView }) {
  const current = view.account.plan;
  const rows: [string, (p: Plan) => string][] = [
    ["Creators and admins", (p) => cell(p.creators)],
    ["Viewers", (p) => cell(p.viewers)],
    ["Datasets", (p) => cell(p.datasets)],
    ["Scheduled refresh", (p) => REFRESH[p.refresh]],
    ["AI credits a month", (p) => number(p.credits)],
    ["Also", (p) => p.extras.join(", ") || "—"],
  ];
  return (
    <section className="card settings-section" aria-labelledby="plan-title">
      <h2 className="section-title" id="plan-title">
        Plan
      </h2>
      <p>
        <strong>{view.plan.name}</strong>
        {view.plan.creators === "seats" && <span className="muted">, {view.account.seats} seats</span>}
        {view.account.cancel_at_period_end && view.account.period_end && (
          <span className="muted">, ending {new Date(view.account.period_end).toLocaleDateString(undefined, { dateStyle: "medium" })}</span>
        )}
      </p>
      <div className="plan-compare-wrap">
        <table className="plan-compare">
          <thead>
            <tr>
              <th scope="col">
                <span className="sr-only">Includes</span>
              </th>
              {view.plans.map((p) => (
                <th key={p.id} scope="col" className={p.id === current ? "current" : ""}>
                  {p.name}
                  {p.id === current && <span className="plan-current">Current</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                {view.plans.map((p) => (
                  <td key={p.id} className={p.id === current ? "current" : ""}>
                    {value(p)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">Changing plan opens at launch. Prices are set from what the AI really costs to run.</p>
    </section>
  );
}

function Payments({ view }: { view: BillingView }) {
  const card = view.account.payment_method;
  return (
    <section className="card settings-section" aria-labelledby="payments-title">
      <h2 className="section-title" id="payments-title">
        Payments
      </h2>
      <p className="muted small">
        {card ? `${card.brand} ending ${card.last4}.` : "No card saved."} Receipts appear here after the first payment.
      </p>
    </section>
  );
}
