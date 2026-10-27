import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { billingApi, type BillingView, type Plan, type Receipt, type Usage, type UsageRow } from "../api";
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

  const [params] = useSearchParams();
  const navigate = useNavigate();

  const load = useCallback(() => billingApi.get().then(setView, (e) => toast((e as Error).message, "error")), [toast]);

  useEffect(() => {
    billingApi.usage().then(setUsage, (e) => toast((e as Error).message, "error"));
    const back = params.get("paid") ? "paid" : params.get("failed") ? "failed" : null;
    const pending = remembered();
    if (back && pending) {
      forget();
      navigate("/billing", { replace: true });
      if (back === "failed") {
        toast("The payment didn't go through, so nothing was charged.", "error");
        void load();
        return;
      }
      // Back from CHIP's checkout: the server checks the purchase with CHIP (its callback may be on the way too).
      billingApi
        .settle(pending)
        .then(
          (r) => toast(r.status === "paid" ? "Payment received. Thank you." : "The payment is still being confirmed; check back in a minute."),
          (e) => toast((e as Error).message, "error"),
        )
        .finally(() => void load());
      return;
    }
    void load();
  }, [toast, params, navigate, load]);

  async function pay(body: { kind: "plan" | "topup"; plan?: "pro" | "team"; seats?: number }) {
    try {
      const started = await billingApi.checkout(body);
      remember(started.id);
      window.location.assign(started.checkout_url); // CHIP's page takes the card, then sends the admin back here
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  async function cancel(cancelIt: boolean) {
    try {
      await billingApi.cancel(cancelIt);
      toast(cancelIt ? "The plan ends at the end of this month" : "The plan carries on");
      void load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

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
          {view.account.status === "past_due" && (
            <p className="notice warn" role="status">
              This month's payment didn't go through. The card is tried again soon; after three tries the workspace moves
              to Free. Choose the plan again below to pay with another card.
            </p>
          )}
          <Credits view={view} usage={usage} onTopup={() => void pay({ kind: "topup" })} />
          <Plans view={view} onChoose={(plan, seats) => void pay({ kind: "plan", plan, seats })} onCancel={(c) => void cancel(c)} />
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
const money = (sen: number) => `RM ${(sen / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// The purchase being paid, kept across the trip to CHIP's checkout and back.
const PENDING = "datawazi-pending-purchase";
function remember(id: string) {
  try {
    sessionStorage.setItem(PENDING, id);
  } catch {
    // private mode: CHIP's callback still settles it
  }
}
function remembered(): string | null {
  try {
    return sessionStorage.getItem(PENDING);
  } catch {
    return null;
  }
}
function forget() {
  try {
    sessionStorage.removeItem(PENDING);
  } catch {
    // nothing to clear
  }
}

function monthName(month: string) {
  return new Date(`${month}-01T00:00:00Z`).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
}

function resetDate(month: string) {
  const [y, m] = month.split("-").map(Number);
  const next = new Date(Date.UTC(m === 12 ? y + 1 : y, m === 12 ? 0 : m, 1));
  return next.toLocaleDateString(undefined, { day: "numeric", month: "long", timeZone: "UTC" });
}

function Credits({ view, usage, onTopup }: { view: BillingView; usage: Usage | null; onTopup: () => void }) {
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
      {view.payments_ready && view.topup.price !== null && (
        <div>
          <button className="secondary" onClick={onTopup}>
            Add {number(view.topup.credits)} credits for {money(view.topup.price)}
          </button>
        </div>
      )}
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

function Plans({ view, onChoose, onCancel }: {
  view: BillingView;
  onChoose: (plan: "pro" | "team", seats: number) => void;
  onCancel: (cancel: boolean) => void;
}) {
  const current = view.account.plan;
  const [seats, setSeats] = useState(Math.max(1, view.account.seats || 1));
  const onSale = view.payments_ready && (view.prices.pro !== null || view.prices.team !== null);
  const price = (p: Plan) => {
    if (p.id === "free") return "RM 0";
    const sen = view.prices[p.id];
    return sen === null ? "Set at launch" : `${money(sen)} a seat a month`;
  };
  const nameOf = (id: Plan["id"]) => view.plans.find((p) => p.id === id)?.name ?? id;
  const rows: [string, (p: Plan) => string][] = [
    ["Price", price],
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
      {onSale ? (
        <div className="plan-actions">
          <label className="field seats-field">
            <span>Seats (creators and admins)</span>
            <input
              type="number"
              inputMode="numeric"
              min={1}
              max={500}
              value={seats}
              onChange={(e) => setSeats(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          {(["pro", "team"] as const)
            .filter((id) => view.prices[id] !== null)
            .map((id) => (
              <button key={id} className={id === "pro" ? "" : "secondary"} onClick={() => onChoose(id, seats)}>
                {current === id ? `Pay for ${seats} ${seats === 1 ? "seat" : "seats"} of ${nameOf(id)}` : `Change to ${nameOf(id)}`}
              </button>
            ))}
          {current !== "free" &&
            (view.account.cancel_at_period_end ? (
              <button className="link" onClick={() => onCancel(false)}>
                Keep the plan
              </button>
            ) : (
              <button className="link danger" onClick={() => onCancel(true)}>
                Cancel at the end of the month
              </button>
            ))}
          <p className="muted small">
            You pay on CHIP's secure page; card details never reach datawazi. Changing plan mid-month takes the unused
            part off the new plan.
          </p>
        </div>
      ) : (
        <p className="muted small">Changing plan opens at launch. Prices are set from what the AI really costs to run.</p>
      )}
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
        {card ? `${card.brand} ending ${card.last4}, used for renewals.` : "No card saved."}
        {view.receipts.length === 0 && " Receipts appear here after the first payment."}
      </p>
      {view.receipts.length > 0 && (
        <ul className="receipts">
          {view.receipts.map((r) => (
            <ReceiptRow key={r.number} receipt={r} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ReceiptRow({ receipt: r }: { receipt: Receipt }) {
  return (
    <li>
      <details>
        <summary>
          <span className="receipt-number">{r.number}</span>
          <span className="muted">{new Date(r.date).toLocaleDateString(undefined, { dateStyle: "medium" })}</span>
          <span className="grow">{r.lines[0]?.description}</span>
          <strong className="receipt-total">{money(r.total)}</strong>
        </summary>
        <table className="usage-table receipt-lines">
          <caption className="sr-only">Receipt {r.number}</caption>
          <tbody>
            {r.lines.map((line, i) => (
              <tr key={i}>
                <td>
                  {line.description}
                  {line.quantity > 1 && ` × ${line.quantity}`}
                  {line.discount > 0 && <span className="muted"> (less {money(line.discount)} unused)</span>}
                </td>
                <td className="num">{money(line.amount)}</td>
              </tr>
            ))}
            {r.tax.amount > 0 && (
              <tr>
                <td>
                  {r.tax.name} at {Math.round(r.tax.rate * 100)}%
                </td>
                <td className="num">{money(r.tax.amount)}</td>
              </tr>
            )}
            <tr>
              <th scope="row">Total</th>
              <td className="num">
                <strong>{money(r.total)}</strong>
              </td>
            </tr>
          </tbody>
        </table>
        <p className="muted small">
          Billed to {r.bill_to ?? r.workspace}
          {r.paid_with && `, paid with ${r.paid_with.brand} ending ${r.paid_with.last4}`}.
        </p>
      </details>
    </li>
  );
}
