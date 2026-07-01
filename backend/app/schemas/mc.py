"""Request schemas for the Monte Carlo router."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


Phase = Literal["phase1", "phase2", "funded", "longterm"]


class MCRunRequest(BaseModel):
    phase: Phase = "phase1"
    pnl: Optional[list[float]] = None
    pnl_split: str = "test"
    params: dict[str, Any] = Field(default_factory=dict)
    # wait=True blocks up to wait_timeout_s for the job to finish, then returns
    # result inline. Used for small synthetic runs and the Phase 3 smoke check.
    wait: bool = False
    wait_timeout_s: float = 30.0


class MCRunAllRequest(BaseModel):
    """Run all four phases in one job using shared pre-drawn samples."""
    pnl: Optional[list[float]] = None
    data_source: Literal["mt5_html", "local_ledger"] = "mt5_html"
    file_path_html: Optional[str] = None     # MT5 Strategy Tester HTML path
    local_ledger_path: Optional[str] = None  # Local replay CSV/Parquet ledger
    pnl_split: str = "test"
    global_params: dict[str, Any] = Field(default_factory=dict)
    phase1_params: dict[str, Any] = Field(default_factory=dict)
    phase2_params: dict[str, Any] = Field(default_factory=dict)
    funded_params: dict[str, Any] = Field(default_factory=dict)
    longterm_params: dict[str, Any] = Field(default_factory=dict)
    wait: bool = False
    wait_timeout_s: float = 120.0


class MCCompareRequest(BaseModel):
    """Run identical Monte Carlo settings against local and MT5 trade sources."""
    local_ledger_path: str
    mt5_report_path: str
    global_params: dict[str, Any] = Field(default_factory=dict)
    phase1_params: dict[str, Any] = Field(default_factory=dict)
    phase2_params: dict[str, Any] = Field(default_factory=dict)
    funded_params: dict[str, Any] = Field(default_factory=dict)
    longterm_params: dict[str, Any] = Field(default_factory=dict)
    max_trade_count_delta_pct: float = Field(default=5.0, ge=0)
    max_net_profit_delta_pct: float = Field(default=10.0, ge=0)


class MCAdvancedRequest(BaseModel):
    metric: str
    params: dict[str, Any] = Field(default_factory=dict)
    wait: bool = False
    wait_timeout_s: float = 30.0


class McRunSummary(BaseModel):
    """Lightweight projection of a saved/recent run for the list view.

    Heavy fields (equity_curves, results_df, etc.) are stripped — the full
    record is fetched separately via GET /mc/runs/{jobId}.
    """
    jobId: str
    name: Optional[str] = None
    timestamp: float
    named: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    summary: Any = None


class McRunSaveRequest(BaseModel):
    """Body for POST /mc/runs/{jobId} — promote a job into the named save list."""
    name: str = Field(..., min_length=1, max_length=120)
