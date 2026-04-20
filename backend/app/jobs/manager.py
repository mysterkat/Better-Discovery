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
    finished_at: Optional[float] = None
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def wait(self, timeout: float) -> bool:
        """Block up to `timeout` seconds. Returns True if the job finished."""
        return self._done.wait(timeout)

    def mark_running(self) -> None:
        self.status = "running"

    def mark_done(self, result: Any) -> None:
        self.result = result
        self.status = "done"
        self.progress = 1.0
        self.finished_at = time.time()
        self._done.set()

    def mark_failed(self, error: str) -> None:
        self.error = error
        self.status = "failed"
        self.finished_at = time.time()
        self._done.set()

    def append_log(self, line: str) -> None:
        self.log.append(line)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "log_tail": self.log[-20:],
            "meta": self.meta,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
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
