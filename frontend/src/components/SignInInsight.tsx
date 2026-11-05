import type { CSSProperties } from "react";
import { Check } from "lucide-react";

/** Revenue by region in samples/sales.csv after cleaning (241 orders, RM 153,948.70): the landing hero's numbers. */
const REGIONS: [string, number][] = [
  ["Central", 46381.8],
  ["Southern", 36160.9],
  ["East Coast", 25782.9],
  ["Unknown", 16028.0],
  ["East Malaysia", 15382.6],
  ["Northern", 14212.5],
];

/**
 * The sign-in screens' picture: the insight the landing page ends on, as a still card. The bars grow in once (a fade
 * under reduced motion). Decorative, so hidden from screen readers.
 */
export function SignInInsight() {
  const most = REGIONS[0][1];
  return (
    <div className="signin-insight" aria-hidden="true">
      <div className="signin-insight-head">
        <span>sales.csv</span>
        <span className="signin-insight-tag">Insight</span>
      </div>
      <div className="signin-insight-body">
        <small>Revenue, 241 orders</small>
        <strong>RM 153,948.70</strong>
        <span className="signin-insight-ok">
          <Check size={13} strokeWidth={2.5} /> Checked against the file
        </span>
        <ol>
          {REGIONS.map(([name, v], i) => (
            <li key={name} style={{ "--w": (v / most).toFixed(3), "--i": i } as CSSProperties}>
              <span>{name}</span>
              <span className="track">
                <i />
              </span>
              <span className="val">{(v / 1000).toFixed(1)}K</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
