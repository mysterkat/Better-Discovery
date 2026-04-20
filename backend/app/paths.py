"""Filesystem + sys.path anchoring for the backend.

Rules:
- The existing MONTE CARLO/src tree is READ-ONLY. We only prepend it to
  sys.path so bridge modules can import from it.
- All app-writable state lives under BETTER DISCOVERY/userdata.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Existing toolkit (read-only). Path is fixed per the delivery brief.
EXISTING_SRC = Path(r"C:\Users\micha\Desktop\MONTE CARLO\src")

if EXISTING_SRC.is_dir() and str(EXISTING_SRC) not in sys.path:
    sys.path.insert(0, str(EXISTING_SRC))

# backend/app/paths.py -> parents[2] == BETTER DISCOVERY/
APP_ROOT = Path(__file__).resolve().parents[2]

USER_DATA = APP_ROOT / "userdata"
USER_DATA.mkdir(exist_ok=True)
(USER_DATA / "themes").mkdir(exist_ok=True)
(USER_DATA / "recent").mkdir(exist_ok=True)
(USER_DATA / "cache").mkdir(exist_ok=True)


def existing_src_available() -> bool:
    return EXISTING_SRC.is_dir()
