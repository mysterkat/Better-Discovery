import { useEffect, useRef } from "react";
import { useJobs, type JobStatus } from "../state/jobs";

interface JobProgressProps {
  jobId: string | null;
  onDone?: (result: unknown) => void;
  onError?: (error: string) => void;
  /** When true, render a Cancel button while the job is running. Default true. */
  showCancel?: boolean;
}

const STATUS_LABELS: Record<JobStatus, string> = {
  pending: "Queued…",
  running: "Running…",
  done: "✓ Complete",
  failed: "✗ Failed",
  cancelled: "Cancelled",
};

function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${s.toString().padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}h ${mm.toString().padStart(2, "0")}m`;
}

export default function JobProgress({
  jobId,
  onDone,
  onError,
  showCancel = true,
}: JobProgressProps) {
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const subscribe = useJobs((s) => s.subscribe);
  const cancel = useJobs((s) => s.cancel);

  // Keep callbacks in refs to avoid stale-closure issues in the effect.
  const onDoneRef = useRef(onDone);
  const onErrorRef = useRef(onError);
  useEffect(() => { onDoneRef.current = onDone; }, [onDone]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  // Connect to SSE stream for this job.
  useEffect(() => {
    if (!jobId) return;
    return subscribe(jobId);
  }, [jobId, subscribe]);

  // Fire callbacks when status reaches a terminal state.
  const prevStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!job || job.status === prevStatus.current) return;
    prevStatus.current = job.status;
    if (job.status === "done") onDoneRef.current?.(job.result);
    if (job.status === "failed") onErrorRef.current?.(job.error ?? "Unknown error");
  });

  if (!jobId || !job) return null;

  const isRunning = job.status === "running" || job.status === "pending";
  const pct = Math.max(0, Math.min(100, Math.round((job.progress ?? 0) * 100)));
  const stageLabel = job.stage_index != null && job.stage_total != null
    ? `[${job.stage_index}/${job.stage_total}] ${job.stage_name ?? ""}`
    : (job.stage_name ?? "");

  return (
    <div className={`job-progress status-${job.status}`}>
      <div className="job-progress-row">
        <span className="job-dot" aria-hidden="true" />
        <span className="job-label">
          {job.cancel_requested && isRunning ? "Cancelling…" : STATUS_LABELS[job.status]}
        </span>
        {isRunning && job.seed_index != null && job.seed_total != null && job.seed_total > 1 && (
          <span className="job-seed" title={`Current random seed: ${job.seed_value ?? "?"}`}>
            Seed {job.seed_index}/{job.seed_total}
          </span>
        )}
        {isRunning && stageLabel && (
          <span className="job-stage">{stageLabel}</span>
        )}
        {isRunning && job.eta_seconds != null && (
          <span className="job-eta">~{formatEta(job.eta_seconds)} left</span>
        )}
        {showCancel && isRunning && !job.cancel_requested && (
          <button
            className="job-cancel-btn"
            onClick={() => cancel(jobId)}
            title="Stop this job"
          >
            ✕ Stop
          </button>
        )}
        {job.status === "failed" && job.error && (
          <span className="job-error-msg" title={job.error}>— {job.error}</span>
        )}
      </div>
      {(isRunning || job.status === "done") && (
        <div className="job-progress-bar" aria-hidden="true">
          <div className="job-progress-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}
