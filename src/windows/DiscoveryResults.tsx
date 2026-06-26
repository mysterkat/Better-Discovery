/**
 * Pattern Discovery results window.
 * Loaded when URL has ?window=discovery-results&jobId=…
 */

import { Fragment, useEffect, useState } from "react";
import { getDiscoveryResults, getSetFileContent, type DiscoveryOverview, type JobRef, type PatternSummary } from "../api/discovery";
import { saveToLibrary } from "../api/library";
import { openFolder } from "../api/system";
import IndicatorsTable from "../components/IndicatorsTable";
import { renderValue, titleCase } from "../lib/format";
import { useSettings } from "../state/settings";

interface HypothesisCandidate {
  strategy_id: string;
  strategy_fingerprint: string;
  lineage: string;
  hypothesis: string;
  parameters: Record<string, unknown>;
  trades: number;
  net_profit: number;
  profit_factor: number | null;
  max_drawdown_pct: number;
  challenge_score: number;
  challenge_pass_count: number;
  challenge_pass_rate: number;
  challenge_active_pass_rate: number;
  challenge_prop_fail_count: number;
  challenge_prop_fail_rate: number;
  median_days_to_target: number | null;
  best_days_to_target: number | null;
  risk_fraction: number;
  internal_daily_stop_pct: number;
  max_trades_per_day: number;
}

interface HypothesisDiscoveryResult {
  experiment_id: string;
  dataset_id: string;
  symbol: string;
  timeframe: string;
  variants_generated: number;
  variants_tested: number;
  artifact_folder: string;
  summary_csv: string;
  summary_json: string;
  top_candidates: HypothesisCandidate[];
}

function isHypothesisResult(value: unknown): value is HypothesisDiscoveryResult {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return Array.isArray(candidate.top_candidates) && typeof candidate.variants_generated === "number";
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  return typeof value === "number" && isFinite(value) ? value.toFixed(digits) : "-";
}

function formatPct(value: number | null | undefined, digits = 1): string {
  return typeof value === "number" && isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "-";
}

export default function DiscoveryResults() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("jobId") ?? "";

  const [job, setJob] = useState<JobRef | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSettings = useSettings((s) => s.load);
  useEffect(() => { loadSettings(); }, [loadSettings]);

  useEffect(() => {
    if (!jobId) { setError("No jobId in URL."); return; }
    let timer: ReturnType<typeof setInterval>;

    const poll = async () => {
      try {
        const result = await getDiscoveryResults(jobId);
        setJob(result);
        if (result.status === "done" || result.status === "failed") {
          clearInterval(timer);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        clearInterval(timer);
      }
    };

    poll();
    timer = setInterval(poll, 1500);
    return () => clearInterval(timer);
  }, [jobId]);

  const rawResult = job?.result;
  const hypothesisResult = isHypothesisResult(rawResult) ? rawResult : null;
  const result = rawResult && typeof rawResult === "object"
    ? rawResult as Record<string, unknown>
    : null;
  // Internal-only fields the user shouldn't see as cards or in the
  // collapsible "Details" sections.
  const HIDDEN_KEYS = new Set(["ok", "overrides_applied", "patterns", "overview"]);
  const scalars = result
    ? Object.entries(result).filter(([k, v]) => !HIDDEN_KEYS.has(k) && (typeof v !== "object" || v === null))
    : [];
  const nested = result
    ? Object.entries(result).filter(([k, v]) => !HIDDEN_KEYS.has(k) && typeof v === "object" && v !== null)
    : [];
  const patternsFound = result && typeof result.patterns_found === "number"
    ? (result.patterns_found as number)
    : null;
  const patterns = (result?.patterns as PatternSummary[] | undefined) ?? [];
  const overview = (result?.overview as DiscoveryOverview | undefined) ?? null;

  return (
    <div className="results-window">
      <div className="results-header">
        <h1>{hypothesisResult ? "FTMO Hypothesis Results" : "Pattern Discovery Results"}</h1>
        {jobId && <span className="job-id-badge">{jobId.slice(0, 8)}</span>}
      </div>

      {!job && !error && <p className="results-loading">Fetching results…</p>}
      {error && <div className="alert alert-error">{error}</div>}

      {(job?.status === "pending" || job?.status === "running") && (
        <p className="results-loading">Discovery running — this window will update automatically.</p>
      )}

      {job?.status === "failed" && (
        <div className="alert alert-error">Discovery failed: {job.error}</div>
      )}

      {job?.status === "done" && hypothesisResult && (
        <HypothesisResults result={hypothesisResult} />
      )}

      {job?.status === "done" && result && !hypothesisResult && (
        <>
          {/* Lead with the headline outcome so the user sees pass/fail before scanning cards. */}
          {patternsFound === 0 && (
            <div className="alert alert-warn" style={{ marginBottom: 16 }}>
              <strong>No patterns passed the quality filters.</strong>{" "}
              Likely causes: not enough imported bars (re-import a longer history),
              filters too strict (loosen Quality Filters in Settings → Edit Default Values),
              or the strategy genuinely didn't find an edge in this dataset.
            </div>
          )}
          {patternsFound != null && patternsFound > 0 && (
            <div className="alert alert-success" style={{ marginBottom: 16 }}>
              <strong>{patternsFound} pattern{patternsFound === 1 ? "" : "s"} passed all filters.</strong>{" "}
              CSV files are in the output folder below.
              {typeof result.output_folder === "string" && result.output_folder && (
                <>
                  {" "}
                  <button
                    className="btn-mini"
                    style={{ marginLeft: 8 }}
                    onClick={() => openFolder(String(result.output_folder)).catch(() => {})}
                    title={String(result.output_folder)}
                  >
                    📂 Open output folder
                  </button>
                </>
              )}
            </div>
          )}

          {overview && patterns.length > 0 && (
            <div className="results-grid" style={{ marginBottom: 16 }}>
              {overview.avg_ea_test_wr != null && (
                <div className="result-card">
                  <span className="result-key">Avg EA-OOS WR</span>
                  <span className="result-val">{overview.avg_ea_test_wr.toFixed(1)}%</span>
                </div>
              )}
              {overview.avg_ea_test_pf != null && (
                <div className="result-card">
                  <span className="result-key">Avg EA-OOS PF</span>
                  <span className="result-val">{overview.avg_ea_test_pf.toFixed(2)}</span>
                </div>
              )}
              {overview.avg_ea_test_expectancy_r != null && (
                <div className="result-card">
                  <span className="result-key">Avg EA-OOS Exp R</span>
                  <span className="result-val">{overview.avg_ea_test_expectancy_r.toFixed(3)}</span>
                </div>
              )}
              {typeof overview.total_ea_test_trades === "number" && (
                <div className="result-card">
                  <span className="result-key">Total EA-OOS Trades</span>
                  <span className="result-val">{overview.total_ea_test_trades}</span>
                </div>
              )}
            </div>
          )}

          {patterns.length > 0 && (
            <PatternsTable patterns={patterns} />
          )}

          {scalars.length > 0 && (
            <>
              <div className="section-label">Summary</div>
              <div className="results-grid">
                {scalars.map(([key, val]) => (
                  <div key={key} className="result-card">
                    <span className="result-key">{titleCase(key)}</span>
                    <span className="result-val result-val-truncate" title={String(val ?? "")}>
                      {renderValue(val)}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}

          {nested.length > 0 && (
            <>
              <div className="section-label" style={{ marginTop: 24 }}>Details</div>
              {nested.map(([key, val]) => (
                <details key={key} className="nested-section">
                  <summary>{titleCase(key)}</summary>
                  <pre className="raw-json">{JSON.stringify(val, null, 2)}</pre>
                </details>
              ))}
            </>
          )}
        </>
      )}

      {job?.status === "done" && !result && (
        <div className="alert alert-warn">
          Discovery completed but returned no data. Check the output folder for generated files.
        </div>
      )}
    </div>
  );
}

function HypothesisResults({ result }: { result: HypothesisDiscoveryResult }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const best = result.top_candidates[0] ?? null;

  return (
    <>
      {!result.top_candidates.length && (
        <div className="alert alert-warn">
          <strong>No hypothesis variant survived the minimum trade filter.</strong>{" "}
          Use a longer dataset, lower the minimum trades, or add more variants.
        </div>
      )}

      {best && (
        <div className="alert alert-success" style={{ marginBottom: 16 }}>
          <strong>Top candidate:</strong>{" "}
          {best.strategy_id} - active pass rate {formatPct(best.challenge_active_pass_rate)}
          {best.median_days_to_target != null && <> - median {formatNumber(best.median_days_to_target, 1)} days</>}
        </div>
      )}

      <div className="results-grid" style={{ marginBottom: 16 }}>
        <div className="result-card"><span className="result-key">Generated</span><span className="result-val">{result.variants_generated}</span></div>
        <div className="result-card"><span className="result-key">Tested</span><span className="result-val">{result.variants_tested}</span></div>
        <div className="result-card"><span className="result-key">Dataset</span><span className="result-val result-val-truncate" title={result.dataset_id}>{result.dataset_id}</span></div>
        <div className="result-card"><span className="result-key">Symbol</span><span className="result-val">{result.symbol} {result.timeframe.toUpperCase()}</span></div>
        {best && (
          <>
            <div className="result-card"><span className="result-key">Best Pass Rate</span><span className="result-val">{formatPct(best.challenge_active_pass_rate)}</span></div>
            <div className="result-card"><span className="result-key">Prop Fail Rate</span><span className="result-val">{formatPct(best.challenge_prop_fail_rate)}</span></div>
          </>
        )}
      </div>

      <div className="action-row" style={{ marginBottom: 16 }}>
        <button className="btn btn-secondary btn-sm" onClick={() => openFolder(result.artifact_folder).catch(() => undefined)} title={result.artifact_folder}>
          Open artifact folder
        </button>
        <button className="btn btn-secondary btn-sm" onClick={() => openFolder(result.summary_csv).catch(() => undefined)} title={result.summary_csv}>
          Open summary CSV
        </button>
      </div>

      {result.top_candidates.length > 0 && (
        <>
          <div className="section-label">Top Hypotheses ({result.top_candidates.length})</div>
          <table className="patterns-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Strategy</th>
                <th>Family</th>
                <th className="num">Score</th>
                <th className="num">Pass</th>
                <th className="num">Fails</th>
                <th className="num">Median Days</th>
                <th className="num">Risk</th>
                <th className="num">Trades</th>
                <th className="num">PF</th>
              </tr>
            </thead>
            <tbody>
              {result.top_candidates.map((candidate, index) => (
                <Fragment key={candidate.strategy_id}>
                  <tr>
                    <td>{index + 1}</td>
                    <td>
                      <button
                        className="link-btn"
                        onClick={() => setExpanded(expanded === candidate.strategy_id ? null : candidate.strategy_id)}
                      >
                        {candidate.strategy_id}
                      </button>
                    </td>
                    <td>{titleCase(candidate.lineage)}</td>
                    <td className="num">{formatNumber(candidate.challenge_score, 1)}</td>
                    <td className="num">{formatPct(candidate.challenge_active_pass_rate)}</td>
                    <td className="num">{formatPct(candidate.challenge_prop_fail_rate)}</td>
                    <td className="num">{formatNumber(candidate.median_days_to_target, 1)}</td>
                    <td className="num">{formatPct(candidate.risk_fraction, 2)}</td>
                    <td className="num">{candidate.trades}</td>
                    <td className="num">{formatNumber(candidate.profit_factor)}</td>
                  </tr>
                  {expanded === candidate.strategy_id && (
                    <tr className="row-detail">
                      <td colSpan={10}>
                        <div className="pattern-detail-grid">
                          <div><span className="kv-key">Hypothesis</span><span>{candidate.hypothesis}</span></div>
                          <div><span className="kv-key">Pass count</span><span>{candidate.challenge_pass_count}</span></div>
                          <div><span className="kv-key">Prop fail count</span><span>{candidate.challenge_prop_fail_count}</span></div>
                          <div><span className="kv-key">Best days</span><span>{formatNumber(candidate.best_days_to_target, 1)}</span></div>
                          <div><span className="kv-key">Internal daily stop</span><span>{formatNumber(candidate.internal_daily_stop_pct, 1)}%</span></div>
                          <div><span className="kv-key">Max trades/day</span><span>{candidate.max_trades_per_day}</span></div>
                          <div><span className="kv-key">Net profit</span><span>{formatNumber(candidate.net_profit, 2)}</span></div>
                          <div><span className="kv-key">Max DD</span><span>{formatNumber(candidate.max_drawdown_pct, 2)}%</span></div>
                          <div className="full-row"><span className="kv-key">Fingerprint</span><span className="mono small">{candidate.strategy_fingerprint}</span></div>
                          <div className="full-row"><span className="kv-key">Parameters</span><pre className="raw-json">{JSON.stringify(candidate.parameters, null, 2)}</pre></div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </>
      )}

      <details className="nested-section">
        <summary>Artifacts</summary>
        <pre className="raw-json">{JSON.stringify({
          artifact_folder: result.artifact_folder,
          summary_csv: result.summary_csv,
          summary_json: result.summary_json,
          experiment_id: result.experiment_id,
        }, null, 2)}</pre>
      </details>
    </>
  );
}

// ─── Patterns table ─────────────────────────────────────────────────────────

type SortKey = "rank" | "ea_test_wr" | "ea_test_wilson_wr" | "ea_test_pf" | "ea_test_expectancy_r" | "ea_test_trades";

function PatternsTable({ patterns }: { patterns: PatternSummary[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDesc, setSortDesc] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const sorted = [...patterns].sort((a, b) => {
    const av = (a[sortKey] as number) ?? 0;
    const bv = (b[sortKey] as number) ?? 0;
    return sortDesc ? bv - av : av - bv;
  });

  const flash = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2000);
  };

  const copySetFile = async (p: PatternSummary) => {
    if (!p.set_file) { flash("No .set file recorded for this pattern."); return; }
    setBusyId(p.pattern_id);
    try {
      const r = await getSetFileContent(p.set_file);
      await navigator.clipboard.writeText(r.content);
      flash(`Copied ${r.name} to clipboard.`);
    } catch (e) {
      flash(`Copy failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const saveToLib = async (p: PatternSummary) => {
    if (!p.set_file) { flash("No .set file recorded for this pattern."); return; }
    setBusyId(p.pattern_id);
    try {
      const r = await saveToLibrary({
        pattern_id: p.pattern_id,
        set_file: p.set_file,
        metadata: p,
      });
      flash(r.duplicate ? `Updated ${p.pattern_id} in library.` : `Saved ${p.pattern_id} to library.`);
    } catch (e) {
      flash(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const downloadSetFile = async (p: PatternSummary) => {
    if (!p.set_file) { flash("No .set file recorded."); return; }
    setBusyId(p.pattern_id);
    try {
      const r = await getSetFileContent(p.set_file);
      const blob = new Blob([r.content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = r.name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      flash(`Downloaded ${r.name}.`);
    } catch (e) {
      flash(`Download failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDesc((s) => !s);
    else { setSortKey(k); setSortDesc(true); }
  };

  const fmt = (n: number, digits = 2) =>
    typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";

  return (
    <>
      <div className="section-label" style={{ marginTop: 8 }}>
        Discovered Patterns ({patterns.length})
      </div>
      <table className="patterns-table">
        <thead>
          <tr>
            <th className="sortable" onClick={() => toggleSort("rank")}>#</th>
            <th>ID</th>
            <th>Dir</th>
            <th>Seed</th>
            <th className="sortable num" onClick={() => toggleSort("ea_test_wr")}>EA WR%</th>
            <th className="sortable num" onClick={() => toggleSort("ea_test_wilson_wr")}>Wilson%</th>
            <th className="sortable num" onClick={() => toggleSort("ea_test_pf")}>EA PF</th>
            <th className="sortable num" onClick={() => toggleSort("ea_test_expectancy_r")}>Exp R</th>
            <th className="sortable num" onClick={() => toggleSort("ea_test_trades")}>Trades</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => (
            <Fragment key={p.pattern_id}>
              <tr className={p.marginal ? "row-marginal" : ""}>
                <td>
                  {p.rank}
                  {p.marginal && (
                    <span
                      className="marginal-pill"
                      title={
                        p.soft_fail
                          ? `Soft filter: ${p.soft_fail.name} (${p.soft_fail.value} ${p.soft_fail.mode === "min" ? "<" : ">"} ${p.soft_fail.threshold})`
                          : "Soft-filtered pattern"
                      }
                    >
                      ⚠ {p.soft_fail?.name ?? "soft"}
                    </span>
                  )}
                </td>
                <td>
                  <button
                    className="link-btn"
                    onClick={() => setExpanded(expanded === p.pattern_id ? null : p.pattern_id)}
                  >
                    {p.pattern_id}
                  </button>
                </td>
                <td>{p.direction}</td>
                <td>{p.seed}</td>
                <td className="num">{fmt(p.ea_test_wr, 1)}</td>
                <td className="num">{fmt(p.ea_test_wilson_wr, 1)}</td>
                <td className="num">{fmt(p.ea_test_pf)}</td>
                <td className="num">{fmt(p.ea_test_expectancy_r, 3)}</td>
                <td className="num">{p.ea_test_trades}</td>
                <td>
                  <button
                    className="btn-mini"
                    onClick={() => saveToLib(p)}
                    disabled={busyId === p.pattern_id || !p.set_file}
                    title="Save to Strategy Library for comparison later"
                  >
                    ⭐ Save
                  </button>{" "}
                  <button
                    className="btn-mini"
                    onClick={() => copySetFile(p)}
                    disabled={busyId === p.pattern_id || !p.set_file}
                    title={p.set_file ?? "No .set file"}
                  >
                    Copy .set
                  </button>{" "}
                  <button
                    className="btn-mini"
                    onClick={() => downloadSetFile(p)}
                    disabled={busyId === p.pattern_id || !p.set_file}
                    title={p.set_file ?? "No .set file"}
                  >
                    ⬇
                  </button>{" "}
                  <button
                    className="btn-mini"
                    onClick={() => p.set_file && openFolder(p.set_file).catch(() => {})}
                    disabled={!p.set_file}
                    title={p.set_file ? "Reveal in file manager" : "No .set file"}
                  >
                    📂
                  </button>
                </td>
              </tr>
              {expanded === p.pattern_id && (
                <tr className="row-detail">
                  <td colSpan={10}>
                    <div className="pattern-detail-grid">
                      <div><span className="kv-key">Cluster</span><span>{p.cluster}</span></div>
                      <div><span className="kv-key">Bidir mode</span><span>{p.bidir_mode}</span></div>
                      <div><span className="kv-key">Composite</span><span>{fmt(p.composite_score)}</span></div>
                      <div><span className="kv-key">Train WR</span><span>{fmt(p.train_wr, 1)}%</span></div>
                      <div><span className="kv-key">Train Wilson WR</span><span>{fmt(p.train_wilson_wr, 1)}%</span></div>
                      <div><span className="kv-key">Train PF</span><span>{fmt(p.train_pf)}</span></div>
                      <div><span className="kv-key">Train trades</span><span>{p.train_trades ?? "—"}</span></div>
                      <div><span className="kv-key">Train/day</span><span>{fmt(p.train_per_day)}</span></div>
                      <div><span className="kv-key">EA-OOS breakeven WR</span><span>{fmt(p.ea_test_breakeven_wr, 1)}%</span></div>
                      <div><span className="kv-key">Cluster OOS WR</span><span>{fmt(p.test_wr, 1)}%</span></div>
                      <div><span className="kv-key">Cluster OOS PF</span><span>{fmt(p.test_pf)}</span></div>
                      <div><span className="kv-key">Cluster OOS trades</span><span>{p.test_trades}</span></div>
                      <div><span className="kv-key">Time consistency</span><span>{fmt(p.consistency)}</span></div>
                      <div><span className="kv-key">Overall WR</span><span>{fmt(p.overall_wr, 1)}%</span></div>
                      <div><span className="kv-key">Recent WR</span><span>{fmt(p.recent_wr, 1)}%</span></div>
                      <div><span className="kv-key">Implied R:R</span><span>{fmt(p.implied_rr)}</span></div>
                      <div><span className="kv-key">SL</span><span>{fmt(p.sl_pct * 100, 3)}%</span></div>
                      <div><span className="kv-key">TP</span><span>{fmt(p.tp_pct * 100, 3)}%</span></div>
                      <div className="full-row"><span className="kv-key">.set file</span><span className="mono small">{p.set_file ?? "—"}</span></div>
                      {p.genetic_rule && Object.keys(p.genetic_rule).length > 0 && (
                        <div className="full-row" style={{ marginTop: 10 }}>
                          <span className="kv-key" style={{ display: "block", marginBottom: 4 }}>
                            Indicators ({Object.keys(p.genetic_rule).length})
                          </span>
                          <IndicatorsTable rule={p.genetic_rule} />
                        </div>
                      )}
                      {p.marginal && (
                        <div className="full-row marginal-tag">
                          ⚠ Marginal — softed by{" "}
                          {p.soft_fail ? (
                            <strong>
                              {p.soft_fail.name}
                            </strong>
                          ) : "an unknown filter"}
                          {p.soft_fail && (
                            <>
                              {" "}({fmt(p.soft_fail.value)}{" "}
                              {p.soft_fail.mode === "min" ? "<" : ">"}{" "}
                              {fmt(p.soft_fail.threshold)})
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
