/**
 * Scatter3D
 *
 * 3-D scatter of individual simulation outcomes.
 *
 * phase1/phase2: X=days, Y=final equity, Z=sim index  — colour = pass/fail
 * funded:        X=days active, Y=earnings, Z=payouts  — colour = breach/ok
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyObj = Record<string, any>;

import Plot from "./plotly";
import type { McResultData } from "./types";

interface Props {
  data: McResultData;
}

interface ResultRecord {
  passed?: boolean;
  breach?: boolean;
  days?: number;
  final_equity?: number;
  days_active?: number;
  total_earnings?: number;
  payout_count?: number;
  fail_reason?: string | null;
}

function makeLayout(title: string, x: string, y: string, z: string): AnyObj {
  return {
    title:         { text: title, font: { color: "#e8e8e8", family: "inherit", size: 14 } },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor:  "rgba(0,0,0,0)",
    scene: {
      xaxis:   { title: { text: x }, gridcolor: "#444", color: "#aaa" },
      yaxis:   { title: { text: y }, gridcolor: "#444", color: "#aaa" },
      zaxis:   { title: { text: z }, gridcolor: "#444", color: "#aaa" },
      bgcolor: "rgba(0,0,0,0)",
      camera:  { eye: { x: 1.5, y: -1.5, z: 0.7 } },
    },
    legend:   { font: { color: "#e8e8e8", family: "inherit" }, bgcolor: "rgba(0,0,0,0)" },
    font:     { color: "#e8e8e8", family: "inherit" },
    margin:   { t: 50, b: 20, l: 20, r: 20 },
    autosize: true,
  };
}

export default function Scatter3D({ data }: Props) {
  const df      = data.results_df as { columns: string[]; records: ResultRecord[] } | undefined;
  const records = df?.records;

  if (!records || records.length === 0) {
    return (
      <div className="chart-placeholder">
        Scatter data not available for this phase.
      </div>
    );
  }

  // Downsample to 2 000 points for responsiveness.
  const step   = Math.max(1, Math.floor(records.length / 2000));
  const sample = records.filter((_, i) => i % step === 0);

  // phase1 / phase2
  if ("passed" in (records[0] ?? {})) {
    const passed = sample.filter((r) => r.passed);
    const failed = sample.filter((r) => !r.passed);

    const plotData: AnyObj[] = [
      {
        type:   "scatter3d",
        mode:   "markers",
        name:   "Pass",
        x:      passed.map((r) => r.days ?? 0),
        y:      passed.map((r) => r.final_equity ?? 0),
        z:      passed.map((_, i) => i),
        marker: { size: 3, color: "#33dd77", opacity: 0.7 },
      },
      {
        type:   "scatter3d",
        mode:   "markers",
        name:   "Fail",
        x:      failed.map((r) => r.days ?? 0),
        y:      failed.map((r) => r.final_equity ?? 0),
        z:      failed.map((_, i) => i),
        marker: {
          size:    3,
          color:   failed.map((r) => r.fail_reason === "daily_dd" ? "#ff4444" : "#ff9900"),
          opacity: 0.6,
        },
      },
    ];

    return (
      <Plot
        data={plotData as Plotly.Data[]}
        layout={makeLayout("Simulation Scatter", "Days", "Final Equity", "Index") as Partial<Plotly.Layout>}
        config={{ responsive: true, displayModeBar: true, displaylogo: false }}
        style={{ width: "100%", height: "460px" }}
        useResizeHandler
      />
    );
  }

  // funded
  if ("breach" in (records[0] ?? {})) {
    const survived = sample.filter((r) => !r.breach);
    const breached = sample.filter((r) => r.breach);

    const plotData: AnyObj[] = [
      {
        type:   "scatter3d",
        mode:   "markers",
        name:   "Survived",
        x:      survived.map((r) => r.days_active ?? 0),
        y:      survived.map((r) => r.total_earnings ?? 0),
        z:      survived.map((r) => r.payout_count ?? 0),
        marker: { size: 3, color: "#33dd77", opacity: 0.7 },
      },
      {
        type:   "scatter3d",
        mode:   "markers",
        name:   "Breach",
        x:      breached.map((r) => r.days_active ?? 0),
        y:      breached.map((r) => r.total_earnings ?? 0),
        z:      breached.map((r) => r.payout_count ?? 0),
        marker: { size: 3, color: "#ff4444", opacity: 0.6 },
      },
    ];

    return (
      <Plot
        data={plotData as Plotly.Data[]}
        layout={makeLayout("Funded Scatter", "Days Active", "Total Earnings", "Payouts") as Partial<Plotly.Layout>}
        config={{ responsive: true, displayModeBar: true, displaylogo: false }}
        style={{ width: "100%", height: "460px" }}
        useResizeHandler
      />
    );
  }

  return (
    <div className="chart-placeholder">No scatter data available for this phase.</div>
  );
}
