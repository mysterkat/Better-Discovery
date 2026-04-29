"""Thread runner that wires a callable into a Job's lifecycle.

Also exposes a thread-local "current job" handle so deeply-nested code (e.g.,
the discovery bridge or stdout interceptors) can update progress / check for
cancellation without explicit job plumbing through every function call.
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Optional

from .manager import Job


# Thread-local: each worker thread can publish its own "current job" so any
# code running on that thread can call get_current_job() to locate it.
_thread_state = threading.local()


def get_current_job() -> Optional[Job]:
    return getattr(_thread_state, "job", None)


class CancelledError(Exception):
    """Raised by code that detects a cancel request and wants to bail out."""


def check_cancelled() -> None:
    """If the current thread's job has been cancel-requested, raise."""
    j = get_current_job()
    if j is not None and j.is_cancel_requested():
        raise CancelledError(f"job {j.job_id} cancelled")


def run_in_thread(job: Job, fn: Callable[[], Any]) -> threading.Thread:
    def _run() -> None:
        _thread_state.job = job
        job.mark_running()
        try:
            result = fn()
            if job.is_cancel_requested():
                job.mark_cancelled()
            else:
                job.mark_done(result)
        except CancelledError as e:
            job.append_log(f"cancelled: {e}")
            job.mark_cancelled()
        except Exception as e:
            job.append_log(traceback.format_exc())
            if job.is_cancel_requested():
                # Cancel triggered the exception (e.g., subprocess killed) —
                # report as cancelled, not failure.
                job.mark_cancelled(f"{type(e).__name__}: {e}")
            else:
                job.mark_failed(f"{type(e).__name__}: {e}")
        finally:
            _thread_state.job = None

    t = threading.Thread(target=_run, name=f"job-{job.job_id}", daemon=True)
    t.start()
    return t
