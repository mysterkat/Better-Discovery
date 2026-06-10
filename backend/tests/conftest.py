"""Shared pytest path setup.

The embedded runtime (src-tauri/binaries/python) pins sys.path via
python311._pth, so neither the repo root nor backend/ is importable by
default. Insert both so tests can do `from backend.app... import` and
`from app... import` regardless of which interpreter runs them.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "backend"

for p in (str(_REPO_ROOT), str(_BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)
