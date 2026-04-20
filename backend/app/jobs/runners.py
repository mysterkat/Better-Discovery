"""Thread runner that wires a callable into a Job's lifecycle."""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from .manager import Job


def run_in_thread(job: Job, fn: Callable[[], Any]) -> threading.Thread:
    def _run() -> None:
        job.mark_running()
        try:
            result = fn()
            job.mark_done(result)
        except Exception as e:
            job.append_log(traceback.format_exc())
            job.mark_failed(f"{type(e).__name__}: {e}")

    t = threading.Thread(target=_run, name=f"job-{job.job_id}", daemon=True)
    t.start()
    return t
