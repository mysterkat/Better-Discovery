import { useEffect, useRef } from "react";
import { useJobs, type JobStatus } from "../state/jobs";

interface JobProgressProps {
  jobId: string | null;
  onDone?: (result: unknown) => void;
  onError?: (error: string) => void;
}

const STATUS_LABELS: Record<JobStatus, string> = {
  pending: "Queued…",
  running: "Running…",
  done: "✓ Complete",
  failed: "✗ Failed",
  cancelled: "Cancelled",
};

export default function JobProgress({ jobId, onDone, onError }: JobProgressProps) {
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const subscribe = useJobs((s) => s.subscribe);

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

  return (
    <div className={`job-progress status-${job.status}`}>
      <span className="job-dot" aria-hidden="true" />
      <span className="job-label">{STATUS_LABELS[job.status]}</span>
      {job.status === "failed" && job.error && (
        <span className="job-error-msg"> — {job.error}</span>
      )}
    </div>
  );
}
