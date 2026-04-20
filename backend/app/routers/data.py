"""Data Import router.

POST /data/import          -> preview a CSV, cache it under userdata/cache/
GET  /data/preview/{id}    -> retrieve a cached preview
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..bridge import data_import as di_bridge
from ..paths import USER_DATA
from ..schemas.discovery import DataImportRequest

router = APIRouter()

_CACHE_DIR = USER_DATA / "cache" / "data_preview"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


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
