"""Filesystem + sys.path anchoring for the backend.

Rules:
- The existing MONTE CARLO/src tree is READ-ONLY. We only prepend it to
  sys.path so bridge modules can import from it.
- All app-writable state lives under userdata/ (Desktop dev) or
  %APPDATA%/BETTER DISCOVERY/ (production installer).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Existing toolkit (read-only). Path is fixed per the delivery brief.
EXISTING_SRC = Path(r"C:\Users\micha\Desktop\MONTE CARLO\src")

if EXISTING_SRC.is_dir() and str(EXISTING_SRC) not in sys.path:
    sys.path.insert(0, str(EXISTING_SRC))

# ── App root resolution ────────────────────────────────────────────────────────
# paths.py is at: <root>/backend/app/paths.py   (dev)
#             or: <resources>/backend/app/paths.py  (production)
#
# In dev:        parents[2] == BETTER DISCOVERY/
# In production: parents[2] == resources/  (Tauri resource dir)
#                parents[3] == <install dir>

_THIS_FILE = Path(__file__).resolve()
APP_ROOT = _THIS_FILE.parents[2]

# ── User-data directory ────────────────────────────────────────────────────────
# For production installs under Program Files (which is read-only without
# elevation), redirect userdata to %APPDATA%\BETTER DISCOVERY\.
# In dev the project root is writable so keep userdata/ there.

def _resolve_userdata() -> Path:
    # Explicit override (useful for testing / CI).
    if override := os.environ.get("BD_USERDATA"):
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Dev: project root is on the Desktop — writable.
    dev_userdata = APP_ROOT / "userdata"
    try:
        dev_userdata.mkdir(parents=True, exist_ok=True)
        # Confirm it is actually writable.
        probe = dev_userdata / ".write_probe"
        probe.touch()
        probe.unlink()
        return dev_userdata
    except (OSError, PermissionError):
        pass

    # Production fallback: %APPDATA%\BETTER DISCOVERY\userdata
    appdata = Path(os.environ.get("APPDATA", Path.home()))
    prod_userdata = appdata / "BETTER DISCOVERY" / "userdata"
    prod_userdata.mkdir(parents=True, exist_ok=True)
    return prod_userdata


USER_DATA = _resolve_userdata()
(USER_DATA / "themes").mkdir(exist_ok=True)
(USER_DATA / "recent").mkdir(exist_ok=True)
(USER_DATA / "cache").mkdir(exist_ok=True)
(USER_DATA / "mql").mkdir(exist_ok=True)


def existing_src_available() -> bool:
    return EXISTING_SRC.is_dir()
