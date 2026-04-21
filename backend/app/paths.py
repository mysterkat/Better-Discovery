"""Filesystem + sys.path anchoring for the backend.

Rules:
- The MONTE CARLO toolkit is bundled under backend/toolkit/ so the app is
  fully self-contained. We prepend that directory to sys.path.
- All app-writable state lives under userdata/ (Desktop dev) or
  %APPDATA%/BETTER DISCOVERY/ (production installer).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_THIS_FILE_EARLY = Path(__file__).resolve()

# Bundled toolkit — backend/toolkit/ relative to this file's backend/app/ location.
TOOLKIT_DIR = _THIS_FILE_EARLY.parents[1] / "toolkit"

# Fallback: original MONTE CARLO location (dev convenience, optional).
EXISTING_SRC = Path(r"C:\Users\micha\Desktop\MONTE CARLO\src")

# Prefer the bundled toolkit; fall back to external path if toolkit missing.
_toolkit_path = TOOLKIT_DIR if TOOLKIT_DIR.is_dir() else (
    EXISTING_SRC if EXISTING_SRC.is_dir() else None
)
if _toolkit_path and str(_toolkit_path) not in sys.path:
    sys.path.insert(0, str(_toolkit_path))

# ── App root resolution ────────────────────────────────────────────────────────
# paths.py is at: <root>/backend/app/paths.py   (dev)
#             or: <resources>/backend/app/paths.py  (production)
#
# In dev:        parents[2] == BETTER DISCOVERY/
# In production: parents[2] == resources/  (Tauri resource dir)
#                parents[3] == <install dir>

APP_ROOT = _THIS_FILE_EARLY.parents[2]

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
    return _toolkit_path is not None
