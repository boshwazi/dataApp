import { useEffect, useRef, type MouseEvent as ReactMouseEvent } from "react";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";
import { token, useTheme, type Surface } from "./theme";

echarts.use([BarChart, LineChart, PieChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);

// House style colours, read from the theme tokens so charts follow light and dark. LIGHT_TOKENS reads the light
// surface's values, for dashboards, which stay light in every theme.
function palette(surface: Surface) {
  return {
    get text() {
      return token("--text", surface) || "#1F2937";
    },
    get muted() {
      return token("--muted", surface) || "#6B7280";
    },
    get accent() {
      return token("--chart-accent", surface) || "#005A9C";
    },
    get context() {
      return token("--faint", surface) || "#9CA3AF";
    },
    get grid() {
      return token("--grid-line", surface) || "#E5E7EB";
    },
    get good() {
      return token("--ok", surface) || "#007A5E";
    },
    get bad() {
      return token("--warn", surface) || "#B54A00";
    },
  };
}
export const TOKENS = palette("theme");
export const LIGHT_TOKENS = palette("light");
// Okabe-Ito order, colour-blind safe; never more than 6 series.
export const SERIES = ["#0072B2", "#E69F00", "#009E73", "#56B4E9", "#D55E00", "#CC79A7"];

/** One ECharts instance, resized with its box and disposed on unmount. */
export function Chart(props: { option: EChartsCoreOption; height: number | string; label: string; onPick?: (name: string) => void; surface?: Surface }) {
  const { option, height, label, surface = "theme" } = props;
  const colours = surface === "light" ? LIGHT_TOKENS : TOKENS;
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const theme = useTheme();
  const pick = useRef(props.onPick);
  pick.current = props.onPick;

  useEffect(() => {
    if (!box.current) return;
    chart.current = echarts.init(box.current, undefined, { renderer: "svg" });
    // A click picks a category (for cross-filtering and drilling down). Slices and dots are hit directly;
    // on bar, column and line charts anywhere in a category's band counts, as in Power BI.
    const instance = chart.current;
    instance.on("click", (params) => {
      const p = params as { seriesType?: string; name?: string; data?: unknown };
      if (p.seriesType === "pie") pick.current?.(String(p.name ?? ""));
      else if (p.seriesType === "scatter" && Array.isArray(p.data)) pick.current?.(String(p.data[2] ?? ""));
    });
    const observer = new ResizeObserver(() => chart.current?.resize());
    observer.observe(box.current);
    return () => {
      observer.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(
      {
        textStyle: { fontFamily: "'Host Grotesk', system-ui, 'Segoe UI', sans-serif", color: colours.text },
        animation: false,
        ...option,
        tooltip: option.tooltip
          ? { backgroundColor: token("--panel", surface), borderColor: token("--border", surface), textStyle: { color: colours.text }, ...(option.tooltip as object) }
          : undefined,
      },
      true,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [option, theme, surface]);

  /** On bar, column and line charts a click anywhere in a category's band picks it, as in Power BI. */
  function onClick(e: ReactMouseEvent<HTMLDivElement>) {
    const instance = chart.current;
    if (!instance || !pick.current || !box.current) return;
    const rect = box.current.getBoundingClientRect();
    const at = [e.clientX - rect.left, e.clientY - rect.top];
    if (!instance.containPixel({ gridIndex: 0 }, at)) return;
    const option = instance.getOption() as { xAxis?: { type?: string; data?: unknown[] }[]; yAxis?: { type?: string; data?: unknown[] }[] };
    const x = option.xAxis?.[0];
    const y = option.yAxis?.[0];
    const axis = x?.type === "category" ? { data: x.data, i: 0 } : y?.type === "category" ? { data: y.data, i: 1 } : null;
    const point = instance.convertFromPixel({ gridIndex: 0 }, at) as number[] | null;
    if (!axis?.data || !point) return;
    const index = Math.round(point[axis.i]);
    const name = axis.data[index];
    if (name !== undefined && name !== null) pick.current(String(typeof name === "object" ? (name as { value?: unknown }).value : name));
  }

  return <div ref={box} style={{ height, cursor: props.onPick ? "pointer" : undefined }} role="img" aria-label={label} onClick={props.onPick ? onClick : undefined} />;
}
