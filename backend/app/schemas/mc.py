"""Request schemas for the Monte Carlo router."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


Phase = Literal["phase1", "phase2", "funded", "longterm"]


class MCRunRequest(BaseModel):
    phase: Phase = "phase1"
    # One of pnl (inline list) or pnl_csv_path must be provided.
    pnl: Optional[list[float]] = None
    pnl_csv_path: Optional[str] = None
    pnl_split: str = "test"
    params: dict[str, Any] = Field(default_factory=dict)
    # wait=True blocks up to wait_timeout_s for the job to finish, then returns
    # result inline. Used for small synthetic runs and the Phase 3 smoke check.
    wait: bool = False
    wait_timeout_s: float = 30.0


class MCAdvancedRequest(BaseModel):
    metric: str
    params: dict[str, Any] = Field(default_factory=dict)
    wait: bool = False
    wait_timeout_s: float = 30.0
