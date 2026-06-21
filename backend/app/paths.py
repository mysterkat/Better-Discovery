"""Filesystem + sys.path anchoring for the backend.

All paths are derived from the location of THIS file at runtime so the
app always writes output to folders inside its own install tree regardless
of the current working directory or where the user placed the exe.

Rules:
- The toolkit (pattern_discovery_v6, mc_funded_test, import_hist_data …) lives
  under backend/toolkit/ which is bundled with the app. That directory is
  prepended to sys.path so the bridges can import the modules directly.
- All app-writable state lives under userdata/ (Desktop dev) or
  %APPDATA%/BETTER DISCOVERY/ (production installer in Program Files).
- DEFAULT_HIST_DATA   → userdata/hist_data/   (MT5 import output)
- DEFAULT_DISC_OUTPUT → userdata/discovery/   (pattern discovery output)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()

# ── Toolkit directory ──────────────────────────────────────────────────────────
# paths.py lives at: backend/app/paths.py
# toolkit lives at:  backend/toolkit/
# The toolkit is shipped with the app — no external fallbacks. This guarantees
# someone who clones the repo from GitHub can run the app without configuring
# any developer-machine-specific paths.
TOOLKIT_DIR: Path = _THIS_FILE.parents[1] / "toolkit"

_toolkit_on_path = TOOLKIT_DIR if TOOLKIT_DIR.is_dir() else None
if _toolkit_on_path and str(_toolkit_on_path) not in sys.path:
    sys.path.insert(0, str(_toolkit_on_path))

# ── App root ───────────────────────────────────────────────────────────────────
# Dev:        parents[2] == BETTER DISCOVERY/
# Production: parents[2] == resources/  (Tauri resource dir)
APP_ROOT: Path = _THIS_FILE.parents[2]

# ── User-data directory ────────────────────────────────────────────────────────
def _resolve_userdata() -> Path:
    if override := os.environ.get("BD_USERDATA"):
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Prefer a userdata/ dir next to the project root (dev, Desktop installs).
    dev_ud = APP_ROOT / "userdata"
    try:
        dev_ud.mkdir(parents=True, exist_ok=True)
        probe = dev_ud / ".write_probe"
        probe.touch()
        probe.unlink()
        return dev_ud
    except (OSError, PermissionError):
        pass

    # Production fallback (Program Files → read-only).
    appdata = Path(os.environ.get("APPDATA", Path.home()))
    prod_ud = appdata / "BETTER DISCOVERY" / "userdata"
    prod_ud.mkdir(parents=True, exist_ok=True)
    return prod_ud


USER_DATA: Path = _resolve_userdata()

# ── Guaranteed subdirectories ──────────────────────────────────────────────────
for _sub in (
    "themes", "recent", "cache", "mql", "hist_data", "discovery", "library",
    "research", "research/artifacts", "research/configs", "research/reports",
    "market_data",
):
    (USER_DATA / _sub).mkdir(exist_ok=True)

# Convenient default path constants used by bridges.
DEFAULT_HIST_DATA: Path   = USER_DATA / "hist_data"
DEFAULT_DISC_OUTPUT: Path = USER_DATA / "discovery"
DEFAULT_LIBRARY: Path     = USER_DATA / "library"
DEFAULT_RESEARCH: Path    = USER_DATA / "research"
DEFAULT_MARKET_DATA: Path = USER_DATA / "market_data"


def existing_src_available() -> bool:
    return _toolkit_on_path is not None


# ── Startup path-health check ──────────────────────────────────────────────────
def validate_paths() -> dict[str, bool]:
    """Called at startup to verify all critical directories are accessible.

    Returns a dict of {path_name: is_ok} for each checked location.
    Also writes a path manifest to userdata/path_manifest.json so the
    frontend can display the resolved paths in Settings.
    """
    checks: dict[str, bool] = {
        "toolkit":       TOOLKIT_DIR.is_dir(),
        "userdata":      USER_DATA.is_dir(),
        "hist_data":     DEFAULT_HIST_DATA.is_dir(),
        "disc_output":   DEFAULT_DISC_OUTPUT.is_dir(),
    }
    manifest: dict[str, str] = {
        "app_root":         str(APP_ROOT),
        "toolkit":          str(TOOLKIT_DIR),
        "userdata":         str(USER_DATA),
        "hist_data":        str(DEFAULT_HIST_DATA),
        "disc_output":      str(DEFAULT_DISC_OUTPUT),
        "toolkit_on_path":  str(_toolkit_on_path or ""),
    }
    try:
        manifest_file = USER_DATA / "path_manifest.json"
        manifest_file.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass
    return checks
