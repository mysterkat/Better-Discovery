"""Discovery router.

POST /discovery/start                 -> kick off pattern_discovery_v6.main()
GET  /discovery/status/{job_id}
GET  /discovery/results/{job_id}
GET  /discovery/defaults              -> current overridable constant values
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..bridge import discovery as disc_bridge
from ..jobs.manager import JOBS
from ..jobs.runners import run_in_thread
from ..schemas.common import JobRef
from ..schemas.discovery import DiscoveryStartRequest

router = APIRouter()


@router.get("/discovery/defaults")
def discovery_defaults() -> dict:
    vals = disc_bridge.list_defaults()
    # Stringify any Path-like values so the response stays JSON-safe.
    return {k: (str(v) if not isinstance(v, (int, float, str, bool, list, dict)) else v) for k, v in vals.items()}


@router.post("/discovery/start", response_model=JobRef)
def discovery_start(req: DiscoveryStartRequest) -> JobRef:
    job = JOBS.create(kind="discovery", meta={"overrides": req.overrides})
    run_in_thread(job, lambda: disc_bridge.run_discovery(dict(req.overrides)))
    return JobRef(job_id=job.job_id, status=job.status)


@router.get("/discovery/status/{job_id}")
def discovery_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job_id {job_id}")
    return job.snapshot()


@router.get("/discovery/results/{job_id}", response_model=JobRef)
def discovery_results(job_id: str) -> JobRef:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job_id {job_id}")
    return JobRef(job_id=job.job_id, status=job.status, result=job.result, error=job.error)
