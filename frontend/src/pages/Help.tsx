import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Search } from "lucide-react";
import { useAccount } from "../components/account";
import { Markdown } from "../components/Markdown";
import { useCrumbs } from "../components/ui";
import { ARTICLES } from "../help";

/** Help: short articles on each part of the app, the ones for your role first. */
export function Help() {
  const account = useAccount();
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("");
  useCrumbs([{ label: "Help" }]);

  const mine = (a: (typeof ARTICLES)[number]) =>
    a.for === "everyone" || (a.for === "creators" && account.can("creator")) || (a.for === "admins" && account.can("admin"));
  const shown = useMemo(() => {
    const words = filter.trim().toLowerCase();
    const listed = [...ARTICLES.filter(mine), ...ARTICLES.filter((a) => !mine(a))];
    return words ? listed.filter((a) => `${a.title} ${a.summary} ${a.body}`.toLowerCase().includes(words)) : listed;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);
  const current = ARTICLES.find((a) => a.id === params.get("a")) ?? shown[0] ?? ARTICLES[0];

  return (
    <div className="page wide help">
      <div className="home-head">
        <h1>Help</h1>
        <span className="spacer" />
        <label className="filter-box">
          <Search size={15} aria-hidden="true" />
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search help…" aria-label="Search help" />
        </label>
      </div>
      <div className="help-layout">
        <nav className="help-index" aria-label="Help articles">
          {shown.map((a) => (
            <button key={a.id} className={`help-link ${a.id === current.id ? "on" : ""}`} onClick={() => setParams({ a: a.id })}>
              <strong>{a.title}</strong>
              <span className="muted small">{a.summary}</span>
            </button>
          ))}
          {shown.length === 0 && <p className="muted small">Nothing matches "{filter}".</p>}
        </nav>
        <article className="help-article card">
          <h2>{current.title}</h2>
          <Markdown text={current.body.trim()} />
        </article>
      </div>
    </div>
  );
}
