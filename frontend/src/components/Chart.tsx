import { useEffect, useRef } from "react";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";

echarts.use([BarChart, LineChart, PieChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);

// House style tokens (light theme, WCAG-checked on white).
export const TOKENS = {
  text: "#1F2937",
  muted: "#6B7280",
  accent: "#005A9C",
  context: "#9CA3AF",
  grid: "#E5E7EB",
  good: "#007A5E",
  bad: "#B54A00",
};
// Okabe-Ito order, colour-blind safe; never more than 6 series.
export const SERIES = ["#0072B2", "#E69F00", "#009E73", "#56B4E9", "#D55E00", "#CC79A7"];

/** One ECharts instance, resized with its box and disposed on unmount. */
export function Chart({ option, height, label }: { option: EChartsCoreOption; height: number | string; label: string }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!box.current) return;
    chart.current = echarts.init(box.current, undefined, { renderer: "svg" });
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
        textStyle: { fontFamily: "system-ui, 'Segoe UI', sans-serif", color: TOKENS.text },
        animation: false,
        ...option,
      },
      true,
    );
  }, [option]);

  return <div ref={box} style={{ height }} role="img" aria-label={label} />;
}
