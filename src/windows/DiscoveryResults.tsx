/**
 * Pattern Discovery results window.
 * Loaded when URL has ?window=discovery-results&jobId=…
 */

import { Fragment, useEffect, useState } from "react";
import { getDiscoveryResults, getSetFileContent, type DiscoveryOverview, type JobRef, type PatternSummary } from "../api/discovery";
import { openFolder } from "../api/system";
import { renderValue, titleCase } from "../lib/format";
import { useSettings } from "../state/settings";

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

  const result = job?.result as Record<string, unknown> | null | undefined;
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
        <h1>Pattern Discovery Results</h1>
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

      {job?.status === "done" && result && (
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
              {overview.avg_test_wr != null && (
                <div className="result-card">
                  <span className="result-key">Avg Test WR</span>
                  <span className="result-val">{overview.avg_test_wr.toFixed(1)}%</span>
                </div>
              )}
              {overview.avg_test_pf != null && (
                <div className="result-card">
                  <span className="result-key">Avg Test PF</span>
                  <span className="result-val">{overview.avg_test_pf.toFixed(2)}</span>
                </div>
              )}
              {overview.avg_train_wr != null && (
                <div className="result-card">
                  <span className="result-key">Avg Train WR</span>
                  <span className="result-val">{overview.avg_train_wr.toFixed(1)}%</span>
                </div>
              )}
              {overview.avg_train_pf != null && (
                <div className="result-card">
                  <span className="result-key">Avg Train PF</span>
                  <span className="result-val">{overview.avg_train_pf.toFixed(2)}</span>
                </div>
              )}
              {typeof overview.total_test_trades === "number" && (
                <div className="result-card">
                  <span className="result-key">Total Test Trades</span>
                  <span className="result-val">{overview.total_test_trades}</span>
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

// ─── Patterns table ─────────────────────────────────────────────────────────

type SortKey = "rank" | "test_score" | "test_wr" | "test_pf" | "test_trades" | "consistency";

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
            <th className="sortable num" onClick={() => toggleSort("test_score")}>Score</th>
            <th className="sortable num" onClick={() => toggleSort("test_wr")}>Test WR%</th>
            <th className="sortable num" onClick={() => toggleSort("test_pf")}>Test PF</th>
            <th className="sortable num" onClick={() => toggleSort("test_trades")}>Trades</th>
            <th className="sortable num" onClick={() => toggleSort("consistency")}>Consist</th>
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
                <td className="num">{fmt(p.test_score)}</td>
                <td className="num">{fmt(p.test_wr, 1)}</td>
                <td className="num">{fmt(p.test_pf)}</td>
                <td className="num">{p.test_trades}</td>
                <td className="num">{fmt(p.consistency)}</td>
                <td>
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
                    Save…
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
                      <div><span className="kv-key">Overall WR</span><span>{fmt(p.overall_wr, 1)}%</span></div>
                      <div><span className="kv-key">Recent WR</span><span>{fmt(p.recent_wr, 1)}%</span></div>
                      <div><span className="kv-key">Implied R:R</span><span>{fmt(p.implied_rr)}</span></div>
                      <div><span className="kv-key">SL</span><span>{fmt(p.sl_pct * 100, 3)}%</span></div>
                      <div><span className="kv-key">TP</span><span>{fmt(p.tp_pct * 100, 3)}%</span></div>
                      <div className="full-row"><span className="kv-key">.set file</span><span className="mono small">{p.set_file ?? "—"}</span></div>
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
