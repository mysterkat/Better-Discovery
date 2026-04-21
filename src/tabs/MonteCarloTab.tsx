import { useState } from "react";
import { runMc, type McPhase } from "../api/mc";
import { useJobs } from "../state/jobs";
import FilePicker from "../components/FilePicker";
import JobProgress from "../components/JobProgress";
import { openResultWindow } from "../lib/windows";

interface PhaseInfo {
  label: string;
  description: string;
}

const PHASES: Record<McPhase, PhaseInfo> = {
  phase1: {
    label: "Phase 1 — Standard",
    description: "Win rate, profit factor, max drawdown, equity curve distribution.",
  },
  phase2: {
    label: "Phase 2 — Extended",
    description: "Time analysis, streak statistics, worst-case scenario tables.",
  },
  funded: {
    label: "Funded Account",
    description: "Breach probability, daily loss limits, funded phase progression.",
  },
  longterm: {
    label: "Long-Term",
    description: "1–5 year growth projections, compound risk, ruin probability.",
  },
};

export default function MonteCarloTab() {
  const [phase, setPhase] = useState<McPhase>("phase1");
  const [csvPath, setCsvPath] = useState("");
  const [nSims, setNSims] = useState("1000");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));

  const handleRun = async () => {
    const csv = csvPath.trim();
    if (!csv) {
      setError("Please enter a CSV file path.");
      return;
    }
    setStarting(true);
    setError(null);
    setJobId(null);
    try {
      const params: Record<string, unknown> = {};
      const n = parseInt(nSims, 10);
      if (!isNaN(n) && n > 0) params.n_simulations = n;

      const ref = await runMc({ phase, pnl_csv_path: csv, params });
      setJobId(ref.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const handleDone = async () => {
    if (!jobId) return;
    await openResultWindow(
      `mc-results-${jobId}`,
      `Monte Carlo — ${phase} results`,
      { window: "mc-results", jobId, phase },
    );
  };

  const isRunning = !!jobId && job?.status === "running";
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed");

  return (
    <div className="tab-content">
      <div className="tab-header">
        <h2>Monte Carlo</h2>
        <p className="tab-subtitle">
          Simulate thousands of equity trajectories on your P&amp;L data.
          Results open in a separate chart window.
        </p>
      </div>

      {/* Phase selector */}
      <div className="form-section">
        <div className="section-label">Simulation mode</div>
        <div className="phase-grid">
          {(Object.entries(PHASES) as [McPhase, PhaseInfo][]).map(([p, info]) => (
            <button
              key={p}
              className={`phase-btn${phase === p ? " active" : ""}`}
              onClick={() => setPhase(p)}
              disabled={isRunning}
            >
              <strong>{info.label}</strong>
              <span>{info.description}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Inputs */}
      <div className="form-section">
        <FilePicker
          label="CSV File Path (P&L data)"
          value={csvPath}
          onChange={setCsvPath}
          placeholder="C:\…\pattern_results.csv"
          hint="Path to a CSV file containing a P&L column (use Data Import to verify)."
        />
        <div className="field">
          <label className="field-label">Simulations</label>
          <input
            className="field-input field-sm"
            type="number"
            min={100}
            max={100000}
            step={100}
            value={nSims}
            onChange={(e) => setNSims(e.target.value)}
            disabled={isRunning}
          />
          <span className="field-hint">100–100 000 paths (higher = slower but smoother).</span>
        </div>
      </div>

      <div className="action-row">
        <button
          className="btn btn-primary"
          onClick={handleRun}
          disabled={starting || isRunning}
        >
          {starting ? "Starting…" : "▶ Run Simulation"}
        </button>
        {isDone && (
          <button
            className="btn btn-secondary"
            onClick={() => { setJobId(null); setError(null); }}
          >
            New Run
          </button>
        )}
        {jobId && job?.status === "done" && (
          <button className="btn btn-accent" onClick={handleDone}>
            ↗ Open Results
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <JobProgress
        jobId={jobId}
        onDone={handleDone}
        onError={(msg) => setError(msg)}
      />
    </div>
  );
}
