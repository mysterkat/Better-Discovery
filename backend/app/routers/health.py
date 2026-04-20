"""Health + smoke endpoints. Used by the Tauri sidecar to detect readiness."""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..paths import APP_ROOT, EXISTING_SRC, USER_DATA, existing_src_available

router = APIRouter()


class HealthResponse(BaseModel):
    ok: bool
    version: str
    python: str
    platform: str
    existing_src: str
    existing_src_found: bool
    app_root: str
    user_data: str


class EchoRequest(BaseModel):
    payload: dict[str, Any]


class EchoResponse(BaseModel):
    echoed: dict[str, Any]
    size: int


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    from .. import __version__

    return HealthResponse(
        ok=True,
        version=__version__,
        python=sys.version.split()[0],
        platform=platform.platform(),
        existing_src=str(EXISTING_SRC),
        existing_src_found=existing_src_available(),
        app_root=str(APP_ROOT),
        user_data=str(USER_DATA),
    )


@router.post("/smoke/echo", response_model=EchoResponse)
def echo(req: EchoRequest) -> EchoResponse:
    return EchoResponse(echoed=req.payload, size=len(req.payload))
