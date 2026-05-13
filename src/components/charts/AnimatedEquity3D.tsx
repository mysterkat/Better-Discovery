/**
 * AnimatedEquity3D
 *
 * Animated 3-D ribbon chart of equity paths.
 * Uses Plotly animation frames with Play / Pause controls and a day slider.
 * 30 key-frames evenly spread over the full trading-day range.
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyObj = Record<string, any>;

import { useMemo } from "react";
import Plot from "./plotly";
import type { McResultData } from "./types";

interface Props {
  data: McResultData;
}

const N_FRAMES   = 30;   // animation key-frames
const MAX_CURVES = 60;   // curves shown (kept low for smooth animation)

export default function AnimatedEquity3D({ data }: Props) {
  const curves = data.equity_curves as number[][] | undefined;

  const built = useMemo<{
    frames: AnyObj[];
    initialData: AnyObj[];
    layout: AnyObj;
  } | null>(() => {
    if (!curves || curves.length === 0) return null;

    const step    = Math.max(1, Math.floor(curves.length / MAX_CURVES));
    const sampled = curves.filter((_, i) => i % step === 0).slice(0, MAX_CURVES);
    const nDays   = sampled[0].length;

    const frameDays: number[] = Array.from({ length: N_FRAMES }, (_, fi) =>
      Math.round((fi / (N_FRAMES - 1)) * (nDays - 1))
    );

    const makeTraces = (upToDay: number): AnyObj[] =>
      sampled.map((curve, simIdx) => ({
        type:       "scatter3d",
        mode:       "lines",
        x:          curve.slice(0, upToDay + 1),
        y:          new Array(upToDay + 1).fill(simIdx),
        z:          Array.from({ length: upToDay + 1 }, (_, d) => d),
        line:       { color: curve[upToDay] ?? 100, width: 2, colorscale: "Viridis", cmin: 80, cmax: 120 },
        showlegend: false,
      }));

    const frames: AnyObj[] = frameDays.map((day, fi) => ({
      name: String(fi),
      data: makeTraces(day),
    }));

    const initialData: AnyObj[] = makeTraces(0);

    const layout: AnyObj = {
      title:         { text: "Equity Paths (animated)", font: { color: "#e8e8e8", family: "inherit", size: 14 } },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor:  "rgba(0,0,0,0)",
      scene: {
        xaxis:   { title: { text: "Equity %" }, gridcolor: "#444", color: "#aaa" },
        yaxis:   { title: { text: "Simulation" }, gridcolor: "#444", color: "#aaa" },
        zaxis:   { title: { text: "Day" }, gridcolor: "#444", color: "#aaa", range: [0, nDays] },
        bgcolor: "rgba(0,0,0,0)",
        camera:  { eye: { x: 1.6, y: -1.8, z: 0.7 } },
      },
      updatemenus: [
        {
          type:       "buttons",
          showactive: false,
          x: 0.08, y: 0,
          xanchor: "right", yanchor: "top",
          pad: { t: 45 },
          buttons: [
            {
              label:  "▶ Play",
              method: "animate",
              args:   [null, { fromcurrent: true, transition: { duration: 80, easing: "linear" }, frame: { duration: 80, redraw: false } }],
            },
            {
              label:  "⏸ Pause",
              method: "animate",
              args:   [[null], { mode: "immediate", transition: { duration: 0, easing: "linear" }, frame: { duration: 0, redraw: false } }],
            },
          ],
        },
      ],
      sliders: [
        {
          pad: { t: 30 },
          currentvalue: {
            prefix: "Frame: ", visible: true, xanchor: "center",
            font: { size: 12, color: "#ccc", family: "inherit" },
          },
          transition: { duration: 80, easing: "linear" },
          x: 0.08, len: 0.86,
          steps: frameDays.map((day, fi) => ({
            label:  String(day),
            method: "animate",
            args:   [[String(fi)], { mode: "immediate", transition: { duration: 80, easing: "linear" }, frame: { duration: 80, redraw: false } }],
          })),
        },
      ],
      font:     { color: "#e8e8e8", family: "inherit" },
      margin:   { t: 50, b: 60, l: 20, r: 20 },
      autosize: true,
    };

    return { frames, initialData, layout };
  }, [curves]);

  if (!built) {
    return (
      <div className="chart-placeholder">
        Equity animation not available for this phase.
      </div>
    );
  }

  return (
    <Plot
      data={built.initialData as Plotly.Data[]}
      layout={built.layout as Partial<Plotly.Layout>}
      frames={built.frames as unknown as Plotly.Frame[]}
      config={{ responsive: true, displayModeBar: true, displaylogo: false }}
      style={{ width: "100%", height: "500px" }}
      useResizeHandler
    />
  );
}
