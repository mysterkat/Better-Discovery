/**
 * PassRateHeatmap3D
 *
 * 3-D bar chart (Scatter3d pillars) showing simulation outcome breakdown:
 *   Pass / Daily DD Breach / Total DD Breach / Profit Shortfall
 *
 * Works for phase1/phase2 (pass/fail) and funded (breach) results.
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyObj = Record<string, any>;

import Plot from "react-plotly.js";
import type { McResultData } from "./types";

interface Props {
  data: McResultData;
}

interface BarEntry { label: string; value: number; color: string; }

function buildBars(data: McResultData): BarEntry[] {
  if (typeof data.pass_rate === "number" && typeof data.n_passed === "number") {
    const failPcts  = data.fail_pcts as Record<string, number> | undefined;
    const dailyFail = failPcts?.daily_dd ?? (data.daily_dd_breach_pct as number) ?? 0;
    const totalFail = failPcts?.total_dd ?? (data.total_dd_breach_pct as number) ?? 0;
    const shortfall = Math.max(0, 100 - (data.pass_rate as number) - dailyFail - totalFail);
    return [
      { label: "Pass",      value: data.pass_rate as number, color: "#33cc66" },
      { label: "Daily DD",  value: dailyFail,                color: "#ff4444" },
      { label: "Total DD",  value: totalFail,                color: "#ff8800" },
      { label: "Shortfall", value: shortfall,                color: "#ffcc00" },
    ];
  }
  if (typeof data.breach_rate === "number") {
    const bPcts = data.breach_pcts as Record<string, number> | undefined;
    return [
      { label: "Surviving", value: 100 - (data.breach_rate as number), color: "#33cc66" },
      { label: "Daily DD",  value: bPcts?.daily_dd ?? 0,               color: "#ff4444" },
      { label: "Total DD",  value: bPcts?.total_dd ?? 0,               color: "#ff8800" },
    ];
  }
  if (typeof data.pass_rate === "number") {
    return [
      { label: "Surviving", value: (data.pass_rate as number) * 100,       color: "#33cc66" },
      { label: "Ruined",    value: (1 - (data.pass_rate as number)) * 100, color: "#ff4444" },
    ];
  }
  return [];
}

export default function PassRateHeatmap3D({ data }: Props) {
  const bars = buildBars(data);
  if (bars.length === 0) {
    return <div className="chart-placeholder">No pass-rate data available.</div>;
  }

  const labels = bars.map((b) => b.label);
  const values = bars.map((b) => Math.max(0, b.value));

  // Vertical pillars — one Scatter3d trace per bar.
  const pillars: AnyObj[] = bars.map((bar) => ({
    type:       "scatter3d",
    mode:       "lines+markers",
    name:       bar.label,
    x:          [bar.label, bar.label],
    y:          [0, 0],
    z:          [0, Math.max(0, bar.value)],
    line:       { color: bar.color, width: 24 },
    marker:     { size: 8, color: bar.color, symbol: "square" },
    showlegend: true,
  }));

  // Top-cap text labels.
  const caps: AnyObj = {
    type:         "scatter3d",
    mode:         "text+markers",
    x:            labels,
    y:            new Array(bars.length).fill(0),
    z:            values,
    marker:       { size: 7, color: bars.map((b) => b.color), symbol: "square" },
    text:         values.map((v) => `${v.toFixed(1)}%`),
    textfont:     { color: "#ffffff", size: 11, family: "inherit" },
    textposition: "top center",
    showlegend:   false,
    hoverinfo:    "skip",
  };

  const layout: AnyObj = {
    title:         { text: "Outcome Breakdown", font: { color: "#e8e8e8", family: "inherit", size: 14 } },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor:  "rgba(0,0,0,0)",
    scene: {
      xaxis:   { title: { text: "" },                   gridcolor: "#444", color: "#aaa" },
      yaxis:   { title: { text: "" },                   gridcolor: "#333", color: "#333", showticklabels: false },
      zaxis:   { title: { text: "% of Simulations" },   gridcolor: "#444", color: "#aaa", range: [0, 105] },
      bgcolor: "rgba(0,0,0,0)",
      camera:  { eye: { x: 1.6, y: -1.2, z: 0.9 } },
    },
    legend:   { font: { color: "#e8e8e8", family: "inherit" }, bgcolor: "rgba(0,0,0,0)" },
    font:     { color: "#e8e8e8", family: "inherit" },
    margin:   { t: 50, b: 20, l: 20, r: 20 },
    autosize: true,
  };

  return (
    <Plot
      data={[...pillars, caps] as Plotly.Data[]}
      layout={layout as Partial<Plotly.Layout>}
      config={{ responsive: true, displayModeBar: true, displaylogo: false }}
      style={{ width: "100%", height: "460px" }}
      useResizeHandler
    />
  );
}
