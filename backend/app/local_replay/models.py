from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReplayRequest(BaseModel):
    dataset_id: str
    set_path: str
    symbol: str = "XAUUSD"
    timeframe: str = "m10"
    dataset_role: Literal["validation", "walk_forward", "lockbox"] = "validation"
    initial_balance: float = Field(default=10_000.0, gt=0)
    contract_size: float = Field(default=100.0, gt=0)
    commission_per_lot_round_turn: float = Field(default=7.0, ge=0)
    slippage_points: float = Field(default=0.0, ge=0)
    session_utc_offset: int = Field(default=0, ge=-12, le=14)
    chart_max_bars: int = Field(default=2500, ge=100, le=20_000)
    date_from: datetime | None = None
    date_to: datetime | None = None

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_date_range(self) -> "ReplayRequest":
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError("date_from and date_to must be provided together")
        if self.date_from is not None and self.date_to is not None:
            self.date_from = self._utc(self.date_from)
            self.date_to = self._utc(self.date_to)
            if self.date_to <= self.date_from:
                raise ValueError("date_to must be after date_from")
        return self


class ReplayMetrics(BaseModel):
    trades: int
    wins: int
    win_rate_pct: float | None
    net_profit: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    expected_payoff: float | None
    max_drawdown: float
    max_drawdown_pct: float


class ReplayResult(BaseModel):
    replay_id: str
    strategy_fingerprint: str
    dataset_id: str
    dataset_role: str
    ledger_csv: str
    ledger_parquet: str
    metrics: ReplayMetrics
    chart: dict
    warnings: list[str] = Field(default_factory=list)
