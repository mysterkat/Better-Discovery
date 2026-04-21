import { useState, useEffect } from "react";
import { getDefaults, startDiscovery } from "../api/discovery";
import { useJobs } from "../state/jobs";
import JobProgress from "../components/JobProgress";
import { openResultWindow } from "../lib/windows";

// The subset of pattern_discovery_v6 module-level constants that are safe
// and useful to override from the UI (all others use their baked-in defaults).
const OVERRIDE_KEYS: { key: string; label: string; description: string }[] = [
  { key: "RANDOM_SEED",   label: "Random Seed",   description: "Set a fixed seed for reproducible results." },
  { key: "TRAIN_RATIO",   label: "Train Ratio",   description: "Fraction of data used for training (0.5–0.9)." },
  { key: "MIN_TRADES",    label: "Min Trades",    description: "Minimum trades in a cluster to keep it." },
  { key: "OUTPUT_FOLDER", label: "Output Folder", description: "Override default output folder path." },
];

export default function DiscoveryTab() {
  const [defaults, setDefaults] = useState<Record<string, unknown>>({});
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));

  useEffect(() => {
    getDefaults().then(setDefaults).catch(() => {/* backend not ready yet */});
  }, []);

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    setJobId(null);
    try {
      const parsed: Record<string, unknown> = {};
      for (const { key } of OVERRIDE_KEYS) {
        const raw = overrides[key]?.trim();
        if (!raw) continue;
        const n = Number(raw);
        parsed[key] = isNaN(n) ? raw : n;
      }
      const ref = await startDiscovery(parsed);
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
      `discovery-results-${jobId}`,
      "Pattern Discovery Results",
      { window: "discovery-results", jobId },
    );
  };

  const isRunning = !!jobId && job?.status === "running";
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed");

  return (
    <div className="tab-content">
      <div className="tab-header">
        <h2>Pattern Discovery</h2>
        <p className="tab-subtitle">
          Run Pattern Discovery v6 on the loaded dataset to discover profitable
          trading patterns. Results open in a separate window.
        </p>
      </div>

      <div className="form-section">
        <div className="section-label">Parameter overrides</div>
        <p className="form-hint">
          Leave blank to use the defaults from{" "}
          <code>pattern_discovery_v6.py</code>.
        </p>
        <div className="override-grid">
          {OVERRIDE_KEYS.map(({ key, label, description }) => (
            <div key={key} className="field">
              <label className="field-label">
                {label}
                <span className="field-default">
                  {defaults[key] != null ? ` (default: ${defaults[key]})` : ""}
                </span>
              </label>
              <input
                className="field-input"
                type="text"
                value={overrides[key] ?? ""}
                placeholder={String(defaults[key] ?? "")}
                onChange={(e) =>
                  setOverrides((prev) => ({ ...prev, [key]: e.target.value }))
                }
                disabled={isRunning}
              />
              <span className="field-hint">{description}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="action-row">
        <button
          className="btn btn-primary"
          onClick={handleStart}
          disabled={starting || isRunning}
        >
          {starting ? "Starting…" : "▶ Run Discovery"}
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
          <button
            className="btn btn-accent"
            onClick={handleDone}
          >
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
