import { useEffect, useRef, useState, type FormEvent } from "react";
import type { EChartsCoreOption } from "echarts/core";
import { askApi, formatCompact, formatMeasure, type AskResponse, type AskResult, type MeasureFormat } from "../api";
import { Send, Square, X } from "lucide-react";
import { Chart, TOKENS } from "./Chart";
import { Markdown } from "./Markdown";

type Turn = { question: string; response?: AskResponse; error?: string };

const STARTERS = ["What stands out on this dashboard?", "Which category is growing fastest?", "Show the monthly trend"];

/**
 * Ask questions about a dashboard's data. Read-only: answers come from the same measures the
 * dashboard uses, each with "how this was calculated". The conversation lives in this browser tab.
 */
export function AskPanel({ dashboardId, side, onClose }: { dashboardId: string; side: "draft" | "published"; onClose?: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [turns, busy]);

  async function ask(question: string) {
    const clean = question.trim();
    if (!clean || busy) return;
    setText("");
    setBusy(true);
    const history = turns.flatMap((t) => [
      { role: "user", content: t.question },
      { role: "assistant", content: t.response?.answer ?? t.error ?? "" },
    ]);
    setTurns((current) => [...current, { question: clean }]);
    const abort = new AbortController();
    controller.current = abort;
    try {
      const response = await askApi.ask(dashboardId, clean, history, side, abort.signal);
      setTurns((current) => current.map((t, i) => (i === current.length - 1 ? { ...t, response } : t)));
    } catch (e) {
      const error = abort.signal.aborted ? "Stopped." : (e as Error).message;
      setTurns((current) => current.map((t, i) => (i === current.length - 1 ? { ...t, error } : t)));
    } finally {
      controller.current = null;
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(text);
  }

  return (
    <aside className="chat-panel ask-panel" aria-label="Ask about this data">
      <header className="chat-head">
        <div>
          <strong>Ask about this data</strong>
          <span className="muted small">Answers show how each number was calculated</span>
        </div>
        {onClose && (
          <button className="icon" onClick={onClose} aria-label="Close" title="Close">
            <X size={16} />
          </button>
        )}
      </header>
      <div className="chat-messages" ref={list}>
        {turns.length === 0 && (
          <div className="chat-empty">
            <p className="muted small">Try:</p>
            {STARTERS.map((s) => (
              <button key={s} className="chip" onClick={() => ask(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {turns.map((turn, i) => (
          <div key={i} className="ask-turn">
            <div className="chat-msg user">
              <p>{turn.question}</p>
            </div>
            {turn.error && <div className="chat-msg assistant bad small">{turn.error}</div>}
            {turn.response && <Answer response={turn.response} onFollowUp={ask} busy={busy} />}
          </div>
        ))}
        {busy && <div className="chat-msg assistant thinking">Working it out…</div>}
      </div>
      <form className="chat-input" onSubmit={submit}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask(text);
            }
          }}
          placeholder="Ask a question about this data…"
          rows={2}
          aria-label="Question"
        />
        {busy ? (
          <button type="button" className="secondary" onClick={() => controller.current?.abort()} aria-label="Stop this question">
            <Square size={14} aria-hidden="true" /> Stop
          </button>
        ) : (
          <button type="submit" disabled={!text.trim()}>
            <Send size={15} aria-hidden="true" /> Ask
          </button>
        )}
      </form>
    </aside>
  );
}

function Answer({ response, onFollowUp, busy }: { response: AskResponse; onFollowUp: (q: string) => void; busy: boolean }) {
  return (
    <div className="chat-msg assistant answer">
      <Markdown text={response.answer || response.cannot_answer || ""} />
      {!response.check.verified && (
        <p className="unverified small">⚠ Unverified number{response.check.unmatched.length > 1 ? "s" : ""}: {response.check.unmatched.join(", ")}. Check the results below.</p>
      )}
      {response.results.map((result, i) => (
        <ResultCard key={i} result={result} formats={response.formats} />
      ))}
      <p className="muted small">
        Data as of {response.as_of ?? "unknown"}, dataset v{response.dataset_version}
      </p>
      {response.follow_ups.length > 0 && (
        <div className="chat-empty">
          {response.follow_ups.map((f) => (
            <button key={f} className="chip" onClick={() => onFollowUp(f)} disabled={busy}>
              {f}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ResultCard({ result, formats }: { result: AskResult; formats: Record<string, MeasureFormat> }) {
  if (result.error) return <p className="bad small">{result.title}: {result.error}</p>;
  const groups = result.query.group_by.length;
  const measures = result.query.measures;
  const single = groups === 0 && result.rows.length === 1;
  return (
    <div className="ask-result">
      <div className="ask-title">{result.title}</div>
      {single ? (
        <div className="ask-number">
          {measures.map((m, i) => (
            <div key={m}>
              <span className="kpi-value small-kpi">{formatCompact(result.rows[0][i] as number, formats[m])}</span>
              <span className="muted small"> {m}</span>
            </div>
          ))}
        </div>
      ) : (
        <>
          {groups === 1 && (result.show_as === "bar" || result.show_as === "line") && result.rows.length > 1 && (
            <Chart option={miniChart(result, formats)} height={result.show_as === "bar" ? Math.min(40 + result.rows.length * 24, 300) : 180} label={result.title} />
          )}
          <div className="table-wrap ask-table">
            <table className="grid">
              <thead>
                <tr>
                  {result.columns.map((c, i) => (
                    <th key={c} className={i >= groups ? "num" : ""}>
                      {i < groups ? c.replace(/^.*\[(.*)\]$/, "$1") : c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((v, i) => (
                      <td key={i} className={i >= groups ? "num" : ""}>
                        {i < groups ? (v === null ? "(blank)" : String(v)) : formatMeasure(v as number | null, formats[measures[i - groups]] ?? { type: "number", decimals: 2 })}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.truncated && <p className="muted small">Showing the first {result.rows.length} rows.</p>}
        </>
      )}
      {result.how && (
        <details className="how">
          <summary>How this was calculated</summary>
          <ul>
            {result.how.measures.map((m) => (
              <li key={m.name}>
                <strong>{m.name}</strong>: {m.definition}
              </li>
            ))}
            {result.how.split_by.length > 0 && <li>Split by {result.how.split_by.join(", ")}</li>}
            {result.how.filters.length > 0 && <li>Filtered to {result.how.filters.join(" and ")}</li>}
            <li>Tables used: {result.how.tables.join(", ")}</li>
          </ul>
        </details>
      )}
    </div>
  );
}

function miniChart(result: AskResult, formats: Record<string, MeasureFormat>): EChartsCoreOption {
  const measure = result.query.measures[0];
  const labels = result.rows.map((r) => (r[0] === null ? "(blank)" : String(r[0])));
  const values = result.rows.map((r) => r[1] as number | null);
  const format = (v: number) => formatCompact(v, formats[measure]);
  if (result.show_as === "bar") {
    return {
      grid: { left: 4, right: 56, top: 4, bottom: 4, containLabel: true },
      xAxis: { type: "value", show: false },
      yAxis: { type: "category", data: [...labels].reverse(), axisLabel: { color: TOKENS.muted, width: 110, overflow: "truncate" }, axisTick: { show: false } },
      series: [{ type: "bar", data: [...values].reverse(), itemStyle: { color: TOKENS.accent }, barMaxWidth: 18,
                 label: { show: true, position: "right", color: TOKENS.text, formatter: (p: { value: number }) => format(p.value) } }],
    };
  }
  return {
    grid: { left: 4, right: 12, top: 12, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: format },
    xAxis: { type: "category", data: labels, axisLabel: { color: TOKENS.muted } },
    yAxis: { type: "value", axisLabel: { color: TOKENS.muted, formatter: format }, splitLine: { lineStyle: { color: TOKENS.grid } } },
    series: [{ type: "line", data: values, showSymbol: false, lineStyle: { color: TOKENS.accent, width: 2 }, itemStyle: { color: TOKENS.accent } }],
  };
}
