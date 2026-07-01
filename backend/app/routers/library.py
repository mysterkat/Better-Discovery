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
    HypothesisLibrarySaveRequest,
    LibraryAttachRequest,
    LibraryEntry,
    LibraryEvolutionRequest,
    LibraryMergeRequest,
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _write_metadata(folder: Path, metadata: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    metadata["__saved_at"] = _now_iso()
    (folder / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
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

    meta_out = dict(req.metadata)
    meta_out.setdefault("__kind", "set_pattern")
    _write_metadata(folder, meta_out)

    entry = _read_entry(folder)
    if entry is None:
        raise HTTPException(500, "saved entry could not be read back")
    return LibrarySaveResponse(entry=entry, duplicate=duplicate)


@router.post("/library/save-hypothesis", response_model=LibrarySaveResponse)
def library_save_hypothesis(req: HypothesisLibrarySaveRequest) -> LibrarySaveResponse:
    """Persist a Market Mind / hypothesis strategy without requiring a .set file."""
    pattern_id = _safe_hypothesis_id(req.name or req.strategy.strategy_id)
    folder = _entry_dir(pattern_id)
    duplicate = folder.exists()
    metadata = {
        "__kind": "hypothesis",
        "name": req.name or req.strategy.strategy_id,
        "notes": req.notes,
        "source": req.source,
        "metrics": req.metrics,
        "hypothesis_strategy": req.strategy.model_dump(mode="json"),
    }
    _write_metadata(folder, metadata)
    (folder / "strategy.hypothesis.json").write_text(
        req.strategy.model_dump_json(indent=2),
        encoding="utf-8",
    )
    entry = _read_entry(folder)
    if entry is None:
        raise HTTPException(500, "saved hypothesis entry could not be read back")
    return LibrarySaveResponse(entry=entry, duplicate=duplicate)


def _safe_hypothesis_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", value).strip("_")
    return cleaned[:96] or "hypothesis_strategy"


def _entry_or_404(pattern_id: str) -> LibraryEntry:
    folder = _entry_dir(pattern_id)
    if not folder.is_dir():
        raise HTTPException(404, f"library entry not found: {pattern_id}")
    entry = _read_entry(folder)
    if entry is None:
        raise HTTPException(500, f"library entry unreadable: {pattern_id}")
    return entry


@router.post("/library/merge", response_model=LibrarySaveResponse)
def library_merge(req: LibraryMergeRequest) -> LibrarySaveResponse:
    """Create a saved merged-strategy record from 2-3 existing saved entries."""
    if not 2 <= len(req.components) <= 3:
        raise HTTPException(422, "merged strategy needs 2 or 3 components")
    ids = [item.pattern_id for item in req.components]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "merged strategy components must be unique")
    entries = [_entry_or_404(pattern_id) for pattern_id in ids]

    merge_id = _safe_hypothesis_id(f"merged_{req.name}")
    folder = _entry_dir(merge_id)
    duplicate = folder.exists()
    component_payloads = []
    for component, entry in zip(req.components, entries):
        component_payloads.append({
            "pattern_id": component.pattern_id,
            "weight": component.weight,
            "role": component.role,
            "kind": entry.metadata.get("__kind", "set_pattern"),
            "name": entry.metadata.get("name") or component.pattern_id,
            "metrics": entry.metadata.get("metrics", entry.metadata),
            "hypothesis_strategy": entry.metadata.get("hypothesis_strategy"),
            "set_path": entry.set_path,
            "csv_path": entry.csv_path,
        })
    metadata = {
        "__kind": "merged",
        "name": req.name,
        "mode": req.mode,
        "notes": req.notes,
        "components": component_payloads,
    }
    _write_metadata(folder, metadata)
    (folder / "merged_strategy.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    entry = _read_entry(folder)
    if entry is None:
        raise HTTPException(500, "merged entry could not be read back")
    return LibrarySaveResponse(entry=entry, duplicate=duplicate)


@router.post("/library/{pattern_id}/export-hypothesis-ea")
def library_export_hypothesis_ea(pattern_id: str) -> dict:
    """Export a saved hypothesis strategy entry to standalone MQL5 EA files."""
    entry = _entry_or_404(pattern_id)
    raw_strategy = entry.metadata.get("hypothesis_strategy")
    if not isinstance(raw_strategy, dict):
        raise HTTPException(422, "this library entry does not contain a hypothesis strategy")
    try:
        from ..bridge import hypothesis_to_mql
        from ..hypothesis.models import HypothesisSpec

        strategy = HypothesisSpec.model_validate(raw_strategy)
        result = hypothesis_to_mql.export(
            strategy,
            output_name=pattern_id,
            risk_fraction=float(entry.metadata.get("metrics", {}).get("risk_fraction", 0.01)),
            daily_loss_pct=float(entry.metadata.get("metrics", {}).get("internal_daily_stop_pct", 4.0)),
            max_trades_per_day=int(entry.metadata.get("metrics", {}).get("max_trades_per_day", 4)),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"export failed: {exc}") from exc
    folder = _entry_dir(pattern_id)
    for key, target_name in {
        "mq5_path": "strategy.mq5",
        "set_path": "strategy.set",
        "spec_path": "strategy.hypothesis.json",
    }.items():
        source = Path(str(result.get(key, "")))
        if source.is_file():
            shutil.copyfile(source, folder / target_name)
    return {"ok": True, **result}


@router.post("/library/{pattern_id}/evolve")
def library_evolve(pattern_id: str, req: LibraryEvolutionRequest) -> dict:
    """Create nearby child strategies from a saved hypothesis strategy."""
    entry = _entry_or_404(pattern_id)
    raw_strategy = entry.metadata.get("hypothesis_strategy")
    if not isinstance(raw_strategy, dict):
        raise HTTPException(422, "this library entry does not contain an evolvable hypothesis strategy")
    try:
        from ..hypothesis.grammar import mutate_hypothesis
        from ..hypothesis.models import HypothesisSpec

        parent = HypothesisSpec.model_validate(raw_strategy)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    children: list[LibraryEntry] = []
    for index in range(req.child_count):
        child = mutate_hypothesis(
            parent,
            child_index=index,
            generation=req.generation,
            seed=req.seed,
        )
        child_id = _safe_hypothesis_id(child.strategy_id)
        folder = _entry_dir(child_id)
        metadata = {
            "__kind": "hypothesis",
            "name": child.strategy_id,
            "notes": req.notes,
            "source": {
                "type": "evolution",
                "parent_pattern_id": pattern_id,
                "parent_strategy_id": parent.strategy_id,
                "generation": req.generation,
                "seed": req.seed,
                "child_index": index,
            },
            "metrics": {},
            "hypothesis_strategy": child.model_dump(mode="json"),
        }
        _write_metadata(folder, metadata)
        (folder / "strategy.hypothesis.json").write_text(
            child.model_dump_json(indent=2),
            encoding="utf-8",
        )
        child_entry = _read_entry(folder)
        if child_entry is not None:
            children.append(child_entry)
    return {
        "ok": True,
        "parent_pattern_id": pattern_id,
        "created": len(children),
        "children": [entry.model_dump(mode="json") for entry in children],
    }


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
