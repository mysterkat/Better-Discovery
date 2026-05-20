/**
 * Pairwise strategy-similarity utilities for the Strategy Compare tab.
 *
 * Two independent metrics, both 0..1:
 *
 *   ruleSimilarity   — Jaccard on indicator names × IoU on shared bounds.
 *                      Cheap (uses genetic_rule from PatternSummary metadata).
 *                      Answers "do these strategies use the same features?"
 *
 *   tradeSimilarity  — Jaccard on trade entry timestamps.
 *                      Needs the discovery trades.csv from the backend.
 *                      Answers "do these strategies actually fire on the
 *                      same bars?" — the gold standard, since two very
 *                      different rules can converge on the same trades.
 */

import type { IndicatorRule } from "../components/IndicatorsTable";

// ─── Rule similarity ─────────────────────────────────────────────────────────

export interface RuleSimResult {
  /** Combined 0..1 score, used for headline display. */
  score: number;
  /** Jaccard on indicator names. */
  jaccard: number;
  /** Average IoU across indicators present in both rules; 0 if none shared. */
  avgIou: number;
  /** Count of indicators each rule has + how many overlap. */
  shared: number;
  unionSize: number;
}

export function ruleSimilarity(a: IndicatorRule, b: IndicatorRule): RuleSimResult {
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  const setA = new Set(keysA);
  const setB = new Set(keysB);
  const union = new Set([...keysA, ...keysB]);
  const sharedKeys = keysA.filter((k) => setB.has(k));
  const jaccard = union.size === 0 ? 1 : sharedKeys.length / union.size;

  if (sharedKeys.length === 0) {
    return { score: 0, jaccard, avgIou: 0, shared: 0, unionSize: union.size };
  }

  let iouSum = 0;
  for (const k of sharedKeys) {
    const [aLo, aHi] = a[k];
    const [bLo, bHi] = b[k];
    const interLo = Math.max(aLo, bLo);
    const interHi = Math.min(aHi, bHi);
    const inter = Math.max(0, interHi - interLo);
    const u = Math.max(aHi, bHi) - Math.min(aLo, bLo);
    iouSum += u > 0 ? inter / u : 1;
  }
  const avgIou = iouSum / sharedKeys.length;
  // Multiplicative penalty: same indicators × overlapping bounds. Floored so
  // that "100% same indicators, 0% bounds" still reads as related (0.5)
  // rather than 0 — the user still wants to see them paired up.
  const score = jaccard * (0.5 + 0.5 * avgIou);
  // Silence unused warning on setA — kept for symmetry with the algorithm.
  void setA;
  return { score, jaccard, avgIou, shared: sharedKeys.length, unionSize: union.size };
}

// ─── Trade-CSV parsing ───────────────────────────────────────────────────────

/** Parse a single column out of a comma-separated header-row CSV. Tolerates
 *  the toolkit's output: dates like "2024-01-15 10:30:00", split tags,
 *  pnl_pts column, etc. Returns string values verbatim so callers can decide
 *  how to compare (we use string equality on timestamps for Jaccard). */
export function extractCsvColumn(text: string, columnName: string): string[] {
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length < 2) return [];
  const header = lines[0].split(",").map((h) => h.trim());
  const idx = header.indexOf(columnName);
  if (idx < 0) return [];
  const out: string[] = [];
  for (let i = 1; i < lines.length; i += 1) {
    const cols = lines[i].split(",");
    const v = cols[idx];
    if (v != null) out.push(v.trim());
  }
  return out;
}

/** Read a numeric column. Skips rows that don't parse. */
export function extractCsvNumberColumn(text: string, columnName: string): number[] {
  return extractCsvColumn(text, columnName)
    .map((s) => parseFloat(s))
    .filter((n) => Number.isFinite(n));
}

// ─── Trade similarity ────────────────────────────────────────────────────────

export interface TradeSimResult {
  /** Jaccard on entry timestamps; 0..1. */
  score: number;
  shared: number;
  union: number;
  tradesA: number;
  tradesB: number;
}

/** Set-based Jaccard. Timestamps are compared as strings (same bar → same
 *  string). If discovery emits timezone-aware vs naive strings, the user
 *  should never mix runs from different timezones in the library anyway. */
export function tradeSimilarity(timesA: string[], timesB: string[]): TradeSimResult {
  const setA = new Set(timesA);
  const setB = new Set(timesB);
  if (setA.size === 0 && setB.size === 0) {
    return { score: 1, shared: 0, union: 0, tradesA: 0, tradesB: 0 };
  }
  let shared = 0;
  for (const t of setA) if (setB.has(t)) shared += 1;
  const union = setA.size + setB.size - shared;
  const score = union === 0 ? 0 : shared / union;
  return { score, shared, union, tradesA: setA.size, tradesB: setB.size };
}

// ─── Pairwise matrix utility ─────────────────────────────────────────────────

export type Pair = readonly [string, string];

/** Compute pairwise similarity for an array of ids. The `compute` callback is
 *  only invoked for i<j (upper triangle); the returned map is keyed by the
 *  canonical "min|max" string so lookup works either way around. */
export function pairwise<T>(
  ids: readonly string[],
  compute: (a: string, b: string) => T,
): Map<string, T> {
  const out = new Map<string, T>();
  for (let i = 0; i < ids.length; i += 1) {
    for (let j = i + 1; j < ids.length; j += 1) {
      const a = ids[i];
      const b = ids[j];
      const key = a < b ? `${a}|${b}` : `${b}|${a}`;
      out.set(key, compute(a, b));
    }
  }
  return out;
}

export function pairKey(a: string, b: string): string {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

/** Max similarity from a given id to any OTHER id in the set. Used for the
 *  "Unique 92%" vs "Duplicate of X 78%" badge in the library rail. */
export function bestMatch(
  id: string,
  others: readonly string[],
  matrix: Map<string, number>,
): { other: string; score: number } | null {
  let bestId: string | null = null;
  let bestScore = -1;
  for (const o of others) {
    if (o === id) continue;
    const s = matrix.get(pairKey(id, o)) ?? 0;
    if (s > bestScore) { bestScore = s; bestId = o; }
  }
  return bestId == null ? null : { other: bestId, score: bestScore };
}
