/**
 * EquitySurface3D
 *
 * 3-D surface plot of sampled MC equity paths.
 * X = trading day, Y = simulation index, Z = equity (% of starting balance).
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyObj = Record<string, any>;

import Plot from "./plotly";
import type { McResultData } from "./types";

interface Props {
  data: McResultData;
}

export default function EquitySurface3D({ data }: Props) {
  const curves = data.equity_curves as number[][] | undefined;

  if (!curves || curves.length === 0) {
    return (
      <div className="chart-placeholder">
        Equity surface not available for this phase.
      </div>
    );
  }

  // Downsample to ≤ 100 curves for performance.
  const step    = Math.max(1, Math.floor(curves.length / 100));
  const sampled = curves.filter((_, i) => i % step === 0).slice(0, 100);
  const nDays   = sampled[0].length;
  const xDays   = Array.from({ length: nDays }, (_, i) => i);
  const ySims   = Array.from({ length: sampled.length }, (_, i) => i);

  const plotData: AnyObj[] = [
    {
      type:       "surface",
      x:          xDays,
      y:          ySims,
      z:          sampled,
      colorscale: "Viridis",
      contours:   {
        z: { show: true, usecolormap: true, highlightcolor: "#42f462", project: { z: true } },
      },
      opacity:   0.88,
      showscale: true,
      colorbar:  { thickness: 16 },
    },
  ];

  const layout: AnyObj = {
    title:          { text: "Equity Surface", font: { color: "#e8e8e8", family: "inherit", size: 14 } },
    paper_bgcolor:  "rgba(0,0,0,0)",
    plot_bgcolor:   "rgba(0,0,0,0)",
    scene: {
      xaxis:   { title: { text: "Day" },        gridcolor: "#444", color: "#aaa" },
      yaxis:   { title: { text: "Simulation" }, gridcolor: "#444", color: "#aaa" },
      zaxis:   { title: { text: "Equity %" },   gridcolor: "#444", color: "#aaa" },
      bgcolor: "rgba(0,0,0,0)",
      camera:  { eye: { x: 1.5, y: -1.8, z: 0.9 } },
    },
    font:   { color: "#e8e8e8", family: "inherit" },
    margin: { t: 50, b: 20, l: 20, r: 20 },
    autosize: true,
  };

  return (
    <Plot
      data={plotData as Plotly.Data[]}
      layout={layout as Partial<Plotly.Layout>}
      config={{ responsive: true, displayModeBar: true, displaylogo: false }}
      style={{ width: "100%", height: "460px" }}
      useResizeHandler
    />
  );
}
