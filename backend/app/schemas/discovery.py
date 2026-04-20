"""Request schemas for the Discovery router."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DiscoveryStartRequest(BaseModel):
    # Whitelisted overrides applied to pattern_discovery_v6 module globals.
    overrides: dict[str, Any] = Field(default_factory=dict)


class DataImportRequest(BaseModel):
    path: str
    schema_hint: dict[str, Any] | None = None


class MqlExportRequest(BaseModel):
    pattern_id: str
    template: str
