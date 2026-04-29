"""Request/response schemas for the Discovery and MQL routers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class MqlExportRequest(BaseModel):
    """Request body for POST /mql/export."""

    # Raw text of the .set file produced by pattern_discovery_v6.
    set_content: str

    # Path to the .mq5 EA template.  null/omitted → use bundled default
    # (MONTE CARLO/ea/PatternDiscoveryEA.mq5).
    template_path: str | None = None

    # Override output filename stem (no extension).
    # null → auto-generated from pattern metadata.
    output_name: str | None = None
