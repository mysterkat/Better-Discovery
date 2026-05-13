/**
 * Plotly theme helper for the Monte Carlo dashboard window.
 *
 * Reads live CSS custom-property values from :root so charts re-theme when the
 * user switches between dark / light / midnight-blue / custom palettes.
 */

import type { Config, Layout, ModeBarDefaultButtons } from "plotly.js";

export interface ChartTheme {
  bg: string;
  bg2: string;
  bg3: string;
  border: string;
  text: string;
  text2: string;
  accent: string;
  accent2: string;
  danger: string;
  warn: string;

  // Derived chart-specific colors.
  pass: string;     // green for pass / surviving / payout
  fail: string;     // red for fail / breach
  alt:  string;     // orange/amber for warnings
  axisGrid: string;
}

const FALLBACK: ChartTheme = {
  bg:       "#0b0f17",
  bg2:      "#161b22",
  bg3:      "#21262d",
  border:   "#30363d",
  text:     "#e6edf3",
  text2:    "#8b949e",
  accent:   "#238636",
  accent2:  "#1f6feb",
  danger:   "#da3633",
  warn:     "#d29922",
  pass:     "#238636",
  fail:     "#da3633",
  alt:      "#d29922",
  axisGrid: "rgba(139,148,158,0.18)",
};

function readVar(root: HTMLElement, name: string, fallback: string): string {
  const v = getComputedStyle(root).getPropertyValue(name).trim();
  return v || fallback;
}

export function readTheme(): ChartTheme {
  if (typeof document === "undefined") return FALLBACK;
  const root = document.documentElement;
  const t: ChartTheme = {
    bg:      readVar(root, "--bg",      FALLBACK.bg),
    bg2:     readVar(root, "--bg2",     FALLBACK.bg2),
    bg3:     readVar(root, "--bg3",     FALLBACK.bg3),
    border:  readVar(root, "--border",  FALLBACK.border),
    text:    readVar(root, "--text",    FALLBACK.text),
    text2:   readVar(root, "--text2",   FALLBACK.text2),
    accent:  readVar(root, "--accent",  FALLBACK.accent),
    accent2: readVar(root, "--accent2", FALLBACK.accent2),
    danger:  readVar(root, "--danger",  FALLBACK.danger),
    warn:    readVar(root, "--warn",    FALLBACK.warn),
    pass:    "",
    fail:    "",
    alt:     "",
    axisGrid: "",
  };
  t.pass     = t.accent;
  t.fail     = t.danger;
  t.alt      = t.warn;
  t.axisGrid = hexToRgba(t.text2, 0.18);
  return t;
}

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  if (h.length !== 6 && h.length !== 3) return `rgba(139,148,158,${alpha})`;
  const r = parseInt(h.length === 3 ? h[0] + h[0] : h.slice(0, 2), 16);
  const g = parseInt(h.length === 3 ? h[1] + h[1] : h.slice(2, 4), 16);
  const b = parseInt(h.length === 3 ? h[2] + h[2] : h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export function baseLayout(t: ChartTheme, title?: string): Partial<Layout> {
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor:  "rgba(0,0,0,0)",
    font:          { color: t.text, family: "inherit", size: 12 },
    margin:        { t: title ? 44 : 20, b: 44, l: 56, r: 24 },
    title:         title
      ? { text: title, font: { color: t.text, family: "inherit", size: 14 } }
      : undefined,
    xaxis: {
      gridcolor:    t.axisGrid,
      zerolinecolor: t.border,
      linecolor:    t.border,
      tickfont:     { color: t.text2 },
      title:        { font: { color: t.text2, size: 12 } },
    },
    yaxis: {
      gridcolor:    t.axisGrid,
      zerolinecolor: t.border,
      linecolor:    t.border,
      tickfont:     { color: t.text2 },
      title:        { font: { color: t.text2, size: 12 } },
    },
    legend: {
      bgcolor: "rgba(0,0,0,0)",
      font:    { color: t.text2, size: 11 },
    },
    autosize: true,
  };
}

export const PLOT_CONFIG: Partial<Config> = {
  responsive: true,
  displaylogo: false,
  displayModeBar: "hover",
  modeBarButtonsToRemove: [
    "lasso2d", "select2d", "autoScale2d", "toggleSpikelines",
  ] as ModeBarDefaultButtons[],
};

// Percentile-band colour scheme used by all equity-fan charts.
export function bandColors(t: ChartTheme) {
  return {
    p5:  t.fail,
    p25: t.alt,
    p50: t.accent2,
    p75: t.alt,
    p95: t.pass,
    fillOuter: hexToRgba(t.accent2, 0.10),
    fillInner: hexToRgba(t.accent2, 0.20),
    sample:    hexToRgba(t.accent2, 0.18),
  };
}

// Helper: percentile across a 2-D array of equity curves at column `day`.
export function percentile(arr: number[], pct: number): number {
  if (arr.length === 0) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const idx = Math.max(0, Math.min(sorted.length - 1, Math.floor((pct / 100) * (sorted.length - 1))));
  return sorted[idx];
}

export function percentilesPerDay(curves: number[][], pcts: number[]): Record<number, number[]> {
  const nDays = curves[0]?.length ?? 0;
  const out: Record<number, number[]> = {};
  for (const p of pcts) out[p] = new Array(nDays).fill(0);
  for (let day = 0; day < nDays; day++) {
    const col = curves.map((c) => c[day]);
    for (const p of pcts) out[p][day] = percentile(col, p);
  }
  return out;
}
