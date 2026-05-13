/**
 * Chart2D
 *
 * Classic 2-D Plotly panel for MC results.
 *   1. Equity fan (percentile bands) — requires equity_curves
 *   2. Pass/breach rate donut
 *   3. Days-to-complete histogram (phase1/phase2)
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyObj = Record<string, any>;

import Plot from "./plotly";
import type { McResultData } from "./types";

interface Props {
  data: McResultData;
}

const BASE: AnyObj = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor:  "rgba(22,22,30,0.5)",
  font:          { color: "#e8e8e8", family: "inherit" },
  margin:        { t: 48, b: 40, l: 56, r: 24 },
  autosize:      true,
};

const GRID: AnyObj = { gridcolor: "#2e2e40", zerolinecolor: "#444" };

// ── Equity Fan ────────────────────────────────────────────────────────────────

function EquityFan({ data }: Props) {
  const curves = data.equity_curves as number[][] | undefined;
  if (!curves || curves.length === 0) {
    return (
      <div className="chart-placeholder">
        Equity fan unavailable — no equity curves were sampled for this phase.
      </div>
    );
  }

  const nDays = curves[0].length;
  const xDays = Array.from({ length: nDays }, (_, i) => i);
  const PCTS  = [5, 25, 50, 75, 95];

  const pctSeries = PCTS.map((pct) =>
    xDays.map((day) => {
      const vals = curves.map((c) => c[day]).sort((a, b) => a - b);
      return vals[Math.floor((pct / 100) * (vals.length - 1))];
    })
  );

  const COLORS    = ["#5577cc", "#6699ee", "#ffffff", "#6699ee", "#5577cc"];
  const FILLS     = ["none", "tonexty", "tonexty", "tonexty", "tonexty"];
  const OPACITIES = [0.12, 0.18, 0, 0.18, 0.12];

  const traces: AnyObj[] = pctSeries.map((vals, i) => ({
    type:      "scatter",
    mode:      "lines",
    x:         xDays,
    y:         vals,
    name:      `p${PCTS[i]}`,
    line:      { color: COLORS[i], width: i === 2 ? 2.5 : 1, dash: i === 2 ? "solid" : "dot" },
    fill:      FILLS[i],
    fillcolor: `rgba(100,160,255,${OPACITIES[i]})`,
  }));

  return (
    <Plot
      data={traces as Plotly.Data[]}
      layout={{
        ...BASE,
        title:  { text: "Equity Fan (percentile bands)", font: { color: "#e8e8e8", family: "inherit", size: 13 } },
        xaxis:  { title: { text: "Day" }, ...GRID },
        yaxis:  { title: { text: "Equity %" }, ...GRID },
        legend: { font: { color: "#ccc", family: "inherit" }, bgcolor: "rgba(0,0,0,0)" },
        height: 320,
      } as Partial<Plotly.Layout>}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  );
}

// ── Pass/Fail Donut ───────────────────────────────────────────────────────────

function PassDonut({ data }: Props) {
  let labels: string[];
  let values: number[];
  let colors: string[];

  if (typeof data.pass_rate === "number" && typeof data.n_passed === "number") {
    const failPcts  = data.fail_pcts as Record<string, number> | undefined;
    const dailyFail = failPcts?.daily_dd ?? (data.daily_dd_breach_pct as number) ?? 0;
    const totalFail = failPcts?.total_dd ?? (data.total_dd_breach_pct as number) ?? 0;
    const shortfall = Math.max(0, 100 - (data.pass_rate as number) - dailyFail - totalFail);
    labels = ["Pass", "Daily DD", "Total DD", "Shortfall"];
    values = [data.pass_rate as number, dailyFail, totalFail, shortfall];
    colors = ["#33cc66", "#ff4444", "#ff8800", "#ffcc00"];
  } else if (typeof data.breach_rate === "number") {
    const bPcts = data.breach_pcts as Record<string, number> | undefined;
    labels = ["Surviving", "Daily DD", "Total DD"];
    values = [100 - (data.breach_rate as number), bPcts?.daily_dd ?? 0, bPcts?.total_dd ?? 0];
    colors = ["#33cc66", "#ff4444", "#ff8800"];
  } else {
    return null;
  }

  const pieData: AnyObj[] = [
    {
      type:      "pie",
      labels,
      values:    values.map((v) => Math.max(0, v)),
      hole:      0.55,
      marker:    { colors },
      textfont:  { color: "#fff", size: 12, family: "inherit" },
      hoverinfo: "label+percent",
    },
  ];

  return (
    <Plot
      data={pieData as Plotly.Data[]}
      layout={{
        ...BASE,
        title:  { text: "Outcome Breakdown", font: { color: "#e8e8e8", family: "inherit", size: 13 } },
        legend: { font: { color: "#ccc", family: "inherit" }, bgcolor: "rgba(0,0,0,0)" },
        height: 300,
      } as Partial<Plotly.Layout>}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  );
}

// ── Days Histogram ─────────────────────────────────────────────────────────

function DaysHistogram({ data }: Props) {
  const df      = data.results_df as { columns: string[]; records: AnyObj[] } | undefined;
  const records = df?.records;
  if (!records || records.length === 0) return null;

  const hasPassed = "passed" in (records[0] ?? {});
  const days      = hasPassed
    ? records.filter((r) => r.passed).map((r) => r.days as number)
    : records.map((r) => (r.days_active ?? r.days) as number);

  if (days.length === 0) return null;

  const histData: AnyObj[] = [
    {
      type:   "histogram",
      x:      days,
      nbinsx: 30,
      marker: { color: "#5599ee", opacity: 0.85, line: { color: "#334", width: 0.5 } },
      name:   hasPassed ? "Days to pass" : "Days active",
    },
  ];

  return (
    <Plot
      data={histData as Plotly.Data[]}
      layout={{
        ...BASE,
        title:  { text: hasPassed ? "Days to Pass (passing sims)" : "Days Active", font: { color: "#e8e8e8", family: "inherit", size: 13 } },
        xaxis:  { title: { text: "Days" }, ...GRID },
        yaxis:  { title: { text: "Count" }, ...GRID },
        bargap: 0.04,
        height: 280,
      } as Partial<Plotly.Layout>}
      config={{ responsive: true, displayModeBar: false }}
      style={{ width: "100%" }}
      useResizeHandler
    />
  );
}

// ── Root export ───────────────────────────────────────────────────────────────

export default function Chart2D({ data }: Props) {
  return (
    <div className="charts-2d-stack">
      <EquityFan data={data} />
      <div className="charts-2d-row">
        <PassDonut data={data} />
        <DaysHistogram data={data} />
      </div>
    </div>
  );
}
