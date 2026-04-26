"""Data router.

GET  /data/mt5/check           -> test MT5 connection
POST /data/mt5/fetch           -> start async job: pull historical data from MT5
GET  /data/mt5/candles         -> calculate candle count for given TF + trading_days
POST /data/import              -> (legacy) preview a CSV file
GET  /data/preview/{id}        -> retrieve a cached preview
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..bridge import data_import as di_bridge
from ..bridge import mt5_import as mt5_bridge
from ..jobs.manager import JOBS
from ..jobs.runners import run_in_thread
from ..paths import USER_DATA
from ..schemas.common import JobRef
from ..schemas.discovery import DataImportRequest, MT5FetchRequest

router = APIRouter()

_CACHE_DIR = USER_DATA / "cache" / "data_preview"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/data/mt5/check")
def mt5_check() -> dict[str, Any]:
    """Test MT5 connection in an isolated subprocess (crash-safe)."""
    try:
        return mt5_bridge.check_connection()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/data/mt5/default_folder")
def mt5_default_folder() -> dict[str, str]:
    return {"folder": mt5_bridge.DEFAULT_HIST_FOLDER}


@router.get("/data/mt5/candles")
def mt5_candles(prefix: str, time_value: int, trading_days: int) -> dict[str, int]:
    try:
        n = mt5_bridge.candles_for_days(prefix, time_value, trading_days)
        return {"candles": n}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/data/mt5/fetch", response_model=JobRef)
def mt5_fetch(req: MT5FetchRequest) -> JobRef:
    """Start an async job that connects to MT5 and downloads all requested TFs."""
    tf_specs = [s.model_dump() for s in req.tf_specs]
    job = JOBS.create(
        kind="mt5_fetch",
        meta={"symbol": req.symbol, "n_tf": len(tf_specs)},
    )
    run_in_thread(
        job,
        lambda: mt5_bridge.fetch_historical(req.symbol, req.save_folder, tf_specs),
    )
    return JobRef(job_id=job.job_id, status=job.status)


@router.post("/data/import")
def data_import(req: DataImportRequest) -> dict:
    p = Path(req.path)
    if not p.exists():
        raise HTTPException(404, f"file not found: {p}")
    try:
        preview = di_bridge.preview_csv(p)
    except Exception as e:
        raise HTTPException(400, f"preview failed: {type(e).__name__}: {e}") from e

    preview_id = uuid.uuid4().hex[:12]
    (_CACHE_DIR / f"{preview_id}.json").write_text(
        json.dumps(preview, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"id": preview_id, **preview}


@router.get("/data/preview/{preview_id}")
def data_preview(preview_id: str) -> dict:
    f = _CACHE_DIR / f"{preview_id}.json"
    if not f.exists():
        raise HTTPException(404, f"unknown preview id {preview_id}")
    return json.loads(f.read_text(encoding="utf-8"))
