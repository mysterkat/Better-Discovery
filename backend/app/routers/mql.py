"""Set->MQL router.

POST /mql/export -> returns 501 until the upstream exporter lands.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..bridge import set_to_mql as mql_bridge
from ..schemas.discovery import MqlExportRequest

router = APIRouter()


@router.post("/mql/export")
def mql_export(req: MqlExportRequest) -> dict:
    try:
        path = mql_bridge.export(req.pattern_id, req.template)
    except mql_bridge.SetToMqlNotAvailable as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    return {"ok": True, "path": path}
