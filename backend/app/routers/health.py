"""Health + smoke endpoints. Used by the Tauri sidecar to detect readiness."""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..paths import (
    APP_ROOT, TOOLKIT_DIR, USER_DATA,
    DEFAULT_HIST_DATA, DEFAULT_DISC_OUTPUT,
    existing_src_available, validate_paths,
)

router = APIRouter()


class HealthResponse(BaseModel):
    ok: bool
    version: str
    python: str
    platform: str
    toolkit_found: bool
    app_root: str
    user_data: str


class PathsResponse(BaseModel):
    app_root: str
    toolkit: str
    userdata: str
    hist_data: str
    disc_output: str
    toolkit_ok: bool
    userdata_ok: bool
    hist_data_ok: bool
    disc_output_ok: bool


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
        toolkit_found=existing_src_available(),
        app_root=str(APP_ROOT),
        user_data=str(USER_DATA),
    )


@router.get("/health/paths", response_model=PathsResponse)
def health_paths() -> PathsResponse:
    checks = validate_paths()
    return PathsResponse(
        app_root=str(APP_ROOT),
        toolkit=str(TOOLKIT_DIR),
        userdata=str(USER_DATA),
        hist_data=str(DEFAULT_HIST_DATA),
        disc_output=str(DEFAULT_DISC_OUTPUT),
        toolkit_ok=checks.get("toolkit", False),
        userdata_ok=checks.get("userdata", False),
        hist_data_ok=checks.get("hist_data", False),
        disc_output_ok=checks.get("disc_output", False),
    )


@router.post("/smoke/echo", response_model=EchoResponse)
def echo(req: EchoRequest) -> EchoResponse:
    return EchoResponse(echoed=req.payload, size=len(req.payload))
