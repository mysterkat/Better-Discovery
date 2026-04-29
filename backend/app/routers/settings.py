"""Settings + themes + jobs listing router.

GET  /settings              -> read userdata/settings.json
PUT  /settings              -> write userdata/settings.json
GET  /param-defaults        -> read userdata/param_defaults.json
PUT  /param-defaults        -> write userdata/param_defaults.json
GET  /themes                -> list theme JSON files in userdata/themes/
POST /themes                -> write a theme JSON
GET  /jobs                  -> snapshot all jobs
GET  /jobs/{job_id}/events  -> SSE stub (progress + completion)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..jobs.manager import JOBS
from ..paths import USER_DATA

router = APIRouter()

_SETTINGS_FILE = USER_DATA / "settings.json"
_PARAM_DEFAULTS_FILE = USER_DATA / "param_defaults.json"
_THEMES_DIR = USER_DATA / "themes"
_THEMES_DIR.mkdir(parents=True, exist_ok=True)


class ThemePayload(BaseModel):
    name: str
    tokens: dict[str, Any]


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    if not _SETTINGS_FILE.exists():
        return {}
    return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))


@router.put("/settings")
def put_settings(body: dict[str, Any]) -> dict[str, Any]:
    _SETTINGS_FILE.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@router.get("/param-defaults")
def get_param_defaults() -> dict[str, Any]:
    """Return persisted user-defined default values for Discovery and MC Sim params."""
    if not _PARAM_DEFAULTS_FILE.exists():
        return {}
    return json.loads(_PARAM_DEFAULTS_FILE.read_text(encoding="utf-8"))


@router.put("/param-defaults")
def put_param_defaults(body: dict[str, Any]) -> dict[str, Any]:
    """Persist user-defined default values for Discovery and MC Sim params."""
    _PARAM_DEFAULTS_FILE.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@router.get("/themes")
def list_themes() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(_THEMES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({"file": p.name, **data})
    return out


@router.post("/themes")
def save_theme(payload: ThemePayload) -> dict[str, Any]:
    safe = "".join(c for c in payload.name if c.isalnum() or c in "-_").strip() or "theme"
    path = _THEMES_DIR / f"{safe}.json"
    path.write_text(
        json.dumps({"name": payload.name, "tokens": payload.tokens}, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "file": path.name}


@router.get("/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [j.snapshot() for j in JOBS.list()]


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job_id {job_id}")

    async def gen():
        # Minimal SSE: poll job state 4 Hz until terminal, emit snapshots.
        last: str | None = None
        while True:
            snap = job.snapshot()
            payload = json.dumps(snap)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if snap["status"] in {"done", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: str) -> dict[str, Any]:
    """Request cancellation of a running job.

    Cancellation is cooperative: the job's worker code checks
    `job.is_cancel_requested()` at safe boundaries. Subprocess-based jobs
    (MT5 import) are also actively terminated by the bridge once it sees
    the flag. Discovery (in-process multiprocessing) takes effect at the
    next stage boundary.
    """
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job_id {job_id}")
    if job.status in {"done", "failed", "cancelled"}:
        return {"ok": True, "already_terminal": True, "status": job.status}
    job.request_cancel()
    # If a subprocess was registered for this job, signal it.
    proc = job.meta.get("_subprocess")
    if proc is not None and hasattr(proc, "terminate"):
        try:
            proc.terminate()
        except Exception:
            pass
    return {"ok": True, "status": job.status, "cancel_requested": True}
