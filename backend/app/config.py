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
    # Frontend dev origins for CORS. Empty items are ignored.
    dev_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.environ.get(
            "BD_DEV_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    )
    # Tauri webview origins vary by runtime/build mode.
    tauri_origins: tuple[str, ...] = (
        "tauri://localhost",
        "https://tauri.localhost",
        "http://tauri.localhost",
        "null",
    )


CONFIG = Config()
