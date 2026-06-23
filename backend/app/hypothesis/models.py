from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Lineage = Literal[
    "time_series_breakout",
    "session_range_breakout",
    "trend_pullback",
    "volatility_expansion",
    "regime_mean_reversion",
]


class HypothesisSpec(BaseModel):
    schema_version: int = 1
    strategy_id: str = Field(min_length=3, max_length=80)
    lineage: Lineage
    hypothesis: str = Field(min_length=20, max_length=1000)
    timeframe: Literal["m15"] = "m15"
    context_timeframes: tuple[Literal["h1", "h4"], ...] = ("h1", "h4")
    parameters: dict[str, int | float | str | bool]

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class HypothesisBarRequest(BaseModel):
    dataset_id: str
    strategy: HypothesisSpec
    date_from: datetime
    date_to: datetime
    dataset_role: Literal[
        "fit", "internal_oos", "development", "validation", "variant_retest",
        "lockbox", "five_year_confirmation",
    ]
    initial_balance: float = Field(default=10_000.0, gt=0)
    lot_size: float = Field(default=0.1, gt=0)
    contract_size: float = Field(default=100.0, gt=0)
    commission_per_lot_round_turn: float = Field(default=7.0, ge=0)
    slippage_price_units: float = Field(default=0.05, ge=0)
    promotion_policy: dict[str, int | float] | None = None

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_range(self) -> "HypothesisBarRequest":
        self.date_from = self._utc(self.date_from)
        self.date_to = self._utc(self.date_to)
        if self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from")
        return self


class HypothesisBarResult(BaseModel):
    experiment_id: str
    strategy_fingerprint: str
    dataset_id: str
    dataset_role: str
    ledger_parquet: str
    ledger_csv: str
    metrics: dict[str, Any]
    gate: dict[str, Any]
