/**
 * Saved Monte Carlo run history.
 *
 * Renders below the run controls in MonteCarloTab. Loads from `/mc/runs` on
 * mount; each row has Open (re-launches the dashboard window for that
 * jobId) and Delete actions.
 */

import { useEffect } from "react";
import { useMcRuns } from "../state/mcRuns";
import { openResultWindow } from "../lib/windows";

function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 0) return "just now";
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? "" : "s"} ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`;
  const mo = Math.floor(day / 30);
  if (mo < 12) return `${mo} month${mo === 1 ? "" : "s"} ago`;
  const yr = Math.floor(day / 365);
  return `${yr} year${yr === 1 ? "" : "s"} ago`;
}

function formatPct(v: number | undefined): string {
  if (v == null || isNaN(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

export default function RunHistory() {
  const runs = useMcRuns((s) => s.runs);
  const loading = useMcRuns((s) => s.loading);
  const load = useMcRuns((s) => s.load);
  const remove = useMcRuns((s) => s.remove);

  useEffect(() => {
    void load();
  }, [load]);

  const open = (jobId: string) => {
    void openResultWindow(`mc-dashboard-${jobId.slice(0, 8)}`,
      "Monte Carlo Dashboard",
      { window: "mc-dashboard", jobId });
  };

  const onDelete = (jobId: string) => {
    if (window.confirm("Delete this saved run?")) {
      void remove(jobId);
    }
  };

  return (
    <div className="form-section mc-run-history">
      <div className="section-label">Saved Runs</div>
      {loading && runs.length === 0 ? (
        <p className="tab-loading">Loading saved runs…</p>
      ) : runs.length === 0 ? (
        <p className="field-hint" style={{ marginTop: 4 }}>
          No saved runs yet. After a run completes, click 💾 Save to keep it here.
        </p>
      ) : (
        <div className="mc-run-history-list">
          <div className="mc-run-history-row mc-run-history-head">
            <span>Name</span>
            <span>When</span>
            <span>Preset</span>
            <span>Pass</span>
            <span>Payout</span>
            <span></span>
          </div>
          {runs.map((r) => (
            <div key={r.jobId} className="mc-run-history-row">
              <span className="mc-run-history-name" title={r.jobId}>{r.name}</span>
              <span className="mc-run-history-when" title={new Date(r.timestamp).toLocaleString()}>
                {relativeTime(r.timestamp)}
              </span>
              <span className="mc-run-history-preset">{r.preset_id ?? "—"}</span>
              <span className="mc-run-history-metric">{formatPct(r.pass_rate)}</span>
              <span className="mc-run-history-metric">{formatPct(r.payout_rate)}</span>
              <span className="mc-run-history-actions">
                <button className="btn btn-secondary btn-sm" onClick={() => open(r.jobId)}>
                  Open
                </button>
                <button
                  className="btn btn-secondary btn-sm mc-run-history-delete"
                  onClick={() => onDelete(r.jobId)}
                  title="Delete this saved run"
                >×</button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
