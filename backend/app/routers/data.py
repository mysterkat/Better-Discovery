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

from pydantic import BaseModel, Field

from ..external_data import EXTERNAL_DATA, ExternalDataImportRequest
from ..bridge import data_import as di_bridge
from ..bridge import mt5_import as mt5_bridge
from ..bridge import mt5_setup as mt5_setup_bridge
from ..jobs.manager import JOBS
from ..jobs.runners import run_in_thread
from ..market_data import MARKET_DATA
from ..market_data.models import MarketDataImportRequest
from ..paths import USER_DATA
from ..schemas.common import JobRef
from ..schemas.discovery import DataImportRequest, MT5FetchRequest, MT5FetchManyRequest


# ── v0.7.0: MT5 indicator install + chart auto-setup payloads ────────────────
class MT5ApplySetupRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    # Multi-instrument basket; when present, charts open for the
    # (symbol × timeframe) cross-product. `symbol` is kept as a legacy
    # fallback (always set to symbols[0] by the UI).
    symbols: list[str] | None = None
    timeframes: list[str] = Field(..., min_length=1)
    indicators: list[str] | None = None
    htf_for_div: str = "M15"
    wait_for_ack_s: float = 10.0

router = APIRouter()

_CACHE_DIR = USER_DATA / "cache" / "data_preview"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/data/providers")
def market_data_providers() -> list[dict[str, Any]]:
    """List canonical market-data providers and their capabilities."""
    return MARKET_DATA.providers()


@router.get("/data/datasets")
def market_data_datasets() -> list[dict[str, Any]]:
    return MARKET_DATA.list_datasets()


@router.get("/data/datasets/{dataset_id}")
def market_data_dataset(dataset_id: str) -> dict[str, Any]:
    try:
        return MARKET_DATA.get_dataset(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/data/datasets/{dataset_id}")
def market_data_dataset_delete(dataset_id: str) -> dict[str, str]:
    try:
        return MARKET_DATA.delete_dataset(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/data/external")
def external_data_list() -> list[dict[str, Any]]:
    return EXTERNAL_DATA.list_data()


@router.post("/data/external/import", response_model=JobRef)
def external_data_import(req: ExternalDataImportRequest) -> JobRef:
    job = JOBS.create(
        kind="external_data_import",
        meta={"kind": req.kind, "symbol": req.symbol, "source": req.source},
    )
    run_in_thread(job, lambda: EXTERNAL_DATA.import_data(req))
    return JobRef(job_id=job.job_id, status=job.status)


@router.post("/data/provider/fetch", response_model=JobRef)
def market_data_fetch(req: MarketDataImportRequest) -> JobRef:
    """Import an immutable canonical dataset and publish discovery CSVs."""
    job = JOBS.create(
        kind="market_data_fetch",
        meta={"provider": req.provider, "symbols": req.symbols, "timeframes": req.timeframes},
    )
    run_in_thread(job, lambda: MARKET_DATA.import_data(req))
    return JobRef(job_id=job.job_id, status=job.status)


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
    clear_existing = req.clear_existing
    run_in_thread(
        job,
        lambda: mt5_bridge.fetch_historical(
            req.symbol, req.save_folder, tf_specs,
            clear_existing=clear_existing,
        ),
    )
    return JobRef(job_id=job.job_id, status=job.status)


@router.post("/data/mt5/fetch-many", response_model=JobRef)
def mt5_fetch_many(req: MT5FetchManyRequest) -> JobRef:
    """Fetch a BASKET of symbols into one folder (multi-instrument). Clears once
    before the first symbol; the rest accumulate so the cross-instrument
    discovery has gold + silver + … side by side."""
    tf_specs = [s.model_dump() for s in req.tf_specs]
    syms = [s.strip().upper() for s in req.symbols if s.strip()]
    if not syms:
        raise HTTPException(400, "no symbols provided")
    job = JOBS.create(kind="mt5_fetch", meta={"symbols": syms, "n_tf": len(tf_specs)})
    clear_existing = req.clear_existing
    save_folder = req.save_folder
    run_in_thread(
        job,
        lambda: mt5_bridge.fetch_many_symbols(
            syms, save_folder, tf_specs, clear_existing=clear_existing,
        ),
    )
    return JobRef(job_id=job.job_id, status=job.status)


@router.get("/data/current-import")
def mt5_current_import() -> dict[str, Any]:
    """Return what's currently in the canonical hist_data folder.

    Frontend uses this to (a) decide whether to show a "replace existing data"
    confirmation before a new import and (b) display which timeframes are
    currently active for Pattern Discovery to consume.
    """
    return mt5_bridge.list_current_import()


@router.delete("/data/current-import")
def mt5_clear_current_import() -> dict[str, Any]:
    return mt5_bridge.clear_data_folder()


@router.post("/data/mt5/install-helper")
def mt5_install_helper() -> dict[str, Any]:
    """v0.7.0: Copy bundled BD indicators + helper EA into the live MT5.

    Idempotent. Returns a payload describing what was copied, whether
    metaeditor64.exe was found (so we know if compilation happened
    automatically), and the next manual step the user has to do (attach
    BD_AutoSetup to a chart once).

    Errors surface as ``{"ok": false, "error": "..."}`` rather than
    HTTPException so the UI can display them inline without a toast storm.
    """
    try:
        return {"ok": True, **mt5_setup_bridge.ensure_installed()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.post("/data/mt5/apply-setup")
def mt5_apply_setup(req: MT5ApplySetupRequest) -> dict[str, Any]:
    """v0.7.0: Tell BD_AutoSetup to open charts for symbol+TFs with our stack.

    Writes ``Common/Files/bd_setup.json`` (versioned) and polls the
    ack file. On timeout returns ``{"ok": false, "error": "..."}``
    so the UI can prompt the user to verify the helper EA is running.
    """
    try:
        cfg = mt5_setup_bridge.apply_chart_setup(
            symbol=req.symbol,
            symbols=req.symbols,
            timeframes=req.timeframes,
            indicators=req.indicators,
            htf_for_div=req.htf_for_div,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        ack = mt5_setup_bridge.wait_for_ack(
            version=cfg["version"], timeout_s=req.wait_for_ack_s,
        )
    except TimeoutError as exc:
        return {"ok": False, "error": str(exc), "config": cfg, "acked": False}

    return {"ok": True, "config": cfg, "ack": ack, "acked": True}


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
