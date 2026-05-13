"""Run-history persistence for Monte Carlo jobs.

Saves a small JSON file per saved/recent run under ``userdata/mc_runs/``.

There are TWO storage tiers:

* **Named saves** — explicit POST /mc/runs/{jobId} {name}. Files prefixed
  with ``saved_<jobId>.json``. Persist forever until DELETE.
* **Recent (auto)** — best-effort capture of the last N unnamed completed
  runs. Files prefixed with ``recent_<jobId>.json``. Pruned to the most
  recent ``MAX_RECENT`` entries on each write so the directory stays bounded.

The record shape is intentionally small — full result blobs can run into
megabytes, so we strip ``equity_curves`` / ``equity_paths`` / ``floor_curves``
/ ``results_df`` / ``regime`` from the persisted summary. The dashboard refetches
the full result via /mc/runs/{jobId} only when the user opens a saved run.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from .. import paths

# Maximum unnamed "recent" runs to retain.
MAX_RECENT = 10

# Heavy keys stripped from the *summary* (the lightweight list view payload).
_HEAVY_KEYS = {
    "equity_curves", "equity_paths", "floor_curves", "survival",
    "results_df", "max_dd", "final_equity", "sharpe",
    "regime",
}


def _runs_dir() -> Path:
    d = paths.USER_DATA / "mc_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path_for(job_id: str, *, named: bool) -> Path:
    prefix = "saved_" if named else "recent_"
    return _runs_dir() / f"{prefix}{job_id}.json"


def _scan() -> list[Path]:
    """Return all run JSON files (saved + recent)."""
    return sorted(_runs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _strip_heavy(result: Any) -> Any:
    """Return a shallow copy of ``result`` with large-array fields removed.

    Used for the summary view only — the on-disk full record keeps everything.
    """
    if not isinstance(result, dict):
        return result
    out: dict[str, Any] = {}
    for k, v in result.items():
        if k in _HEAVY_KEYS:
            continue
        if isinstance(v, dict):
            inner = {kk: vv for kk, vv in v.items() if kk not in _HEAVY_KEYS}
            out[k] = inner
        else:
            out[k] = v
    return out


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    """Project a stored record down to the list-view summary shape."""
    return {
        "jobId":     record.get("jobId"),
        "name":      record.get("name"),
        "timestamp": record.get("timestamp"),
        "named":     record.get("named", False),
        "params":    record.get("params"),
        "summary":   _strip_heavy(record.get("result")),
    }


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, record: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _prune_recents() -> None:
    """Cap the recent-runs collection at ``MAX_RECENT`` files."""
    recents = sorted(
        _runs_dir().glob("recent_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in recents[MAX_RECENT:]:
        try:
            old.unlink()
        except OSError:
            pass


def _find_existing(job_id: str) -> Path | None:
    saved = _path_for(job_id, named=True)
    if saved.exists():
        return saved
    recent = _path_for(job_id, named=False)
    if recent.exists():
        return recent
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def list_runs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in _scan():
        rec = _read(p)
        if rec is not None:
            out.append(_summary(rec))
    return out


def get_run(job_id: str) -> dict[str, Any] | None:
    p = _find_existing(job_id)
    if p is None:
        return None
    return _read(p)


def save_run(
    job_id: str,
    name: str | None,
    *,
    params: dict[str, Any] | None = None,
    result: Any = None,
    named: bool = True,
) -> dict[str, Any]:
    """Persist a run. ``named=True`` keeps it forever; False enters the recent ring."""
    record: dict[str, Any] = {
        "jobId":     job_id,
        "name":      name,
        "timestamp": time.time(),
        "named":     bool(named),
        "params":    params or {},
        "result":    result,
    }
    # If renaming an existing recent → saved, drop the recent file.
    if named:
        recent = _path_for(job_id, named=False)
        if recent.exists():
            try:
                recent.unlink()
            except OSError:
                pass
    target = _path_for(job_id, named=named)
    _write(target, record)
    if not named:
        _prune_recents()
    return _summary(record)


def auto_capture(
    job_id: str,
    *,
    params: dict[str, Any] | None = None,
    result: Any = None,
) -> None:
    """Best-effort save into the recent ring. Silent on any failure."""
    # Don't shadow an existing named save for the same job.
    if _path_for(job_id, named=True).exists():
        return
    try:
        save_run(job_id, name=None, params=params, result=result, named=False)
    except Exception:
        pass


def delete_run(job_id: str) -> bool:
    p = _find_existing(job_id)
    if p is None:
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False


__all__: Iterable[str] = (
    "list_runs", "get_run", "save_run", "auto_capture", "delete_run", "MAX_RECENT",
)
