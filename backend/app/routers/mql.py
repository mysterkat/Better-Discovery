"""Set → MQL5 router.

POST /mql/export  -> convert .set content + template to a ready .mq5 file
GET  /mql/template -> return the path to the bundled PatternDiscoveryEA.mq5
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..bridge import set_to_mql as mql_bridge
from ..schemas.discovery import MqlExportRequest

router = APIRouter()


@router.post("/mql/export")
def mql_export(req: MqlExportRequest) -> dict:
    """Merge a .set file with the EA template and return the output .mq5 path."""
    try:
        path = mql_bridge.export(
            req.set_content,
            req.template_path,
            req.output_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "path": path}


@router.get("/mql/template")
def mql_template() -> dict:
    """Return the path to the bundled PatternDiscoveryEA.mq5 template."""
    return {"path": mql_bridge.default_template_path()}
