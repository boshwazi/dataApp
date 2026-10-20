import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { EChartsCoreOption } from "echarts/core";
import {
  api,
  formatCompact,
  formatMeasure,
  reportApi,
  type ChartData,
  type KpiData,
  type MeasureFormat,
  type ReportRun,
  type ReportState,
  type Visual,
} from "../api";
import { Chart, SERIES, TOKENS } from "../components/Chart";
import { useProject } from "./Project";

export function Report() {
  const { project } = useProject();
  const [params, setParams] = useSearchParams();
  const [state, setState] = useState<ReportState | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [run, setRun] = useState<ReportRun | { error: string } | null>(null);

  useEffect(() => {
    reportApi.state(project.id).then(setState).catch((e) => setError(e.message));
    api.getSettings().then((s) => setHasKey(s.has_api_key)).catch(() => setHasKey(false));
  }, [project.id]);

  async function change(action: () => Promise<ReportState>) {
    setError(null);
    try {
      setState(await action());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function build() {
    setBuilding(true);
    setRun(null);
    setError(null);
    try {
      const result = await reportApi.ai(project.id);
      setRun(result);
      setState(result);
      if (result.new_pages[0]) setParams({ page: result.new_pages[0] }, { replace: true });
    } catch (e) {
      setRun({ error: (e as Error).message });
    } finally {
      setBuilding(false);
    }
  }

  if (!state) return <p className={error ? "bad" : "muted"}>{error ?? "Loading…"}</p>;

  const page = state.pages.find((p) => p.id === params.get("page")) ?? state.pages[0];
  const pending = state.pages.reduce(
    (n, p) => n + (p.status === "AI" ? 1 : 0) + p.visuals.filter((v) => v.status === "AI").length,
    0,
  );

  return (
    <div className="report-view">
      <div className="toolbar">
        <button onClick={build} disabled={!hasKey || building}>
          {building ? "AI is designing the report…" : state.pages.length ? "Add pages with AI" : "Build report with AI"}
        </button>
        <button className="secondary" onClick={() => change(() => reportApi.approve(project.id))} disabled={pending === 0}>
          {pending ? `Approve all AI changes (${pending})` : "All approved"}
        </button>
        <button className="secondary" onClick={() => change(() => reportApi.undo(project.id))} disabled={!state.can_undo}>
          Undo last change
        </button>
        {!hasKey && <span className="muted small">Add an API key in Settings so the AI can build the report.</span>}
      </div>
      {error && <p className="bad">{error}</p>}
      {run && <RunResult run={run} />}

      {state.pages.length === 0 ? (
        <div className="empty-state">
          <h2>Report</h2>
          <p className="muted">
            The AI designs report pages from the model's measures: KPI cards with comparisons, sorted bar charts, trends and
            detail tables, each with an insight. Build the model first, then click "Build report with AI".
          </p>
        </div>
      ) : (
        <>
          <nav className="page-tabs" aria-label="Report pages">
            {state.pages.map((p) => (
              <button
                key={p.id}
                className={p.id === page.id ? "on" : ""}
                onClick={() => setParams({ page: p.id }, { replace: true })}
              >
                {p.title}
                {p.status === "AI" && <span className="badge ai">AI</span>}
              </button>
            ))}
          </nav>

          <section className="report-page">
            <header className="report-head">
              <div>
                <h2>{page.title}</h2>
                {page.summary && (
                  <p className="page-summary">
                    <span className="badge ai">AI</span> {page.summary}
                  </p>
                )}
              </div>
              <div className="report-meta">
                <span>Data as of {state.as_of ?? "unknown"}</span>
                <span className="step-tools">
                  {page.status === "AI" && (
                    <button className="link small" onClick={() => change(() => reportApi.approve(project.id, page.id))}>
                      Approve page
                    </button>
                  )}
                  <button className="link small danger" onClick={() => change(() => reportApi.removePage(project.id, page.id))}>
                    Remove page
                  </button>
                </span>
              </div>
            </header>

            <div className="kpi-strip">
              {page.visuals
                .filter((v) => v.type === "kpi")
                .map((v) => (
                  <VisualFrame key={v.id} visual={v} projectId={project.id} onChange={change}>
                    <KpiCard visual={v} format={state.formats[v.measures[0]]} />
                  </VisualFrame>
                ))}
            </div>
            <div className="visual-grid">
              {page.visuals
                .filter((v) => v.type !== "kpi")
                .map((v) => (
                  <VisualFrame key={v.id} visual={v} projectId={project.id} onChange={change}>
                    <ChartVisual visual={v} formats={state.formats} />
                  </VisualFrame>
                ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function RunResult({ run }: { run: ReportRun | { error: string } }) {
  if ("error" in run) return <div className="notice bad-notice small">{run.error}</div>;
  return (
    <div className="ai-card">
      <span className="badge ai">AI</span>
      <div>
        <p>{run.summary || "The AI designed the report."}</p>
        <p className="muted small">
          Added {run.added.length} {run.added.length === 1 ? "visual" : "visuals"}
          {run.refused.length > 0 && `; ${run.refused.length} refused by the checks:`}
        </p>
        {run.refused.map((r, i) => (
          <p key={i} className="issue small">
            {r.label}: {r.error}
          </p>
        ))}
      </div>
    </div>
  );
}

function VisualFrame(props: {
  visual: Visual;
  projectId: string;
  onChange: (action: () => Promise<ReportState>) => void;
  children: React.ReactNode;
}) {
  const { visual, projectId, onChange } = props;
  return (
    <article className={`visual visual-${visual.type} ${visual.width === "full" ? "full" : ""}`}>
      <header className="visual-head">
        <h3>{visual.title}</h3>
        <span className="visual-tools">
          {visual.status === "AI" && <span className="badge ai">AI</span>}
          {visual.status === "AI" && (
            <button className="link small" onClick={() => onChange(() => reportApi.approve(projectId, visual.id))}>
              Approve
            </button>
          )}
          <button className="icon danger" title="Remove visual" onClick={() => onChange(() => reportApi.removeVisual(projectId, visual.id))}>
            ×
          </button>
        </span>
      </header>
      {visual.error ? <p className="bad small">{visual.error}</p> : props.children}
      {visual.insight && (
        <p className="insight">
          {visual.insight.text}
          {!visual.insight.check.verified && (
            <span className="unverified" title="These numbers do not match the data in this visual">
              {" "}⚠ Unverified number{visual.insight.check.unmatched.length > 1 ? "s" : ""}:{" "}
              {visual.insight.check.unmatched.join(", ")}
            </span>
          )}
        </p>
      )}
    </article>
  );
}

// KPI card: label + period, value, change line (arrow + sign + amount + % + comparison), sparkline, top driver.

function KpiCard({ visual, format }: { visual: Visual; format: MeasureFormat | undefined }) {
  const data = visual.data as KpiData;
  const lowerIsBetter = visual.kpi?.direction === "lower_is_better";
  const good = data.delta !== null && (lowerIsBetter ? data.delta <= 0 : data.delta >= 0);
  const sign = (v: number) => (v >= 0 ? "+" : "−");
  const change =
    data.delta !== null
      ? `${data.delta >= 0 ? "▲" : "▼"} ${sign(data.delta)}${formatCompact(Math.abs(data.delta), format)}${
          data.delta_pct !== null ? ` (${sign(data.delta_pct)}${Math.abs(data.delta_pct * 100).toFixed(1)}%)` : ""
        } ${data.comparison}`
      : null;
  const label = `${visual.title}: ${formatCompact(data.value, format)}${change ? `, ${change}` : ""}`;
  return (
    <div className="kpi" aria-label={label}>
      <div className="kpi-period">{data.period}</div>
      <div className="kpi-value">{formatCompact(data.value, format)}</div>
      {change ? (
        <div className="kpi-change" style={{ color: good ? TOKENS.good : TOKENS.bad }}>
          {change}
        </div>
      ) : (
        <div className="kpi-note">{data.note}</div>
      )}
      {data.sparkline.length > 1 && <Sparkline points={data.sparkline.map((p) => p.value)} months={data.sparkline.length} />}
      {data.driver && (
        <div className="kpi-driver">
          Top driver: {data.driver.name} {sign(data.driver.delta)}
          {formatCompact(Math.abs(data.driver.delta), format)}
        </div>
      )}
    </div>
  );
}

function Sparkline({ points, months }: { points: number[]; months: number }) {
  const width = 120;
  const height = 28;
  const low = Math.min(...points);
  const range = Math.max(...points) - low || 1;
  const xy = points.map((v, i) => [Math.round((i / (points.length - 1)) * width), Math.round(height - ((v - low) / range) * height)]);
  const last = xy[xy.length - 1];
  return (
    <div className="kpi-spark">
      <svg viewBox={`-3 -3 ${width + 6} ${height + 6}`} width={width} height={height} aria-hidden="true">
        <polyline points={xy.map((p) => p.join(",")).join(" ")} fill="none" stroke={TOKENS.accent} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={last[0]} cy={last[1]} r={3} fill={TOKENS.accent} />
      </svg>
      <span>{months} months</span>
    </div>
  );
}

// Charts

function axisValue(format: MeasureFormat | undefined) {
  return (v: number) => formatCompact(v, format);
}

function ChartVisual({ visual, formats }: { visual: Visual; formats: Record<string, MeasureFormat> }) {
  const data = visual.data as ChartData;
  const groups = visual.group_by.length;
  const option = useMemo(() => chartOption(visual, data, formats), [visual, data, formats]);

  if (visual.type === "table") return <DetailTable visual={visual} data={data} formats={formats} />;
  const rows = data.rows.length;
  const height = visual.type === "bar" ? Math.max(160, rows * 32 + 40) : 260;
  return (
    <>
      {option && <Chart option={option} height={height} label={`${visual.title} chart`} />}
      <Footnotes data={data} groups={groups} />
    </>
  );
}

function Footnotes({ data, groups }: { data: ChartData; groups: number }) {
  return (
    <p className="visual-foot">
      {data.truncated && <span>Top {data.rows.length} of {data.total_rows}. </span>}
      {data.check && groups === 1 && (
        <span className={data.check.ok ? "check-ok" : "check-bad"}>
          {data.check.ok ? "✓ Parts add up to the total" : "⚠ Parts do not add up to the total"}
        </span>
      )}
    </p>
  );
}

function chartOption(visual: Visual, data: ChartData, formats: Record<string, MeasureFormat>): EChartsCoreOption | null {
  if (visual.type === "table") return null;
  const groups = visual.group_by.length;
  const labels = data.rows.map((r) => (r[0] === null ? "(blank)" : String(r[0])));
  const first = formats[visual.measures[0]];
  const single = visual.measures.length === 1;
  const colour = (i: number) => (single ? TOKENS.accent : SERIES[i % SERIES.length]);
  const tooltip = {
    trigger: visual.type === "donut" ? "item" : "axis",
    valueFormatter: (v: number) => formatCompact(v, first),
  };
  const legend = single ? undefined : { top: 0, textStyle: { color: TOKENS.muted } };
  const valueAxis = {
    type: "value",
    axisLabel: { color: TOKENS.muted, formatter: axisValue(first) },
    splitLine: { lineStyle: { color: TOKENS.grid } },
  };
  const categoryAxis = (reverse: boolean) => ({
    type: "category",
    data: reverse ? [...labels].reverse() : labels,
    axisLabel: { color: TOKENS.muted, width: 140, overflow: "truncate" },
    axisLine: { lineStyle: { color: TOKENS.grid } },
    axisTick: { show: false },
  });
  const series = (type: string, reverse = false) =>
    visual.measures.map((name, i) => {
      const values = data.rows.map((r) => r[groups + i] as number | null);
      return {
        name,
        type,
        data: reverse ? [...values].reverse() : values,
        itemStyle: { color: colour(i) },
        lineStyle: { color: colour(i), width: 2 },
        barMaxWidth: 28,
        showSymbol: false,
        label:
          type === "bar" && single
            ? { show: true, position: reverse ? "right" : "top", color: TOKENS.text, formatter: (p: { value: number }) => formatCompact(p.value, formats[name]) }
            : undefined,
      };
    });

  if (visual.type === "bar") {
    return {
      tooltip,
      legend,
      grid: { left: 8, right: 64, top: single ? 8 : 28, bottom: 8, containLabel: true },
      xAxis: { ...valueAxis, splitLine: { show: false }, axisLabel: { show: false } },
      yAxis: categoryAxis(true),
      series: series("bar", true),
    };
  }
  if (visual.type === "donut") {
    return {
      tooltip,
      series: [
        {
          type: "pie",
          radius: ["48%", "72%"],
          data: data.rows.map((r, i) => ({
            name: r[0] === null ? "(blank)" : String(r[0]),
            value: r[1],
            itemStyle: { color: SERIES[i % SERIES.length] },
          })),
          label: { formatter: "{b}\n{d}%", color: TOKENS.text },
        },
      ],
    };
  }
  return {
    tooltip,
    legend,
    grid: { left: 8, right: 16, top: single ? 16 : 32, bottom: 8, containLabel: true },
    xAxis: categoryAxis(false),
    yAxis: valueAxis,
    series: series(visual.type === "line" ? "line" : "bar"),
  };
}

function DetailTable({ visual, data, formats }: { visual: Visual; data: ChartData; formats: Record<string, MeasureFormat> }) {
  const groups = visual.group_by.length;
  const key = data.rows.map((r) => Math.abs((r[groups] as number) ?? 0));
  const max = Math.max(...key, 0) || 1;
  return (
    <>
      <div className="table-wrap report-table">
        <table className="grid">
          <thead>
            <tr>
              {data.columns.map((c, i) => (
                <th key={c} className={i >= groups ? "num" : ""}>
                  {i < groups ? c.replace(/^.*\[(.*)\]$/, "$1") : c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, r) => (
              <tr key={r}>
                {row.map((value, i) =>
                  i < groups ? (
                    <td key={i}>{value === null ? <span className="null">(blank)</span> : String(value)}</td>
                  ) : (
                    <td key={i} className="num">
                      {i === groups && (
                        <span className="data-bar" style={{ width: `${(Math.abs((value as number) ?? 0) / max) * 100}%` }} />
                      )}
                      <span className="data-value">
                        {formatMeasure(value as number | null, formats[visual.measures[i - groups]] ?? { type: "number", decimals: 2 })}
                      </span>
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Footnotes data={data} groups={groups} />
    </>
  );
}
