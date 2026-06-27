"""Request/response schemas for the Discovery and MQL routers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..hypothesis.models import HypothesisSpec


class DiscoveryStartRequest(BaseModel):
    # Whitelisted overrides applied to pattern_discovery_v6 module globals.
    overrides: dict[str, Any] = Field(default_factory=dict)


class DataImportRequest(BaseModel):
    path: str
    schema_hint: dict[str, Any] | None = None


class TfSpec(BaseModel):
    prefix: str                 # m | h | d | W | M
    time_value: int             # e.g. 5 for m5, 1 for h1
    trading_days: int           # requested history in trading days


class MT5FetchRequest(BaseModel):
    symbol: str = "XAUUSD"
    save_folder: str = ""       # empty → use default userdata/hist_data/
    tf_specs: list[TfSpec]
    # When true, the canonical hist_data folder is wiped of recognized
    # `{symbol}_{tf}.csv` files before the new fetch runs. Frontend should
    # set this only after the user confirms a destructive overwrite.
    clear_existing: bool = False


class MT5FetchManyRequest(BaseModel):
    """Fetch a basket of symbols into ONE folder (multi-instrument)."""
    symbols: list[str]          # e.g. ["XAUUSD", "XAGUSD", "DXY"]
    save_folder: str = ""       # empty → use default userdata/hist_data/
    tf_specs: list[TfSpec]
    # Wipes the folder ONCE before the first symbol; the rest accumulate
    # (filenames are {symbol}_{tf}.csv so different symbols never collide).
    clear_existing: bool = False


class MqlExportRequest(BaseModel):
    """Request body for POST /mql/export."""

    # Raw text of the .set file produced by pattern_discovery_v6.
    set_content: str

    # Path to the .mq5 EA template.  null/omitted → use the bundled default
    # (backend/ea/PatternDiscoveryEA.mq5, resolved via set_to_mql.py).
    template_path: str | None = None

    # Override output filename stem (no extension).
    # null → auto-generated from pattern metadata.
    output_name: str | None = None


class HypothesisMqlExportRequest(BaseModel):
    """Request body for POST /mql/hypothesis-export."""

    strategy: HypothesisSpec
    output_name: str | None = None
    risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    daily_loss_pct: float = Field(default=4.0, gt=0, le=20.0)
    max_loss_pct: float = Field(default=8.0, gt=0, le=30.0)
    max_trades_per_day: int = Field(default=4, ge=1, le=100)
    max_spread_points: float = Field(default=80.0, ge=0)
