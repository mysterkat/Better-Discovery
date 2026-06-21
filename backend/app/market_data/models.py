"""Contracts for immutable market-data datasets."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class MarketDataImportRequest(BaseModel):
    provider: Literal["dukascopy"] = "dukascopy"
    symbols: list[str] = Field(default_factory=lambda: ["XAUUSD"], min_length=1)
    timeframes: list[str] = Field(default_factory=lambda: ["m1", "m5", "m15"], min_length=1)
    date_from: datetime
    date_to: datetime
    include_ticks: bool = True
    write_discovery_csv: bool = True
    price_digits: dict[str, int] = Field(default_factory=dict)
    resume_dataset_id: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "MarketDataImportRequest":
        if self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from")
        self.symbols = list(dict.fromkeys(s.strip().upper() for s in self.symbols if s.strip()))
        if not self.symbols:
            raise ValueError("at least one symbol is required")
        self.timeframes = list(dict.fromkeys(tf.strip().lower() for tf in self.timeframes))
        return self


class DatasetFile(BaseModel):
    kind: Literal["ticks", "bars", "discovery_csv"]
    symbol: str
    timeframe: str | None = None
    path: str
    rows: int
    sha256: str
    first_time: str | None = None
    last_time: str | None = None
    quality: dict[str, Any] = Field(default_factory=dict)


class DatasetManifest(BaseModel):
    schema_version: int = 2
    dataset_id: str
    state: Literal["building", "complete", "failed"] = "building"
    provider: str
    venue: str
    timezone: str = "UTC"
    symbols: list[str]
    timeframes: list[str]
    requested_from: str
    requested_to: str
    created_at: str
    completed_at: str | None = None
    files: list[DatasetFile] = Field(default_factory=list)
    quality: dict[str, Any] = Field(default_factory=dict)
    import_options: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
