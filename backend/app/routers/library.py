"""Strategy Library router (v0.8.0).

Persists user-curated strategies across discovery runs and exposes them
to the Strategy Compare tab.

Disk layout (userdata/library/<pattern_id>/):
    strategy.set        -- copy of the .set file
    trades.csv          -- copy of the discovery trades CSV (if found)
    metadata.json       -- full PatternSummary at save time + saved_at
    mt5_backtest.htm    -- attached later by user
    mt5_backtest.csv    -- attached later by user
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..paths import DEFAULT_DISC_OUTPUT, DEFAULT_LIBRARY, USER_DATA
from ..schemas.common import Ok
from ..schemas.library import (
    LibraryAttachRequest,
    LibraryEntry,
    LibrarySaveRequest,
    LibrarySaveResponse,
)

router = APIRouter()

# pattern_id naming convention from pattern_discovery_v6.py is alphanumeric
# with underscores (e.g. "pattern_03_C2_LONG_seed42"). Reject anything that
# could escape the library folder or be confused with a relative path.
_PATTERN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

# Filenames the user-attached uploads land at. The kind drives which file.
_ATTACH_FILES: dict[str, str] = {
    "mt5_html": "mt5_backtest.htm",
    "mt5_csv":  "mt5_backtest.csv",
}


def _validate_pattern_id(pattern_id: str) -> None:
    if not _PATTERN_ID_RE.match(pattern_id):
        raise HTTPException(400, f"invalid pattern_id: {pattern_id!r}")


def _entry_dir(pattern_id: str) -> Path:
    _validate_pattern_id(pattern_id)
    return DEFAULT_LIBRARY / pattern_id


def _resolve_trades_csv(set_file: Path, metadata: dict) -> Optional[Path]:
    """Find the discovery trades CSV that lives beside the .set file.

    The toolkit writes `cluster_{cid}_{direction}_seed{seed}.csv` in the
    same seed folder as the .set. Returns None if any required field is
    missing or the file isn't there.
    """
    cluster = metadata.get("cluster")
    direction = metadata.get("direction")
    seed = metadata.get("seed")
    if cluster is None or direction is None or seed is None:
        return None
    candidate = set_file.parent / f"cluster_{cluster}_{direction}_seed{seed}.csv"
    return candidate if candidate.is_file() else None


def _within_disc_output(p: Path) -> bool:
    """v1.4.1: accept paths under any of three roots (mirrors the fix shipped
    in v1.1.3 for /discovery/set-file):
      1. DEFAULT_DISC_OUTPUT — the app's default
      2. USER_DATA          — the broader app-writable area
      3. The toolkit module's current OUTPUT_FOLDER — handles user overrides
         that point anywhere on disk (folder-picker can pick anywhere)

    Previously this hardcoded DEFAULT_DISC_OUTPUT with a brittle startswith,
    which broke the library_save tests (they create a temp userdata via a
    fixture) and broke any production save when the user customised
    OUTPUT_FOLDER for benchmark runs."""
    try:
        resolved = p.resolve()
    except OSError:
        return False

    allowed_roots: list[Path] = [
        DEFAULT_DISC_OUTPUT.resolve(),
        USER_DATA.resolve(),
    ]
    # Pull the toolkit module's current OUTPUT_FOLDER (latest override seen).
    try:
        from ..bridge import discovery as _disc_bridge
        mod_out = getattr(_disc_bridge._get_module(), "OUTPUT_FOLDER", None)
        if mod_out:
            allowed_roots.append(Path(str(mod_out)).resolve())
    except Exception:
        pass

    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _read_entry(folder: Path) -> Optional[LibraryEntry]:
    """Load a library entry from disk. Returns None if metadata.json is missing
    or unparseable — a half-written entry shouldn't crash the list endpoint."""
    meta_file = folder / "metadata.json"
    if not meta_file.is_file():
        return None
    try:
        raw = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    set_p = folder / "strategy.set"
    csv_p = folder / "trades.csv"
    html_p = folder / _ATTACH_FILES["mt5_html"]
    mt5csv_p = folder / _ATTACH_FILES["mt5_csv"]
    return LibraryEntry(
        pattern_id=folder.name,
        saved_at=raw.get("__saved_at", ""),
        lib_path=str(folder),
        set_path=str(set_p) if set_p.is_file() else None,
        csv_path=str(csv_p) if csv_p.is_file() else None,
        mt5_html_path=str(html_p) if html_p.is_file() else None,
        mt5_csv_path=str(mt5csv_p) if mt5csv_p.is_file() else None,
        metadata={k: v for k, v in raw.items() if k != "__saved_at"},
    )


@router.post("/library/save", response_model=LibrarySaveResponse)
def library_save(req: LibrarySaveRequest) -> LibrarySaveResponse:
    """Copy a discovery strategy into the persistent library."""
    folder = _entry_dir(req.pattern_id)

    set_src = Path(req.set_file)
    if not _within_disc_output(set_src):
        raise HTTPException(403, f"set_file outside discovery output: {req.set_file}")
    if not set_src.is_file() or set_src.suffix != ".set":
        raise HTTPException(404, f".set file not found: {req.set_file}")

    duplicate = folder.exists()
    folder.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(set_src, folder / "strategy.set")

    csv_src = _resolve_trades_csv(set_src, req.metadata)
    if csv_src is not None:
        shutil.copyfile(csv_src, folder / "trades.csv")

    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta_out = dict(req.metadata)
    meta_out["__saved_at"] = saved_at
    (folder / "metadata.json").write_text(
        json.dumps(meta_out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    entry = _read_entry(folder)
    if entry is None:
        raise HTTPException(500, "saved entry could not be read back")
    return LibrarySaveResponse(entry=entry, duplicate=duplicate)


@router.get("/library/list", response_model=list[LibraryEntry])
def library_list() -> list[LibraryEntry]:
    """List every saved strategy, newest first."""
    if not DEFAULT_LIBRARY.is_dir():
        return []
    entries: list[LibraryEntry] = []
    for child in DEFAULT_LIBRARY.iterdir():
        if not child.is_dir():
            continue
        entry = _read_entry(child)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda e: e.saved_at, reverse=True)
    return entries


@router.post("/library/attach", response_model=LibraryEntry)
def library_attach(req: LibraryAttachRequest) -> LibraryEntry:
    """Attach an MT5 Strategy Tester .htm or trade .csv to a saved entry."""
    folder = _entry_dir(req.pattern_id)
    if not folder.is_dir():
        raise HTTPException(404, f"library entry not found: {req.pattern_id}")

    target_name = _ATTACH_FILES.get(req.kind)
    if target_name is None:
        raise HTTPException(400, f"unknown attach kind: {req.kind}")

    try:
        contents = base64.b64decode(req.content_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, f"invalid base64 payload: {e}") from e

    (folder / target_name).write_bytes(contents)

    entry = _read_entry(folder)
    if entry is None:
        raise HTTPException(500, "entry could not be read back after attach")
    return entry


@router.get("/library/{pattern_id}/mt5_html")
def library_mt5_html(pattern_id: str) -> FileResponse:
    """Return the attached MT5 backtest .htm file as raw HTML.

    Used by the Strategy Compare tab to render the report inside an iframe.
    """
    folder = _entry_dir(pattern_id)
    target = folder / _ATTACH_FILES["mt5_html"]
    if not target.is_file():
        raise HTTPException(404, f"no MT5 html attached for {pattern_id}")
    return FileResponse(target, media_type="text/html")


@router.get("/library/{pattern_id}/trades_csv")
def library_trades_csv(pattern_id: str) -> FileResponse:
    """Return the discovery trades CSV (entry_time, exit_time, pnl_pts, ...).

    Used by the Strategy Compare tab to compute pairwise trade-overlap
    similarity and to drive future trade-distribution overlays.
    """
    folder = _entry_dir(pattern_id)
    target = folder / "trades.csv"
    if not target.is_file():
        raise HTTPException(404, f"no trades CSV saved for {pattern_id}")
    return FileResponse(target, media_type="text/csv")


@router.get("/library/{pattern_id}/mt5_csv")
def library_mt5_csv(pattern_id: str) -> FileResponse:
    """Return the attached MT5 trades CSV (used for equity-curve overlay)."""
    folder = _entry_dir(pattern_id)
    target = folder / _ATTACH_FILES["mt5_csv"]
    if not target.is_file():
        raise HTTPException(404, f"no MT5 trades CSV attached for {pattern_id}")
    return FileResponse(target, media_type="text/csv")


@router.delete("/library/{pattern_id}/attachment/{kind}", response_model=LibraryEntry)
def library_detach(pattern_id: str, kind: str) -> LibraryEntry:
    """fix 3a: Remove an MT5 attachment (html or csv) without deleting the library entry."""
    folder = _entry_dir(pattern_id)
    if not folder.is_dir():
        raise HTTPException(404, f"library entry not found: {pattern_id}")
    target_name = _ATTACH_FILES.get(kind)
    if target_name is None:
        raise HTTPException(400, f"unknown attach kind: {kind}")
    target = folder / target_name
    if target.is_file():
        target.unlink()
    entry = _read_entry(folder)
    if entry is None:
        raise HTTPException(500, "entry could not be read back after detach")
    return entry


@router.delete("/library/{pattern_id}", response_model=Ok)
def library_delete(pattern_id: str) -> Ok:
    folder = _entry_dir(pattern_id)
    if not folder.is_dir():
        raise HTTPException(404, f"library entry not found: {pattern_id}")
    shutil.rmtree(folder)
    return Ok(detail=f"removed {pattern_id}")
