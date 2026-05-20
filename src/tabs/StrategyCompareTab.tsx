/**
 * Strategy Compare tab (v0.8.0).
 *
 * Left rail: library of saved strategies with checkboxes.
 * Right canvas: side-by-side columns for the currently checked entries.
 * Each column shows: header, performance metrics, entry-condition indicators,
 * and an MT5 Strategy Tester report (.htm + .csv) drop slot.
 *
 * Diff mode highlights how the selected strategies differ:
 *   - indicators: unique to one column (amber), shared but different bounds (orange)
 *   - metrics: best-in-row (green)
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  attachToLibrary,
  deleteLibraryEntry,
  getDiscoveryTradesCsv,
  getMt5HtmlUrl,
  getMt5TradesCsv,
  listLibrary,
  type LibraryEntry,
} from "../api/library";
import type { PatternSummary } from "../api/discovery";
import Plot from "../components/charts/plotly";
import type { Data, Layout } from "plotly.js";
import IndicatorsTable, {
  type DiffTag,
  type IndicatorRule,
} from "../components/IndicatorsTable";
import {
  bestMatch,
  extractCsvColumn,
  extractCsvNumberColumn,
  pairKey,
  pairwise,
  ruleSimilarity,
  tradeSimilarity,
  type RuleSimResult,
  type TradeSimResult,
} from "../lib/similarity";

interface Mt5Stats {
  trades: number;
  gross_profit: number;
  gross_loss: number;
  max_dd: number;
}

const fmt = (n: number | null | undefined, digits = 2): string =>
  typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
const fmtInt = (n: number | null | undefined): string =>
  typeof n === "number" && isFinite(n) ? Math.round(n).toString() : "—";

/** Pull the genetic_rule out of an entry's metadata regardless of how it was
 *  serialized (it lives at metadata.genetic_rule for entries saved through the
 *  Discovery results window). */
function ruleOf(entry: LibraryEntry): IndicatorRule {
  const m = entry.metadata as PatternSummary | undefined;
  return m?.genetic_rule ?? {};
}

/** Build per-entry diff tags across the selected columns. */
function computeIndicatorDiff(entries: LibraryEntry[]): Map<string, Record<string, DiffTag>> {
  const result = new Map<string, Record<string, DiffTag>>();
  if (entries.length < 2) {
    for (const e of entries) result.set(e.pattern_id, {});
    return result;
  }
  // Indicator presence + bounds across all columns
  const presence = new Map<string, { count: number; bounds: Set<string> }>();
  for (const e of entries) {
    const rule = ruleOf(e);
    for (const [name, [lo, hi]] of Object.entries(rule)) {
      const slot = presence.get(name) ?? { count: 0, bounds: new Set<string>() };
      slot.count += 1;
      slot.bounds.add(`${lo}|${hi}`);
      presence.set(name, slot);
    }
  }
  for (const e of entries) {
    const tags: Record<string, DiffTag> = {};
    const rule = ruleOf(e);
    for (const name of Object.keys(rule)) {
      const slot = presence.get(name);
      if (!slot) continue;
      if (slot.count < entries.length) tags[name] = "unique";
      else if (slot.bounds.size > 1) tags[name] = "mismatch";
      else tags[name] = "shared";
    }
    result.set(e.pattern_id, tags);
  }
  return result;
}

/** Best column per numeric metric, given selected entries. */
function computeMetricBest(entries: LibraryEntry[]): Record<string, string> {
  if (entries.length < 2) return {};
  const metrics: { key: keyof PatternSummary; higherIsBetter: boolean }[] = [
    { key: "test_score",   higherIsBetter: true  },
    { key: "test_wr",      higherIsBetter: true  },
    { key: "test_pf",      higherIsBetter: true  },
    { key: "test_trades",  higherIsBetter: true  },
    { key: "consistency",  higherIsBetter: true  },
    { key: "implied_rr",   higherIsBetter: true  },
    { key: "sl_pct",       higherIsBetter: false },
    { key: "tp_pct",       higherIsBetter: true  },
    { key: "train_wr",     higherIsBetter: true  },
    { key: "train_pf",     higherIsBetter: true  },
  ];
  const winners: Record<string, string> = {};
  for (const m of metrics) {
    let bestVal: number | null = null;
    let bestId: string | null = null;
    for (const e of entries) {
      const meta = e.metadata as PatternSummary;
      const v = meta?.[m.key];
      if (typeof v !== "number" || !isFinite(v)) continue;
      if (bestVal == null
          || (m.higherIsBetter && v > bestVal)
          || (!m.higherIsBetter && v < bestVal)) {
        bestVal = v;
        bestId = e.pattern_id;
      }
    }
    if (bestId) winners[`${bestId}|${m.key as string}`] = "best";
  }
  return winners;
}

/** Parse a tiny subset of an MT5 trades CSV: rolling P/L from each row, then
 *  derive trade count, gross profit/loss, max drawdown in account currency.
 *  Tolerates a few common formats — drops rows that don't parse. */
function summarizeMt5Csv(text: string): Mt5Stats {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { trades: 0, gross_profit: 0, gross_loss: 0, max_dd: 0 };
  // Detect header
  const header = lines[0].split(/[\t,;]/).map((c) => c.trim().toLowerCase());
  const profitIdx = header.findIndex((h) =>
    h === "profit" || h === "p/l" || h === "pl" || h === "net" || h === "result",
  );
  const dataLines = profitIdx >= 0 ? lines.slice(1) : lines;
  const idx = profitIdx >= 0 ? profitIdx : -1;
  let trades = 0;
  let gp = 0;
  let gl = 0;
  let running = 0;
  let peak = 0;
  let maxDd = 0;
  for (const ln of dataLines) {
    const cols = ln.split(/[\t,;]/);
    const raw = idx >= 0 ? cols[idx] : cols[cols.length - 1];
    if (!raw) continue;
    const v = parseFloat(raw.replace(/[ ,$]/g, "").replace(",", "."));
    if (!isFinite(v)) continue;
    trades += 1;
    if (v > 0) gp += v; else gl += v;
    running += v;
    if (running > peak) peak = running;
    const dd = peak - running;
    if (dd > maxDd) maxDd = dd;
  }
  return { trades, gross_profit: gp, gross_loss: gl, max_dd: maxDd };
}

/** Threshold above which two strategies are flagged as "likely duplicates". */
const DUPLICATE_THRESHOLD = 0.7;
/** Threshold below which an entry counts as "unique" in the rail badge. */
const UNIQUE_THRESHOLD = 0.3;

export default function StrategyCompareTab() {
  const [library, setLibrary] = useState<LibraryEntry[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [diffMode, setDiffMode] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [busy, setBusy] = useState<string | null>(null);
  // Lazy CSV caches. discoveryTimes is parsed entry_time arrays keyed by
  // pattern_id, fetched on first need for trade-similarity. mt5CsvText is
  // raw CSV text per pattern, fetched when the entry is selected so the
  // equity overlay can render.
  const [discoveryTimes, setDiscoveryTimes] = useState<Map<string, string[]>>(new Map());
  const [mt5CsvText, setMt5CsvText] = useState<Map<string, string>>(new Map());
  const inFlight = useRef<Set<string>>(new Set());

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listLibrary();
      setLibrary(list);
      // Prune selection to entries that still exist.
      setSelected((prev) => {
        const next = new Set<string>();
        for (const id of prev) if (list.some((e) => e.pattern_id === id)) next.add(id);
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { reload().catch(() => {}); }, []);

  // Lazy-fetch the discovery trades CSV for selected entries that have one
  // recorded. Result feeds trade-similarity computation. Fetches at most once
  // per pattern_id per session; `inFlight` dedupes concurrent fetches.
  useEffect(() => {
    const need: string[] = [];
    for (const e of library) {
      if (!selected.has(e.pattern_id)) continue;
      if (!e.csv_path) continue;
      if (discoveryTimes.has(e.pattern_id)) continue;
      if (inFlight.current.has(e.pattern_id)) continue;
      need.push(e.pattern_id);
    }
    if (need.length === 0) return;
    for (const id of need) inFlight.current.add(id);
    (async () => {
      for (const id of need) {
        try {
          const text = await getDiscoveryTradesCsv(id);
          if (text != null) {
            const times = extractCsvColumn(text, "entry_time");
            setDiscoveryTimes((prev) => {
              const next = new Map(prev);
              next.set(id, times);
              return next;
            });
          }
        } catch { /* swallow per-entry — surface via missing data, not a banner */ }
        finally { inFlight.current.delete(id); }
      }
    })();
  }, [library, selected, discoveryTimes]);

  // Lazy-fetch the MT5 CSV text for selected entries. Drives the equity
  // overlay chart and the per-column CSV summary (post-refresh).
  useEffect(() => {
    const need: string[] = [];
    for (const e of library) {
      if (!selected.has(e.pattern_id)) continue;
      if (!e.mt5_csv_path) continue;
      if (mt5CsvText.has(e.pattern_id)) continue;
      const key = `mt5:${e.pattern_id}`;
      if (inFlight.current.has(key)) continue;
      need.push(e.pattern_id);
    }
    if (need.length === 0) return;
    for (const id of need) inFlight.current.add(`mt5:${id}`);
    (async () => {
      for (const id of need) {
        try {
          const text = await getMt5TradesCsv(id);
          if (text != null) {
            setMt5CsvText((prev) => {
              const next = new Map(prev);
              next.set(id, text);
              return next;
            });
          }
        } catch { /* same — non-fatal */ }
        finally { inFlight.current.delete(`mt5:${id}`); }
      }
    })();
  }, [library, selected, mt5CsvText]);

  // Pairwise rule similarity across the WHOLE library — drives the rail
  // uniqueness badges. Cheap (just walks metadata.genetic_rule).
  const libraryRuleSim = useMemo<Map<string, RuleSimResult>>(() => {
    const ids = library.map((e) => e.pattern_id);
    return pairwise(ids, (a, b) => {
      const ea = library.find((e) => e.pattern_id === a);
      const eb = library.find((e) => e.pattern_id === b);
      return ruleSimilarity(ruleOf(ea!), ruleOf(eb!));
    });
  }, [library]);

  const selectedIds = useMemo(() => [...selected], [selected]);

  // Rule similarity for just the selected pairs — derived from libraryRuleSim
  // for the canvas heatmap.
  const selectedRuleSim = useMemo<Map<string, RuleSimResult>>(() => {
    const out = new Map<string, RuleSimResult>();
    for (let i = 0; i < selectedIds.length; i += 1) {
      for (let j = i + 1; j < selectedIds.length; j += 1) {
        const k = pairKey(selectedIds[i], selectedIds[j]);
        const v = libraryRuleSim.get(k);
        if (v) out.set(k, v);
      }
    }
    return out;
  }, [selectedIds, libraryRuleSim]);

  // Trade similarity for selected pairs — needs the lazy CSV cache. Pairs
  // missing CSV data are omitted (the heatmap renders "—" for them).
  const selectedTradeSim = useMemo<Map<string, TradeSimResult>>(() => {
    return pairwise(selectedIds, (a, b) => {
      const ta = discoveryTimes.get(a);
      const tb = discoveryTimes.get(b);
      if (!ta || !tb) {
        return { score: -1, shared: 0, union: 0, tradesA: 0, tradesB: 0 } as TradeSimResult;
      }
      return tradeSimilarity(ta, tb);
    });
  }, [selectedIds, discoveryTimes]);

  const toggleSelect = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  const selectAll = () => setSelected(new Set(library.map((e) => e.pattern_id)));
  const invertSelection = () =>
    setSelected((prev) => {
      const next = new Set<string>();
      for (const e of library) if (!prev.has(e.pattern_id)) next.add(e.pattern_id);
      return next;
    });

  const handleDelete = async (id: string) => {
    setBusy(id);
    try {
      await deleteLibraryEntry(id);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const handleAttach = async (id: string, kind: "mt5_html" | "mt5_csv", file: File) => {
    setBusy(id);
    try {
      await attachToLibrary(id, kind, file);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const selectedEntries = useMemo(
    () => library.filter((e) => selected.has(e.pattern_id)),
    [library, selected],
  );

  const indicatorDiff = useMemo(
    () => (diffMode ? computeIndicatorDiff(selectedEntries) : new Map()),
    [diffMode, selectedEntries],
  );

  const metricBest = useMemo(
    () => (diffMode ? computeMetricBest(selectedEntries) : {}),
    [diffMode, selectedEntries],
  );

  return (
    <div className="strategy-compare-root">
      <div className="tab-header" style={{ marginBottom: 12 }}>
        <h2>Strategy Compare</h2>
        <p className="tab-subtitle">
          Pick saved strategies in the left rail to compare them side-by-side.
          Diff mode highlights how their entry conditions and metrics differ.
        </p>
      </div>

      <div className="compare-toolbar">
        <label className="toggle-label">
          <span className="toggle-wrap">
            <input
              type="checkbox"
              className="toggle-input"
              checked={diffMode}
              onChange={(e) => setDiffMode(e.target.checked)}
            />
            <span className="toggle-track" />
          </span>
          Diff mode
        </label>
        <button className="btn btn-secondary btn-mini" onClick={() => reload().catch(() => {})}>
          ↻ Refresh
        </button>
        {library.length > 0 && (
          <>
            <button
              className="btn btn-secondary btn-mini"
              onClick={selectAll}
              disabled={selected.size === library.length}
            >
              Select all ({library.length})
            </button>
            <button
              className="btn btn-secondary btn-mini"
              onClick={invertSelection}
              disabled={library.length === 0}
              title="Invert: select what isn't selected"
            >
              Invert
            </button>
          </>
        )}
        {selected.size > 0 && (
          <button className="btn btn-secondary btn-mini" onClick={() => setSelected(new Set())}>
            Clear selection ({selected.size})
          </button>
        )}
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 8 }}>{error}</div>}

      <div className="compare-layout">
        <aside className="compare-rail">
          <div className="compare-rail-title">
            Library ({library.length})
          </div>
          {loading ? (
            <p className="results-loading">Loading…</p>
          ) : library.length === 0 ? (
            <div className="compare-empty-rail">
              <p>No saved strategies yet.</p>
              <p className="field-hint">
                Run a Discovery, then click <strong>⭐ Save</strong> on any pattern row
                to add it here.
              </p>
            </div>
          ) : (
            <ul className="compare-rail-list">
              {library.map((e) => {
                const meta = e.metadata as PatternSummary;
                const checked = selected.has(e.pattern_id);
                const sims = new Map<string, number>();
                for (const [k, v] of libraryRuleSim) sims.set(k, v.score);
                const otherIds = library
                  .filter((o) => o.pattern_id !== e.pattern_id)
                  .map((o) => o.pattern_id);
                const best = bestMatch(e.pattern_id, otherIds, sims);
                let badgeLabel: string | null = null;
                let badgeClass = "";
                if (best != null) {
                  if (best.score >= DUPLICATE_THRESHOLD) {
                    badgeLabel = `Duplicate ${Math.round(best.score * 100)}%`;
                    badgeClass = "uniq-badge-dup";
                  } else if (best.score < UNIQUE_THRESHOLD) {
                    badgeLabel = "Unique";
                    badgeClass = "uniq-badge-uniq";
                  } else {
                    badgeLabel = `Similar ${Math.round(best.score * 100)}%`;
                    badgeClass = "uniq-badge-sim";
                  }
                }
                return (
                  <li key={e.pattern_id} className={checked ? "selected" : ""}>
                    <label className="compare-rail-row">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSelect(e.pattern_id)}
                      />
                      <span className="compare-rail-id" title={e.pattern_id}>
                        {e.pattern_id}
                      </span>
                      {meta?.direction && (
                        <span className={`dir-badge dir-${meta.direction.toLowerCase()}`}>
                          {meta.direction}
                        </span>
                      )}
                    </label>
                    <div className="compare-rail-meta">
                      <span className="field-hint">
                        {e.saved_at ? new Date(e.saved_at).toLocaleDateString() : "—"}
                        {typeof meta?.test_wr === "number" && (
                          <> · WR {fmt(meta.test_wr, 1)}%</>
                        )}
                        {typeof meta?.test_pf === "number" && (
                          <> · PF {fmt(meta.test_pf)}</>
                        )}
                      </span>
                      <button
                        className="link-btn"
                        onClick={() => handleDelete(e.pattern_id)}
                        disabled={busy === e.pattern_id}
                        title="Remove from library"
                      >
                        Remove
                      </button>
                    </div>
                    {badgeLabel && (
                      <div className="compare-rail-badge-row">
                        <span
                          className={`uniq-badge ${badgeClass}`}
                          title={best ? `Closest match (rule similarity): ${best.other}` : ""}
                        >
                          {badgeLabel}
                        </span>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section className="compare-canvas">
          {selectedEntries.length === 0 ? (
            <div className="compare-canvas-empty">
              <h3>Select strategies to compare</h3>
              <p className="field-hint">
                Check two or more entries in the library to see their entry
                conditions and metrics side-by-side. Attach an MT5 Strategy
                Tester .htm report per column to compare backtests too.
              </p>
            </div>
          ) : (
            <>
              {selectedEntries.length >= 2 && (
                <>
                  <EquityOverlay
                    selectedEntries={selectedEntries}
                    mt5CsvText={mt5CsvText}
                  />
                  <SimilarityHeatmap
                    selectedEntries={selectedEntries}
                    ruleSim={selectedRuleSim}
                    tradeSim={selectedTradeSim}
                  />
                  <DuplicateAlerts
                    selectedEntries={selectedEntries}
                    ruleSim={selectedRuleSim}
                    tradeSim={selectedTradeSim}
                  />
                </>
              )}
              <div className="compare-columns">
                {selectedEntries.map((entry) => (
                  <CompareColumn
                    key={entry.pattern_id}
                    entry={entry}
                    indicatorDiff={indicatorDiff.get(entry.pattern_id) ?? {}}
                    metricBest={metricBest}
                    busy={busy === entry.pattern_id}
                    onAttach={(kind, file) => handleAttach(entry.pattern_id, kind, file)}
                  />
                ))}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

// ─── One column per selected strategy ────────────────────────────────────────

interface CompareColumnProps {
  entry: LibraryEntry;
  indicatorDiff: Record<string, DiffTag>;
  metricBest: Record<string, string>;
  busy: boolean;
  onAttach: (kind: "mt5_html" | "mt5_csv", file: File) => void;
}

function CompareColumn({ entry, indicatorDiff, metricBest, busy, onAttach }: CompareColumnProps) {
  const meta = entry.metadata as PatternSummary;
  const rule = ruleOf(entry);
  const [htmlUrl, setHtmlUrl] = useState<string | null>(null);
  const [showReport, setShowReport] = useState<boolean>(false);
  const [csvStats, setCsvStats] = useState<Mt5Stats | null>(null);
  const [csvError, setCsvError] = useState<string | null>(null);

  // Resolve the MT5 HTML URL when an attachment is present.
  useEffect(() => {
    let cancelled = false;
    if (entry.mt5_html_path) {
      getMt5HtmlUrl(entry.pattern_id).then((url) => {
        if (!cancelled) setHtmlUrl(url);
      }).catch(() => { if (!cancelled) setHtmlUrl(null); });
    } else {
      setHtmlUrl(null);
      setShowReport(false);
    }
    return () => { cancelled = true; };
  }, [entry.pattern_id, entry.mt5_html_path]);

  const onCsvAttach = async (file: File) => {
    try {
      const text = await file.text();
      setCsvStats(summarizeMt5Csv(text));
      setCsvError(null);
    } catch (e) {
      setCsvError(e instanceof Error ? e.message : String(e));
    }
    onAttach("mt5_csv", file);
  };

  const metricRow = (label: string, key: keyof PatternSummary, suffix = "", digits = 2) => {
    const v = meta?.[key];
    const isBest = metricBest[`${entry.pattern_id}|${key as string}`] === "best";
    return (
      <tr style={isBest ? { background: "rgba(46, 213, 115, 0.18)" } : undefined}>
        <td className="kv-key">{label}</td>
        <td className="num">{typeof v === "number" ? `${fmt(v, digits)}${suffix}` : "—"}</td>
      </tr>
    );
  };

  return (
    <div className="compare-column">
      <header className="compare-column-header">
        <div className="compare-column-id" title={entry.pattern_id}>
          {entry.pattern_id}
        </div>
        <div className="compare-column-subhead">
          {meta?.direction && (
            <span className={`dir-badge dir-${meta.direction.toLowerCase()}`}>
              {meta.direction}
            </span>
          )}
          <span className="field-hint">
            cluster {meta?.cluster ?? "—"} · seed {meta?.seed ?? "—"}
          </span>
        </div>
        <div className="field-hint" style={{ marginTop: 4 }}>
          Saved {entry.saved_at ? new Date(entry.saved_at).toLocaleString() : "—"}
        </div>
      </header>

      <section className="compare-section">
        <h4>Metrics</h4>
        <table className="compare-metrics">
          <tbody>
            {metricRow("Test WR", "test_wr", "%", 1)}
            {metricRow("Test PF", "test_pf")}
            {metricRow("Test trades", "test_trades", "", 0)}
            {metricRow("Consistency", "consistency")}
            {metricRow("Implied R:R", "implied_rr")}
            {metricRow("SL", "sl_pct", "", 4)}
            {metricRow("TP", "tp_pct", "", 4)}
            {metricRow("Train WR", "train_wr", "%", 1)}
            {metricRow("Train PF", "train_pf")}
          </tbody>
        </table>
      </section>

      <section className="compare-section">
        <h4>Entry conditions ({Object.keys(rule).length})</h4>
        <IndicatorsTable rule={rule} diff={indicatorDiff} compact />
      </section>

      <section className="compare-section">
        <h4>MT5 Backtest</h4>
        {entry.mt5_html_path ? (
          <div className="mt5-attached">
            <div className="field-hint" style={{ marginBottom: 6 }}>
              Report attached.
            </div>
            <button
              className="btn-mini"
              onClick={() => setShowReport((s) => !s)}
            >
              {showReport ? "Hide report" : "View report"}
            </button>
            <ContentDropZone
              label="Replace MT5 .htm"
              accept=".htm,.html"
              compact
              onFile={(f) => onAttach("mt5_html", f)}
              disabled={busy}
            />
            {showReport && htmlUrl && (
              <iframe
                title={`MT5 report — ${entry.pattern_id}`}
                src={htmlUrl}
                style={{
                  width: "100%",
                  height: 480,
                  marginTop: 8,
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  background: "white",
                }}
                sandbox="allow-same-origin"
              />
            )}
          </div>
        ) : (
          <ContentDropZone
            label="Drop MT5 Strategy Tester .htm report"
            accept=".htm,.html"
            onFile={(f) => onAttach("mt5_html", f)}
            disabled={busy}
          />
        )}
        <div style={{ marginTop: 10 }}>
          {entry.mt5_csv_path ? (
            <>
              <div className="field-hint" style={{ marginBottom: 6 }}>
                Trades CSV attached.
              </div>
              {csvStats && (
                <table className="compare-metrics">
                  <tbody>
                    <tr><td className="kv-key">Trades</td><td className="num">{fmtInt(csvStats.trades)}</td></tr>
                    <tr><td className="kv-key">Gross profit</td><td className="num">{fmt(csvStats.gross_profit)}</td></tr>
                    <tr><td className="kv-key">Gross loss</td><td className="num">{fmt(csvStats.gross_loss)}</td></tr>
                    <tr><td className="kv-key">Max DD</td><td className="num">{fmt(csvStats.max_dd)}</td></tr>
                  </tbody>
                </table>
              )}
              {csvError && <div className="alert alert-warn">CSV: {csvError}</div>}
              <ContentDropZone
                label="Replace trades .csv"
                accept=".csv"
                compact
                onFile={onCsvAttach}
                disabled={busy}
              />
            </>
          ) : (
            <ContentDropZone
              label="Drop MT5 trades .csv"
              accept=".csv"
              onFile={onCsvAttach}
              disabled={busy}
            />
          )}
        </div>
      </section>
    </div>
  );
}

// ─── Inline content dropzone (binary File rather than path) ──────────────────

interface ContentDropZoneProps {
  label: string;
  accept: string;
  onFile: (file: File) => void;
  disabled?: boolean;
  compact?: boolean;
}

function ContentDropZone({ label, accept, onFile, disabled, compact }: ContentDropZoneProps) {
  const [hover, setHover] = useState(false);
  return (
    <label
      className={`content-dropzone${hover ? " content-dropzone-hover" : ""}${compact ? " content-dropzone-compact" : ""}${disabled ? " content-dropzone-disabled" : ""}`}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setHover(true); }}
      onDragLeave={() => setHover(false)}
      onDrop={(e) => {
        e.preventDefault();
        setHover(false);
        if (disabled) return;
        const f = e.dataTransfer.files[0];
        if (f) onFile(f);
      }}
    >
      <input
        type="file"
        accept={accept}
        disabled={disabled}
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          // Reset so re-picking the same file fires onChange again.
          e.target.value = "";
        }}
      />
      <span className="content-dropzone-icon">⤓</span>
      <span>{label}</span>
      <span className="field-hint">click or drop {accept}</span>
    </label>
  );
}

// ─── Pairwise similarity heatmap ────────────────────────────────────────────

interface HeatmapProps {
  selectedEntries: LibraryEntry[];
  ruleSim: Map<string, RuleSimResult>;
  tradeSim: Map<string, TradeSimResult>;
}

/** Colour a 0..1 score on a green→amber→red gradient. score<0 means "no data". */
function simBg(score: number): string {
  if (score < 0) return "transparent";
  // 0 → green-ish (low overlap = good diversity), 1 → red (duplicate)
  const r = Math.round(40 + score * 215);
  const g = Math.round(215 - score * 175);
  return `rgba(${r}, ${g}, 60, 0.22)`;
}

function shortId(id: string): string {
  return id.length > 14 ? id.slice(0, 12) + "…" : id;
}

function SimilarityHeatmap({ selectedEntries, ruleSim, tradeSim }: HeatmapProps) {
  const ids = selectedEntries.map((e) => e.pattern_id);
  return (
    <div className="compare-overview-block">
      <div className="compare-overview-header">
        <h4>Pairwise similarity</h4>
        <span className="field-hint">
          Top half: rule similarity (indicators + bound overlap). Bottom: trade overlap (Jaccard on entry timestamps).
        </span>
      </div>
      <div className="heatmap-scroll">
        <table className="heatmap">
          <thead>
            <tr>
              <th></th>
              {ids.map((id) => (
                <th key={id} title={id}>{shortId(id)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ids.map((rowId, i) => (
              <tr key={rowId}>
                <th title={rowId}>{shortId(rowId)}</th>
                {ids.map((colId, j) => {
                  if (i === j) {
                    return <td key={colId} className="heatmap-diag">—</td>;
                  }
                  // Upper triangle (i<j): rule similarity
                  // Lower triangle (i>j): trade similarity
                  const isUpper = i < j;
                  const k = pairKey(rowId, colId);
                  if (isUpper) {
                    const r = ruleSim.get(k);
                    if (!r) return <td key={colId}></td>;
                    return (
                      <td
                        key={colId}
                        style={{ background: simBg(r.score) }}
                        title={`Rule: ${(r.score * 100).toFixed(0)}% — ${r.shared}/${r.unionSize} shared indicators · avg bound IoU ${(r.avgIou * 100).toFixed(0)}%`}
                      >
                        {(r.score * 100).toFixed(0)}%
                      </td>
                    );
                  }
                  const t = tradeSim.get(k);
                  if (!t || t.score < 0) {
                    return (
                      <td key={colId} className="heatmap-loading" title="Loading trades CSV…">
                        …
                      </td>
                    );
                  }
                  return (
                    <td
                      key={colId}
                      style={{ background: simBg(t.score) }}
                      title={`Trade overlap: ${(t.score * 100).toFixed(0)}% — ${t.shared} shared / ${t.union} union (A:${t.tradesA}, B:${t.tradesB})`}
                    >
                      {(t.score * 100).toFixed(0)}%
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Duplicate-alert callouts ────────────────────────────────────────────────

interface AlertsProps {
  selectedEntries: LibraryEntry[];
  ruleSim: Map<string, RuleSimResult>;
  tradeSim: Map<string, TradeSimResult>;
}

function DuplicateAlerts({ selectedEntries, ruleSim, tradeSim }: AlertsProps) {
  const ids = selectedEntries.map((e) => e.pattern_id);
  const alerts: { pair: [string, string]; rule: number; trade: number | null; msg: string }[] = [];
  for (let i = 0; i < ids.length; i += 1) {
    for (let j = i + 1; j < ids.length; j += 1) {
      const k = pairKey(ids[i], ids[j]);
      const r = ruleSim.get(k);
      const t = tradeSim.get(k);
      const ruleScore = r?.score ?? 0;
      const tradeScore = t && t.score >= 0 ? t.score : null;
      // Surface when EITHER signal is in duplicate territory.
      const flagTrade = tradeScore != null && tradeScore >= DUPLICATE_THRESHOLD;
      const flagRule = ruleScore >= DUPLICATE_THRESHOLD;
      if (!flagRule && !flagTrade) continue;
      let msg: string;
      if (flagTrade && flagRule) {
        msg = `share ${Math.round(tradeScore! * 100)}% of trades AND ${Math.round(ruleScore * 100)}% rule similarity — almost certainly redundant`;
      } else if (flagTrade) {
        msg = `share ${Math.round(tradeScore! * 100)}% of trades despite different rules — likely the same edge`;
      } else {
        msg = `share ${Math.round(ruleScore * 100)}% rule similarity — likely close variants`;
      }
      alerts.push({ pair: [ids[i], ids[j]], rule: ruleScore, trade: tradeScore, msg });
    }
  }
  if (alerts.length === 0) {
    return (
      <div className="compare-overview-block">
        <div className="alert alert-success" style={{ margin: 0 }}>
          ✓ No duplicate pairs detected above {Math.round(DUPLICATE_THRESHOLD * 100)}% threshold.
        </div>
      </div>
    );
  }
  return (
    <div className="compare-overview-block">
      {alerts.map((a) => (
        <div key={a.pair.join("|")} className="alert alert-warn duplicate-alert">
          ⚠ <strong>{shortId(a.pair[0])}</strong> and <strong>{shortId(a.pair[1])}</strong> {a.msg}.
        </div>
      ))}
    </div>
  );
}

// ─── MT5 equity-curve overlay ───────────────────────────────────────────────

interface EquityOverlayProps {
  selectedEntries: LibraryEntry[];
  mt5CsvText: Map<string, string>;
}

interface EquitySeries {
  patternId: string;
  trades: number[];
  cumPL: number[];
}

/** Parse MT5 trades CSV into a cumulative P/L curve indexed by trade number.
 *  Plots by trade index (not timestamp) so backtests with different lengths
 *  remain comparable on a single axis. */
function parseMt5Equity(text: string): EquitySeries {
  // Reuse the lenient extractor; the column header in MT5 reports is usually
  // "Profit" but can vary. Try a few common names.
  const candidates = ["Profit", "profit", "P/L", "p/l", "PL", "pl", "Net", "net", "Result", "result"];
  let pl: number[] = [];
  for (const name of candidates) {
    const found = extractCsvNumberColumn(text, name);
    if (found.length > 0) { pl = found; break; }
  }
  if (pl.length === 0) return { patternId: "", trades: [], cumPL: [] };
  const trades = pl.map((_, i) => i + 1);
  let acc = 0;
  const cumPL = pl.map((v) => (acc += v));
  return { patternId: "", trades, cumPL };
}

function EquityOverlay({ selectedEntries, mt5CsvText }: EquityOverlayProps) {
  const series: EquitySeries[] = useMemo(() => {
    const out: EquitySeries[] = [];
    for (const e of selectedEntries) {
      if (!e.mt5_csv_path) continue;
      const text = mt5CsvText.get(e.pattern_id);
      if (!text) continue;
      const parsed = parseMt5Equity(text);
      if (parsed.trades.length === 0) continue;
      out.push({ ...parsed, patternId: e.pattern_id });
    }
    return out;
  }, [selectedEntries, mt5CsvText]);

  const withMt5Count = selectedEntries.filter((e) => e.mt5_csv_path).length;
  if (withMt5Count < 2) return null;

  const data: Data[] = series.map((s) => ({
    type: "scatter",
    mode: "lines",
    name: shortId(s.patternId),
    x: s.trades,
    y: s.cumPL,
    hovertemplate: "Trade %{x}<br>Cum P/L: %{y:.2f}<extra>%{fullData.name}</extra>",
  }));
  const layout: Partial<Layout> = {
    height: 320,
    margin: { l: 60, r: 20, t: 30, b: 50 },
    title: { text: "MT5 backtest — cumulative P/L by trade index", font: { size: 13 } },
    xaxis: { title: { text: "Trade #" } },
    yaxis: { title: { text: "Cumulative P/L" }, zeroline: true },
    legend: { orientation: "h", y: -0.2 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "var(--text)" } as Partial<Layout["font"]>,
  };
  return (
    <div className="compare-overview-block">
      <div className="compare-overview-header">
        <h4>MT5 equity overlay</h4>
        <span className="field-hint">
          {series.length} of {withMt5Count} attached backtest{withMt5Count === 1 ? "" : "s"} parsed
          {series.length < withMt5Count && " — others still loading or have unrecognized columns"}
        </span>
      </div>
      <Plot
        data={data}
        layout={layout}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%" }}
      />
    </div>
  );
}
