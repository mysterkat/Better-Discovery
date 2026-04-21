/**
 * Monte Carlo results window.
 *
 * Phase 6: Statistics table.
 * Phase 7: 2D | 3D Plotly chart toggle.
 *
 * Loaded when URL has ?window=mc-results&jobId=…&phase=…
 */

import { useEffect, useState, lazy, Suspense } from "react";
import { getMcResults } from "../api/mc";
import type { JobRef } from "../api/mc";
import { renderValue, titleCase } from "../lib/format";
import { useSettings } from "../state/settings";

// Lazy-load heavy chart bundles so the window opens instantly.
const Chart2D = lazy(() => import("../components/charts/Chart2D"));
const Chart3D = lazy(() => import("../components/charts/Chart3D"));

type ViewMode = "2d" | "3d";

export default function MonteCarloResults() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("jobId") ?? "";
  const phase = params.get("phase") ?? "phase1";

  const [job, setJob]       = useState<JobRef | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const [view, setView]     = useState<ViewMode>("2d");

  const loadSettings = useSettings((s) => s.load);
  useEffect(() => { loadSettings(); }, [loadSettings]);

  useEffect(() => {
    if (!jobId) { setError("No jobId in URL."); return; }
    let timer: ReturnType<typeof setInterval>;

    const poll = async () => {
      try {
        const result = await getMcResults(jobId);
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

  // Separate scalar metrics from nested objects for the summary table.
  const scalars = result
    ? Object.entries(result).filter(
        ([k, v]) => (typeof v !== "object" || v === null) && k !== "phase"
      )
    : [];
  const nested = result
    ? Object.entries(result).filter(
        ([k, v]) =>
          typeof v === "object" && v !== null && k !== "equity_curves"
      )
    : [];

  return (
    <div className="results-window">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="results-header">
        <h1>Monte Carlo — {phase}</h1>
        <div className="results-header-right">
          {jobId && <span className="job-id-badge">{jobId.slice(0, 8)}</span>}
          {job?.status === "done" && result && (
            <div className="view-toggle" role="group" aria-label="Chart mode">
              <button
                className={`toggle-btn${view === "2d" ? " active" : ""}`}
                onClick={() => setView("2d")}
              >
                2D
              </button>
              <button
                className={`toggle-btn${view === "3d" ? " active" : ""}`}
                onClick={() => setView("3d")}
              >
                3D
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Loading / errors ────────────────────────────────────────── */}
      {!job && !error && <p className="results-loading">Fetching results…</p>}
      {error && <div className="alert alert-error">{error}</div>}

      {(job?.status === "pending" || job?.status === "running") && (
        <p className="results-loading">
          Simulation running — this window will update automatically.
        </p>
      )}

      {job?.status === "failed" && (
        <div className="alert alert-error">Run failed: {job.error}</div>
      )}

      {/* ── Results ─────────────────────────────────────────────────── */}
      {job?.status === "done" && result && (
        <>
          {/* Summary stats (always shown) */}
          {scalars.length > 0 && (
            <>
              <div className="section-label">Summary statistics</div>
              <div className="results-grid">
                {scalars.map(([key, val]) => (
                  <div key={key} className="result-card">
                    <span className="result-key">{titleCase(key)}</span>
                    <span className="result-val">{renderValue(val)}</span>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Chart area — toggled by 2D | 3D */}
          <div className="section-label" style={{ marginTop: 24 }}>
            Charts&ensp;
            <span className="section-label-sub">
              ({view === "2d" ? "classic view" : "3-D surface / scatter"})
            </span>
          </div>
          <Suspense fallback={<div className="results-loading">Loading charts…</div>}>
            {view === "2d" ? (
              <Chart2D data={result} />
            ) : (
              <Chart3D data={result} />
            )}
          </Suspense>

          {/* Collapsible raw data sections */}
          {nested.length > 0 && (
            <>
              <div className="section-label" style={{ marginTop: 24 }}>
                Raw data
              </div>
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
          Run completed but returned no data. Check backend logs.
        </div>
      )}
    </div>
  );
}
