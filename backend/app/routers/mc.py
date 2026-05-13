"""Monte Carlo router.

POST /mc/run       -> kick off a phase run; if wait=True, block for result.
POST /mc/advanced  -> run one of 15 advanced-metric functions.
GET  /mc/results/{job_id}
GET  /mc/metrics   -> list known metric names.
"""

from __future__ import annotations

import numpy as np
from fastapi import APIRouter, HTTPException

from ..bridge import data_import as di_bridge
from ..bridge import mc as mc_bridge
from ..jobs.manager import JOBS
from ..jobs.runners import run_in_thread
from ..schemas.common import JobRef
from ..schemas.mc import MCAdvancedRequest, MCRunAllRequest, MCRunRequest

router = APIRouter()


def _resolve_pnl_all(req: MCRunAllRequest) -> np.ndarray:
    if req.pnl is not None:
        return np.asarray(req.pnl, dtype=float)
    if req.data_source == "mt5_html":
        if not req.file_path_html:
            raise HTTPException(400, "file_path_html is required for data_source='mt5_html'")
        return mc_bridge.load_daily_pnl("mt5_html", req.file_path_html)
    # tradingview (default)
    if not req.pnl_csv_path:
        raise HTTPException(400, "pnl_csv_path is required for data_source='tradingview'")
    return mc_bridge.load_daily_pnl("tradingview", req.pnl_csv_path)


def _resolve_pnl(req: MCRunRequest) -> np.ndarray:
    if req.pnl is not None:
        return np.asarray(req.pnl, dtype=float)
    if req.pnl_csv_path:
        return di_bridge.load_csv_as_daily_pnl(req.pnl_csv_path, split_filter=req.pnl_split)
    raise HTTPException(400, "either 'pnl' or 'pnl_csv_path' is required")


@router.post("/mc/run", response_model=JobRef)
def mc_run(req: MCRunRequest) -> JobRef:
    pnl = _resolve_pnl(req)
    if pnl.size == 0:
        raise HTTPException(400, "pnl is empty")
    job = JOBS.create(kind="mc", meta={"phase": req.phase, "n_days": int(pnl.size)})
    run_in_thread(job, lambda: mc_bridge.run_phase(req.phase, pnl, dict(req.params)))
    if req.wait and job.wait(req.wait_timeout_s):
        return JobRef(job_id=job.job_id, status=job.status, result=job.result, error=job.error)
    return JobRef(job_id=job.job_id, status=job.status)


@router.get("/mc/results/{job_id}", response_model=JobRef)
def mc_results(job_id: str) -> JobRef:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job_id {job_id}")
    return JobRef(job_id=job.job_id, status=job.status, result=job.result, error=job.error)


@router.post("/mc/run_all", response_model=JobRef)
def mc_run_all(req: MCRunAllRequest) -> JobRef:
    """Start a single job that runs all four phases with shared pre-drawn samples."""
    pnl = _resolve_pnl_all(req)
    if pnl.size == 0:
        raise HTTPException(400, "pnl is empty")
    # Best-effort regime computation — only succeeds if the trade comments
    # carry R:<n> markers. None when unavailable; the dashboard renders the
    # heatmap conditionally.
    regime_data: dict | None = None
    file_for_regime = req.file_path_html if req.data_source == "mt5_html" else req.pnl_csv_path
    if file_for_regime:
        regime_data = mc_bridge.compute_regime_from_file(req.data_source, file_for_regime)
    job = JOBS.create(kind="mc_all", meta={"n_days": int(pnl.size)})
    run_in_thread(
        job,
        lambda: mc_bridge.run_all_phases(
            pnl,
            dict(req.global_params),
            dict(req.phase1_params),
            dict(req.phase2_params),
            dict(req.funded_params),
            dict(req.longterm_params),
            regime_data=regime_data,
        ),
    )
    if req.wait and job.wait(req.wait_timeout_s):
        return JobRef(job_id=job.job_id, status=job.status, result=job.result, error=job.error)
    return JobRef(job_id=job.job_id, status=job.status)


@router.get("/mc/defaults")
def mc_defaults() -> dict:
    """Current mc_funded_test module-level default values."""
    return mc_bridge.list_mc_defaults()


@router.get("/mc/params")
def mc_params() -> list:
    """Values + full UI metadata (type, group, min/max/step) for every MC constant."""
    return mc_bridge.list_mc_defaults_with_meta()


@router.get("/mc/metrics")
def mc_metrics() -> dict[str, list[str]]:
    return {
        "phases": sorted(mc_bridge.PHASE_RUNNERS.keys()),
        "advanced": sorted(mc_bridge.ADVANCED_METRICS.keys()),
    }


@router.post("/mc/advanced", response_model=JobRef)
def mc_advanced(req: MCAdvancedRequest) -> JobRef:
    if req.metric not in mc_bridge.ADVANCED_METRICS:
        raise HTTPException(400, f"unknown metric '{req.metric}'")
    job = JOBS.create(kind="mc_advanced", meta={"metric": req.metric})
    run_in_thread(job, lambda: mc_bridge.run_advanced(req.metric, dict(req.params)))
    if req.wait and job.wait(req.wait_timeout_s):
        return JobRef(job_id=job.job_id, status=job.status, result=job.result, error=job.error)
    return JobRef(job_id=job.job_id, status=job.status)
