/**
 * Chart3D
 *
 * Renders all five 3-D Plotly charts in a scrollable grid.
 * Each chart is labelled and lazy-loaded via React.Suspense.
 */

import { Suspense, lazy } from "react";
import type { McResultData } from "./types";

const EquitySurface3D   = lazy(() => import("./EquitySurface3D"));
const DrawdownCone3D    = lazy(() => import("./DrawdownCone3D"));
const PassRateHeatmap3D = lazy(() => import("./PassRateHeatmap3D"));
const Scatter3D         = lazy(() => import("./Scatter3D"));
const AnimatedEquity3D  = lazy(() => import("./AnimatedEquity3D"));

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
      <Suspense fallback={<div className="chart-placeholder">Loading chart…</div>}>
        <Component data={data} />
      </Suspense>
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
