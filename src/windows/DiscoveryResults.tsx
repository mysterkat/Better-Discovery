/**
 * Pattern Discovery results window.
 * Loaded when URL has ?window=discovery-results&jobId=…
 */

import { useEffect, useState } from "react";
import { getDiscoveryResults, type JobRef } from "../api/discovery";
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
  const scalars = result
    ? Object.entries(result).filter(([, v]) => typeof v !== "object" || v === null)
    : [];
  const nested = result
    ? Object.entries(result).filter(([, v]) => typeof v === "object" && v !== null)
    : [];

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
          {scalars.length > 0 && (
            <>
              <div className="section-label">Summary</div>
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
          Discovery completed but returned no data. Check the OUTPUT_FOLDER for generated files.
        </div>
      )}
    </div>
  );
}
