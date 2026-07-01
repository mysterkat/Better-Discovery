from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Lineage = Literal[
    "strategy_grammar",
    "time_series_breakout",
    "session_range_breakout",
    "trend_pullback",
    "volatility_expansion",
    "regime_mean_reversion",
    "liquidity_sweep_reclaim",
    "failed_breakout_reversal",
    "prior_day_level_continuation",
    "volatility_spike_reversal",
    "opening_range_continuation_reversal",
    "trend_day_pullback",
    "day_time_regime_filter",
    "inside_bar_expansion",
]


class HypothesisSpec(BaseModel):
    schema_version: int = 1
    strategy_id: str = Field(min_length=3, max_length=80)
    lineage: Lineage
    hypothesis: str = Field(min_length=20, max_length=1000)
    timeframe: Literal["m1", "m5", "m10", "m15"] = "m15"
    context_timeframes: tuple[Literal["h1", "h4"], ...] = ("h1", "h4")
    parameters: dict[str, Any]

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


class FTMOChallengeConfig(BaseModel):
    initial_balance: float = Field(default=10_000.0, gt=0)
    target_profit_pct: float = Field(default=10.0, gt=0)
    daily_loss_pct: float = Field(default=5.0, gt=0)
    max_loss_pct: float = Field(default=10.0, gt=0)
    max_attempt_days: int = Field(default=10, ge=1, le=120)
    start_frequency: str = Field(default="1D", min_length=2, max_length=12)
    risk_fractions: tuple[float, ...] = (0.005, 0.0075, 0.01)
    internal_daily_stop_pcts: tuple[float, ...] = (2.0, 3.0, 4.0)
    max_trades_per_day_options: tuple[int, ...] = (4, 8, 12)

    @model_validator(mode="after")
    def validate_grid(self) -> "FTMOChallengeConfig":
        if not self.risk_fractions:
            raise ValueError("risk_fractions cannot be empty")
        if not self.internal_daily_stop_pcts:
            raise ValueError("internal_daily_stop_pcts cannot be empty")
        if not self.max_trades_per_day_options:
            raise ValueError("max_trades_per_day_options cannot be empty")
        if any(value <= 0 or value > 0.05 for value in self.risk_fractions):
            raise ValueError("risk_fractions must be within (0, 0.05]")
        if any(value <= 0 or value > self.daily_loss_pct for value in self.internal_daily_stop_pcts):
            raise ValueError("internal_daily_stop_pcts must be within daily_loss_pct")
        if any(value <= 0 for value in self.max_trades_per_day_options):
            raise ValueError("max_trades_per_day_options must be positive")
        return self


class HypothesisDiscoveryRequest(BaseModel):
    dataset_id: str
    symbol: str = "XAUUSD"
    timeframe: Literal["m1", "m5", "m10", "m15"] = "m5"
    grammar_timeframes: tuple[Literal["m1", "m5", "m10", "m15"], ...] | None = None
    date_from: datetime
    date_to: datetime
    families: tuple[Lineage, ...] | None = None
    grammar_block_groups: tuple[
        Literal["liquidity", "structure", "imbalance", "orderflow", "sessions", "volatility", "smt"],
        ...,
    ] | None = None
    grammar_complexity: Literal["simple", "medium", "complex"] = "medium"
    grammar_randomness: Literal["low", "balanced", "high"] = "balanced"
    search_mode: Literal["market_mind", "manual", "broad", "guided"] = "market_mind"
    target_regime: Literal[
        "auto",
        "trend",
        "range_reversal",
        "volatility_expansion",
        "compression",
        "session_liquidity",
    ] = "auto"
    market_mind_bias_pct: float = Field(default=0.70, ge=0.0, le=1.0)
    random_seed: int = Field(default=310200, ge=0, le=2_147_483_647)
    max_variants: int = Field(default=5_000, ge=1, le=20_000)
    guided_initial_fraction: float = Field(default=0.35, ge=0.10, le=0.90)
    guided_generations: int = Field(default=3, ge=0, le=8)
    guided_parents_kept: int = Field(default=30, ge=1, le=250)
    guided_children_per_parent: int = Field(default=30, ge=1, le=250)
    guided_exploration_pct: float = Field(default=0.25, ge=0.0, le=0.75)
    parent_min_profit_factor: float = Field(default=1.15, ge=1.0, le=5.0)
    final_min_profit_factor: float = Field(default=1.25, ge=1.0, le=5.0)
    final_min_active_pass_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    max_candidate_drawdown_pct: float = Field(default=15.0, gt=0.0, le=100.0)
    min_closed_trades: int = Field(default=180, ge=1)
    min_trades_per_week: float = Field(default=2.5, ge=0)
    parallel_workers: int = Field(default=6, ge=1, le=32)
    top_n: int = Field(default=25, ge=1, le=250)
    lot_size: float = Field(default=0.1, gt=0)
    contract_size: float = Field(default=100.0, gt=0)
    commission_per_lot_round_turn: float = Field(default=7.0, ge=0)
    slippage_price_units: float = Field(default=0.10, ge=0)
    challenge: FTMOChallengeConfig = Field(default_factory=FTMOChallengeConfig)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_request(self) -> "HypothesisDiscoveryRequest":
        self.symbol = self.symbol.upper()
        self.date_from = self._utc(self.date_from)
        self.date_to = self._utc(self.date_to)
        if self.grammar_timeframes is not None:
            ordered = tuple(dict.fromkeys((self.timeframe, *self.grammar_timeframes)))
            self.grammar_timeframes = ordered
        if self.symbol != "XAUUSD":
            raise ValueError("hypothesis discovery is currently locked to XAUUSD")
        if self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from")
        return self


class HypothesisDiscoveryResult(BaseModel):
    experiment_id: str
    dataset_id: str
    symbol: str
    timeframe: str
    variants_generated: int
    variants_tested: int
    variants_evaluated: int | None = None
    search_summary: dict[str, Any] | None = None
    parallel_workers: int = 1
    artifact_folder: str
    summary_csv: str
    summary_json: str
    top_candidates: list[dict[str, Any]]
