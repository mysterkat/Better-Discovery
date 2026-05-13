/**
 * DrawdownCone3D
 *
 * 3-D surface plot of running max-drawdown bands over time.
 * X = trading day, Y = percentile level, Z = drawdown %.
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyObj = Record<string, any>;

import Plot from "./plotly";
import type { McResultData } from "./types";

interface Props {
  data: McResultData;
}

const PERCENTILES = [5, 10, 25, 50, 75, 90, 95];

function runningMaxDrawdown(curve: number[]): number[] {
  let peak = curve[0];
  return curve.map((v) => {
    if (v > peak) peak = v;
    return peak > 0 ? -((peak - v) / peak) * 100 : 0;
  });
}

export default function DrawdownCone3D({ data }: Props) {
  const curves = data.equity_curves as number[][] | undefined;

  if (!curves || curves.length === 0) {
    return (
      <div className="chart-placeholder">
        Drawdown cone not available for this phase.
      </div>
    );
  }

  const nDays    = curves[0].length;
  const xDays    = Array.from({ length: nDays }, (_, i) => i);
  const ddMatrix = curves.map(runningMaxDrawdown);

  const zSurface: number[][] = PERCENTILES.map((pct) =>
    xDays.map((day) => {
      const vals = ddMatrix.map((sim) => sim[day]).sort((a, b) => a - b);
      return vals[Math.floor((pct / 100) * (vals.length - 1))];
    })
  );

  const plotData: AnyObj[] = [
    {
      type:         "surface",
      x:            xDays,
      y:            PERCENTILES,
      z:            zSurface,
      colorscale:   [[0, "#ff3b3b"], [0.5, "#ffaa33"], [1, "#33ff99"]],
      reversescale: true,
      opacity:      0.85,
      showscale:    true,
      colorbar:     { thickness: 16 },
    },
  ];

  const layout: AnyObj = {
    title:         { text: "Drawdown Cone", font: { color: "#e8e8e8", family: "inherit", size: 14 } },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor:  "rgba(0,0,0,0)",
    scene: {
      xaxis:   { title: { text: "Day" },         gridcolor: "#444", color: "#aaa" },
      yaxis:   { title: { text: "Percentile" },  gridcolor: "#444", color: "#aaa", tickvals: PERCENTILES },
      zaxis:   { title: { text: "Drawdown %" },  gridcolor: "#444", color: "#aaa" },
      bgcolor: "rgba(0,0,0,0)",
      camera:  { eye: { x: 1.4, y: -1.6, z: 0.8 } },
    },
    font:     { color: "#e8e8e8", family: "inherit" },
    margin:   { t: 50, b: 20, l: 20, r: 20 },
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
