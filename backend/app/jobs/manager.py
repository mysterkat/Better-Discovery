"""In-process job registry.

Single-user desktop app: no Redis, no Celery. Jobs are tracked in a dict
guarded by a lock; runners.run_in_thread executes the work on a daemon thread
and writes results back through Job.mark_done / Job.mark_failed.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

JobStatus = Literal["pending", "running", "done", "failed", "cancelled"]


@dataclass
class Job:
    job_id: str
    kind: str
    status: JobStatus = "pending"
    progress: float = 0.0
    log: list[str] = field(default_factory=list)
    result: Any = None
    error: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # Stage-name progress reporting (e.g., "[3/13] Bidirectional Analysis").
    stage_name: Optional[str] = None
    stage_index: Optional[int] = None
    stage_total: Optional[int] = None
    # ETA in seconds, derived from elapsed / stages-completed × stages-remaining.
    eta_seconds: Optional[float] = None
    # Multi-seed batch progress (discovery only). seed_index is 1-based.
    seed_index: Optional[int] = None
    seed_total: Optional[int] = None
    seed_value: Optional[int] = None
    # Cancel cooperation: set by POST /jobs/{id}/cancel; workers check it.
    cancel_requested: bool = False
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def wait(self, timeout: float) -> bool:
        """Block up to `timeout` seconds. Returns True if the job finished."""
        return self._done.wait(timeout)

    def mark_running(self) -> None:
        self.status = "running"
        self.started_at = time.time()

    def mark_stage(self, name: str, index: int, total: int) -> None:
        """Update the current stage indicator and recompute ETA.

        ETA is a linear projection: (elapsed / stages_completed) × stages_remaining.
        Off for non-uniform stages but good enough as a "still alive, ~N min left"
        signal. We treat stage_index as the *just-completed* stage count so the
        first call (1/13) means "starting stage 1, 0 stages done yet".
        """
        self.stage_name = name
        self.stage_index = index
        self.stage_total = total
        # progress = fraction of stages started — gives a non-zero bar early.
        if total > 0:
            self.progress = max(0.0, min(1.0, index / total))
        # ETA: extrapolate from time spent so far.
        if self.started_at is not None and index > 1 and total > index:
            elapsed = time.time() - self.started_at
            stages_done = max(1, index - 1)  # finished stages
            per_stage = elapsed / stages_done
            self.eta_seconds = per_stage * (total - index + 1)
        else:
            self.eta_seconds = None

    def mark_seed(self, index: int, total: int, value: int) -> None:
        """Update which seed of a multi-seed batch is currently running.
        Stage progress within the seed continues to flow through mark_stage."""
        self.seed_index = index
        self.seed_total = total
        self.seed_value = value

    def mark_done(self, result: Any) -> None:
        self.result = result
        self.status = "done"
        self.progress = 1.0
        self.eta_seconds = 0.0
        self.finished_at = time.time()
        self._done.set()

    def mark_failed(self, error: str) -> None:
        self.error = error
        self.status = "failed"
        self.finished_at = time.time()
        self._done.set()

    def mark_cancelled(self, reason: str = "Cancelled by user") -> None:
        self.error = reason
        self.status = "cancelled"
        self.finished_at = time.time()
        self._done.set()

    def request_cancel(self) -> None:
        """Mark cancellation requested. Workers cooperate by checking
        is_cancel_requested() at safe boundaries; subprocess-based jobs may
        also be terminated externally via meta-stored handles."""
        self.cancel_requested = True

    def is_cancel_requested(self) -> bool:
        return self.cancel_requested

    def append_log(self, line: str) -> None:
        self.log.append(line)

    def snapshot(self) -> dict[str, Any]:
        # Filter underscore-prefixed keys out of meta — they hold runtime
        # handles like a live Popen reference for cancellation, which are
        # not JSON-serializable and must never reach the SSE stream.
        public_meta = {k: v for k, v in self.meta.items() if not k.startswith("_")}
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "stage_name": self.stage_name,
            "stage_index": self.stage_index,
            "stage_total": self.stage_total,
            "eta_seconds": self.eta_seconds,
            "seed_index": self.seed_index,
            "seed_total": self.seed_total,
            "seed_value": self.seed_value,
            "log_tail": self.log[-20:],
            "meta": public_meta,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.cancel_requested,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, *, kind: str, meta: dict[str, Any] | None = None) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], kind=kind, meta=meta or {})
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())


JOBS = JobManager()
