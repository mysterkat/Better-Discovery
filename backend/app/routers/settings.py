"""Settings + themes + jobs listing router.

GET  /settings              -> read userdata/settings.json
PUT  /settings              -> write userdata/settings.json
GET  /param-defaults        -> read userdata/param_defaults.json
PUT  /param-defaults        -> write userdata/param_defaults.json
GET  /themes                -> list theme JSON files in userdata/themes/
POST /themes                -> write a theme JSON
GET  /jobs                  -> snapshot all jobs
GET  /jobs/{job_id}/events  -> SSE stub (progress + completion)
POST /system/open-folder    -> reveal a userdata path in the OS file manager
POST /system/clear-cache    -> delete generated .set/.mq5/.csv artifacts
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..jobs.manager import JOBS
from ..paths import DEFAULT_DISC_OUTPUT, DEFAULT_LIBRARY, USER_DATA

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


class OpenFolderRequest(BaseModel):
    path: str


@router.post("/system/open-folder")
def open_folder(req: OpenFolderRequest) -> dict[str, Any]:
    """Reveal a folder (or the parent of a file) in the OS file manager.

    Sandboxed: the path MUST resolve inside USER_DATA. This prevents the
    endpoint from being abused to launch arbitrary system locations.
    """
    target = Path(req.path).resolve()
    safe_root = USER_DATA.resolve()

    # Allow files too — open the parent directory and select the file when
    # the OS supports it (Windows /select). For folders, just open them.
    folder = target if target.is_dir() else target.parent
    select_file = target if target.is_file() else None

    try:
        folder.relative_to(safe_root)
    except ValueError:
        raise HTTPException(403, f"path outside userdata: {req.path}")

    if not folder.is_dir():
        raise HTTPException(404, f"folder does not exist: {folder}")

    try:
        if sys.platform.startswith("win"):
            if select_file is not None:
                # /select highlights the file in Explorer
                subprocess.Popen(
                    ["explorer", "/select,", str(select_file)],
                    close_fds=True,
                )
            else:
                os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            args = ["open", "-R", str(select_file)] if select_file else ["open", str(folder)]
            subprocess.Popen(args, close_fds=True)
        else:  # linux/other
            subprocess.Popen(["xdg-open", str(folder)], close_fds=True)
    except Exception as e:
        raise HTTPException(500, f"failed to open folder: {e}") from e

    return {"ok": True, "opened": str(folder)}


# fix 4a/4b: Known cache types and what they map to on disk.
# "discovery"       → userdata/discovery/     (pattern-discovery artifacts)
# "mql"             → userdata/mql/            (MQL export files)
# "library_reports" → userdata/library/*/mt5_backtest.{htm,csv}
#                     (Compare-tab attachments — keeps strategies intact)
#
# NOT clearable:
#   userdata/hist_data/        — imported MT5 history (slow to re-fetch)
#   userdata/settings.json     — user settings
#   userdata/param_defaults.json — user parameter defaults
#   userdata/themes/           — custom themes
#   userdata/library/*/metadata.json, strategy.set, trades.csv — library entries
_ALL_CACHE_TYPES = frozenset({"discovery", "mql", "library_reports"})


def _clear_folder(folder: Path) -> tuple[int, int]:
    """Recursively delete all contents of a folder. Returns (files, bytes)."""
    files_removed = 0
    bytes_removed = 0
    if not folder.is_dir():
        return files_removed, bytes_removed
    for entry in folder.iterdir():
        try:
            if entry.is_dir():
                for sub in entry.rglob("*"):
                    if sub.is_file():
                        try:
                            bytes_removed += sub.stat().st_size
                            files_removed += 1
                        except OSError:
                            pass
                shutil.rmtree(entry, ignore_errors=True)
            else:
                try:
                    bytes_removed += entry.stat().st_size
                except OSError:
                    pass
                entry.unlink(missing_ok=True)
                files_removed += 1
        except OSError:
            continue
    return files_removed, bytes_removed


def _clear_library_reports() -> tuple[int, int]:
    """Remove only MT5 attachment files from library entries; keeps the entries."""
    files_removed = 0
    bytes_removed = 0
    if not DEFAULT_LIBRARY.is_dir():
        return files_removed, bytes_removed
    for entry_dir in DEFAULT_LIBRARY.iterdir():
        if not entry_dir.is_dir():
            continue
        for fname in ("mt5_backtest.htm", "mt5_backtest.csv"):
            fpath = entry_dir / fname
            if fpath.is_file():
                try:
                    bytes_removed += fpath.stat().st_size
                    fpath.unlink()
                    files_removed += 1
                except OSError:
                    pass
    return files_removed, bytes_removed


class ClearCacheRequest(BaseModel):
    """fix 4b: optional list of cache types to clear. Empty = clear all."""
    types: list[str] = []


@router.post("/system/clear-cache")
def clear_cache(body: ClearCacheRequest | None = None) -> dict[str, Any]:
    """fix 4a/4b: Delete selected cache types or all if none specified.

    Cache types:
      "discovery"       — discovery artifacts (.set/.csv/.png etc.)
      "mql"             — MQL export files
      "library_reports" — MT5 attachments per library entry (htm + csv)

    Raises 400 if an unknown type is requested.
    Settings, themes, param defaults, imported history, and library strategy
    metadata are never touched.
    """
    requested = set((body.types if body and body.types else []) or _ALL_CACHE_TYPES)
    unknown = requested - _ALL_CACHE_TYPES
    if unknown:
        raise HTTPException(400, f"unknown cache types: {sorted(unknown)}")

    summary: dict[str, Any] = {"ok": True, "folders": {}}
    total_files = 0
    total_bytes = 0

    if "discovery" in requested:
        f, b = _clear_folder(DEFAULT_DISC_OUTPUT)
        summary["folders"]["discovery"] = {"files_removed": f, "bytes_removed": b}
        total_files += f; total_bytes += b

    if "mql" in requested:
        f, b = _clear_folder(USER_DATA / "mql")
        summary["folders"]["mql"] = {"files_removed": f, "bytes_removed": b}
        total_files += f; total_bytes += b

    if "library_reports" in requested:
        f, b = _clear_library_reports()
        summary["folders"]["library_reports"] = {"files_removed": f, "bytes_removed": b}
        total_files += f; total_bytes += b

    summary["total_files"] = total_files
    summary["total_bytes"] = total_bytes
    return summary


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
