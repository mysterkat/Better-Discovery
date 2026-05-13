/**
 * Chart3D
 *
 * Renders all five 3-D Plotly charts in a scrollable grid.
 *
 * NOTE on lazy loading: previously each chart was wrapped in lazy(() => import(...))
 * inside its own <Suspense> boundary. With the shared ./plotly factory the
 * full plotly bundle is loaded ONCE for the whole window. Lazy-splitting each
 * tiny per-chart wrapper just added Suspense flicker for no payoff, so the
 * components are imported eagerly here.
 */

import type { McResultData } from "./types";
import EquitySurface3D from "./EquitySurface3D";
import DrawdownCone3D from "./DrawdownCone3D";
import PassRateHeatmap3D from "./PassRateHeatmap3D";
import Scatter3D from "./Scatter3D";
import AnimatedEquity3D from "./AnimatedEquity3D";

interface Props {
  data: McResultData;
}

const CHARTS = [
  { Component: EquitySurface3D,   label: "Equity Surface" },
  { Component: DrawdownCone3D,    label: "Drawdown Cone" },
  { Component: PassRateHeatmap3D, label: "Outcome Breakdown" },
  { Component: Scatter3D,         label: "Simulation Scatter" },
  { Component: AnimatedEquity3D,  label: "Animated Equity" },
];

function ChartCard({
  label,
  Component,
  data,
}: {
  label: string;
  Component: React.ComponentType<{ data: McResultData }>;
  data: McResultData;
}) {
  return (
    <div className="chart-card">
      <div className="chart-card-label">{label}</div>
      <Component data={data} />
    </div>
  );
}

export default function Chart3D({ data }: Props) {
  return (
    <div className="charts-3d-grid">
      {CHARTS.map(({ Component, label }) => (
        <ChartCard key={label} label={label} Component={Component} data={data} />
      ))}
    </div>
  );
}