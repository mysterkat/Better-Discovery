"""Set and hypothesis → MQL5 router.

POST /mql/export  -> convert .set content + template to a ready .mq5 file
POST /mql/hypothesis-export -> convert a hypothesis candidate to a standalone .mq5 EA
GET  /mql/template -> return the path to the bundled PatternDiscoveryEA.mq5
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..bridge import hypothesis_to_mql as hypothesis_bridge
from ..bridge import set_to_mql as mql_bridge
from ..schemas.discovery import HypothesisMqlExportRequest, MqlExportRequest

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
        report = mql_bridge.export_report(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **report}


@router.post("/mql/hypothesis-export")
def mql_hypothesis_export(req: HypothesisMqlExportRequest) -> dict:
    """Write a standalone EA, .set file, and hypothesis spec for a candidate."""
    try:
        result = hypothesis_bridge.export(
            req.strategy,
            output_name=req.output_name,
            risk_fraction=req.risk_fraction,
            daily_loss_pct=req.daily_loss_pct,
            max_loss_pct=req.max_loss_pct,
            max_trades_per_day=req.max_trades_per_day,
            max_spread_points=req.max_spread_points,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.get("/mql/template")
def mql_template() -> dict:
    """Return the path to the bundled PatternDiscoveryEA.mq5 template."""
    return {"path": mql_bridge.default_template_path()}
