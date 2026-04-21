/**
 * Monte Carlo results window.
 *
 * Phase 6: Statistics table.
 * Phase 7: Adds 2D/3D Plotly chart toggle.
 *
 * Loaded when URL has ?window=mc-results&jobId=…
 */

import { useEffect, useState } from "react";
import { getMcResults } from "../api/mc";
import type { JobRef } from "../api/mc";
import { renderValue, titleCase } from "../lib/format";
import { useSettings } from "../state/settings";

export default function MonteCarloResults() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("jobId") ?? "";
  const phase = params.get("phase") ?? "phase1";

  const [job, setJob] = useState<JobRef | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSettings = useSettings((s) => s.load);

  // Apply theme from persisted settings.
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

  // Separate scalar results from nested objects (arrays / dicts go in a raw section).
  const scalars = result
    ? Object.entries(result).filter(([, v]) => typeof v !== "object" || v === null)
    : [];
  const nested = result
    ? Object.entries(result).filter(([, v]) => typeof v === "object" && v !== null)
    : [];

  return (
    <div className="results-window">
      <div className="results-header">
        <h1>Monte Carlo — {phase}</h1>
        {jobId && <span className="job-id-badge">{jobId.slice(0, 8)}</span>}
      </div>

      {!job && !error && <p className="results-loading">Fetching results…</p>}
      {error && <div className="alert alert-error">{error}</div>}

      {job?.status === "pending" || job?.status === "running" ? (
        <p className="results-loading">Simulation running — this window will update automatically.</p>
      ) : null}

      {job?.status === "failed" && (
        <div className="alert alert-error">Run failed: {job.error}</div>
      )}

      {job?.status === "done" && result && (
        <>
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

          {nested.length > 0 && (
            <>
              <div className="section-label" style={{ marginTop: 24 }}>
                Additional data
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
