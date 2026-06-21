"""Canonical contracts shared by the API, MCP server, and MT5 worker."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class StrategySpec(BaseModel):
    schema_version: int = 1
    name: str = Field(min_length=1, max_length=120)
    source_set_path: str
    parameters: dict[str, str]
    discovery_meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(exclude={"source_set_path"}, mode="json")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def from_set(cls, path: str | Path) -> "StrategySpec":
        from ..bridge.set_to_mql import parse_set_file

        source = Path(path).resolve()
        if not source.is_file() or source.suffix.lower() != ".set":
            raise FileNotFoundError(f"strategy .set not found: {source}")
        parsed = parse_set_file(source.read_text(encoding="utf-8", errors="replace"))
        return cls(
            name=source.stem,
            source_set_path=str(source),
            parameters={k: str(v) for k, v in parsed["params"].items()},
            discovery_meta=parsed["meta"],
        )


class MT5Environment(BaseModel):
    terminal_path: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    metaeditor_path: str = r"C:\Program Files\MetaTrader 5\MetaEditor64.exe"
    data_path: str | None = None
    portable: bool = False


class BacktestSpec(BaseModel):
    dataset_role: Literal["validation", "walk_forward", "lockbox"] = "validation"
    symbol: str = "XAUUSD"
    timeframe: str = "M10"
    date_from: date
    date_to: date
    model: int = Field(default=4, ge=0, le=4)
    deposit: float = Field(default=10_000.0, gt=0)
    currency: str = "USD"
    leverage: int = Field(default=100, gt=0)
    execution_mode: int = 0
    timeout_seconds: int = Field(default=3600, ge=30, le=86400)

    @model_validator(mode="after")
    def valid_dates(self) -> "BacktestSpec":
        if self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from")
        return self


class ReportMetrics(BaseModel):
    expert: str = ""
    symbol: str = ""
    period: str = ""
    net_profit: float | None = None
    profit_factor: float | None = None
    expected_payoff: float | None = None
    total_trades: int | None = None
    win_rate_pct: float | None = None
    maximal_balance_drawdown_pct: float | None = None
    maximal_equity_drawdown_pct: float | None = None
    gross_profit: float | None = None
    gross_loss: float | None = None
    closed_trades_parsed: int = 0
    segments: dict[str, dict[str, dict[str, float | int | None]]] = Field(
        default_factory=dict
    )
    inputs: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, str] = Field(default_factory=dict)


class PromotionPolicy(BaseModel):
    min_trades: int = Field(default=100, ge=1)
    min_profit_factor: float = Field(default=1.15, ge=0)
    min_expected_payoff: float = 0.0
    min_net_profit: float = 0.0
    max_equity_drawdown_pct: float = Field(default=20.0, gt=0)


class GateResult(BaseModel):
    decision: Literal["promote", "reject"]
    passed: list[str]
    failed: list[str]
    warning: str = (
        "A passing backtest is only a screening result, not evidence of future profitability."
    )
