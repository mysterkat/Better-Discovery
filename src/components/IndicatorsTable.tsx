/**
 * Renders the genetic_rule indicator bounds as a table.
 * Used in:
 *   - DiscoveryResults expanded-row "Indicators" section
 *   - StrategyCompareTab side-by-side columns (with diff coloring)
 */

import type { CSSProperties } from "react";

export type IndicatorRule = Record<string, [number, number]>;

/** Per-row classification used by the compare view for highlighting. */
export type DiffTag = "shared" | "unique" | "mismatch";

export interface IndicatorsTableProps {
  rule: IndicatorRule;
  /** Optional per-indicator diff tag map; controls row background color. */
  diff?: Record<string, DiffTag>;
  /** Compact mode: tighter padding for side-by-side columns. */
  compact?: boolean;
}

const fmt = (n: number, digits = 3): string =>
  typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";

const ROW_STYLES: Record<DiffTag, CSSProperties> = {
  shared:   { background: "transparent" },
  unique:   { background: "rgba(255, 215, 64, 0.18)" }, // amber
  mismatch: { background: "rgba(255, 138, 80, 0.20)" }, // orange
};

export default function IndicatorsTable({ rule, diff, compact }: IndicatorsTableProps) {
  const entries = Object.entries(rule);
  if (entries.length === 0) {
    return <div className="field-hint" style={{ fontStyle: "italic" }}>No indicators recorded.</div>;
  }
  return (
    <table className={`indicators-table${compact ? " indicators-table-compact" : ""}`}>
      <thead>
        <tr>
          <th style={{ width: 28 }} className="num">#</th>
          <th>Indicator</th>
          <th className="num">Min</th>
          <th className="num">Max</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([name, [lo, hi]], i) => {
          const tag = diff?.[name];
          const style = tag ? ROW_STYLES[tag] : undefined;
          return (
            <tr key={name} style={style}>
              <td className="num">{i + 1}</td>
              <td className="mono">{name}</td>
              <td className="num">{fmt(lo)}</td>
              <td className="num">{fmt(hi)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
