/** Numeric and string formatting helpers. */

export function fmtPct(v: number, decimals = 1): string {
  return `${(v * 100).toFixed(decimals)}%`;
}

export function fmtNum(v: number, decimals = 2): string {
  return v.toFixed(decimals);
}

export function fmtInt(v: number): string {
  return Math.round(v).toLocaleString();
}

/** Camel/snake → Title Case */
export function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/([A-Z])/g, " $1")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\w/, (c) => c.toUpperCase());
}

/** Render any scalar value as a display string. */
export function renderValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return fmtInt(v);
    return fmtNum(v, 4);
  }
  return String(v);
}
