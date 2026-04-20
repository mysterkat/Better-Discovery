"""Runtime config for the backend.

Values come from environment variables; defaults are dev-friendly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str = os.environ.get("BD_BACKEND_HOST", "127.0.0.1")
    port: int = int(os.environ.get("BD_BACKEND_PORT", "8765"))
    # Frontend dev origin for CORS. Empty string disables CORS.
    dev_origin: str = os.environ.get("BD_DEV_ORIGIN", "http://localhost:5173")


CONFIG = Config()
