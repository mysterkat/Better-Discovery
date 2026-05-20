"""Schemas for the Strategy Library (v0.8.0).

The library is a user-curated set of discovered strategies that survive
across discovery runs. Each entry is a folder under userdata/library/
keyed by pattern_id, containing the .set file, the discovery trades CSV
(when present), the full PatternSummary as JSON, and optionally an
MT5 Strategy Tester backtest report attached later by the user.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel


class LibrarySaveRequest(BaseModel):
    pattern_id: str
    set_file: str
    metadata: dict[str, Any]


class LibraryEntry(BaseModel):
    pattern_id: str
    saved_at: str
    lib_path: str
    set_path: Optional[str] = None
    csv_path: Optional[str] = None
    mt5_html_path: Optional[str] = None
    mt5_csv_path: Optional[str] = None
    metadata: dict[str, Any]


class LibrarySaveResponse(BaseModel):
    entry: LibraryEntry
    duplicate: bool = False


AttachKind = Literal["mt5_html", "mt5_csv"]


class LibraryAttachRequest(BaseModel):
    pattern_id: str
    kind: AttachKind
    # Raw file bytes encoded as base64. Used because adding python-multipart
    # for one tiny upload (typically <1 MB) isn't worth the dep.
    content_b64: str
