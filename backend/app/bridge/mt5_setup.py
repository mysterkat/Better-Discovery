"""MT5 indicator + helper-EA installer and chart-auto-setup bridge.

v0.7.0: BETTER DISCOVERY ships 12 native MT5 indicators (under
``backend/mt5/indicators/``) and one helper EA (``backend/mt5/services/``)
that together let the host app preconfigure MT5 with the right symbol,
timeframes, and indicator stack on every connection test or data import.

The flow is:

1. ``ensure_installed()`` calls MT5 via the ``MetaTrader5`` Python package
   to resolve the terminal's ``data_path`` (per-instance ``MQL5/`` folder)
   and ``commondata_path`` (cross-instance ``Common/Files/`` folder).
2. It copies our ``.mq5`` source files into:
       - ``<data_path>/MQL5/Indicators/BetterDiscovery/``
       - ``<data_path>/MQL5/Experts/BetterDiscovery/``
   …only when the bundled source file is newer than the installed one.
3. If ``metaeditor64.exe`` is found next to ``terminal64.exe`` we run it
   with ``/compile`` so the freshly-copied source is built into ``.ex5``
   in-place. (Otherwise MT5 will throw an "unknown indicator" until the
   user opens MetaEditor once and recompiles manually — we surface this
   in the result dict so the UI can warn.)
4. ``apply_chart_setup(symbol, timeframes, indicators=None)`` writes
   ``<commondata_path>/Files/bd_setup.json`` with an incremented
   ``version`` field. The BD_AutoSetup helper EA (which the user has to
   attach to any chart ONCE) wakes on its 1-second timer, reads the JSON,
   opens charts via ``ChartOpen()``, and attaches indicators via
   ``IndicatorCreate`` + ``ChartIndicatorAdd``.
5. ``wait_for_ack(version, timeout_s)`` polls
   ``<commondata_path>/Files/bd_setup_ack.json`` until the helper EA
   echoes back the same version we wrote (or the timeout elapses).

Nothing in this module touches the registry — ``mt5.terminal_info()``
already abstracts the MT5-side discovery, and it works for both
default installs and portable terminals.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..paths import APP_ROOT  # noqa: F401  (kept for forward compatibility)

# ── Source locations bundled in the repo ──────────────────────────────────────
# When running from source: backend/mt5/indicators/*.mq5 etc.
# When running from a Tauri bundle: those files live under the resource dir
# alongside the backend folder. ``_resolve_source_dir`` figures out which.

_INDICATOR_STEMS = (
    "BD_PinBar", "BD_RollingSharpe", "BD_MacdNorm", "BD_VwapDist",
    "BD_SDZone", "BD_VolPriceDiv", "BD_BBExpanding", "BD_PrevSessBias",
    "BD_POCdist", "BD_Regime", "BD_HtfDiv", "BD_MtfBullScore",
)
_HELPER_EA_STEM   = "BD_AutoSetup"
_DUMPER_EA_STEM   = "BD_FeatureDump"   # one-shot validation-harness dumper
_INSTALL_SUBDIR   = "BetterDiscovery"   # used under both Indicators/ and Experts/


def _resolve_source_dir() -> Path:
    """Return the path to ``backend/mt5/`` inside the running app.

    Walks up from this file: ``backend/app/bridge/mt5_setup.py`` →
    ``parents[2]`` is ``backend/``.
    """
    return Path(__file__).resolve().parents[2] / "mt5"


# ── MT5 path resolution ───────────────────────────────────────────────────────

def _resolve_mt5_paths() -> dict[str, str]:
    """Return ``{install, data, common}`` paths from the live MT5 terminal.

    Raises ``RuntimeError`` if MT5 isn't installed, isn't running, or the
    terminal hasn't logged in. Auto-initialises and shuts down so we don't
    leave a dangling connection on the user's terminal.
    """
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "The MetaTrader5 Python package is not available. Reinstall "
            "BETTER DISCOVERY or run `pip install MetaTrader5` inside the "
            "embedded Python."
        ) from exc

    if not mt5.initialize():
        err = mt5.last_error()
        raise RuntimeError(
            f"MT5 initialize failed (code {err[0]}): {err[1]}. "
            "Make sure MetaTrader 5 is open and logged in."
        )
    try:
        info = mt5.terminal_info()
        if info is None:
            raise RuntimeError("mt5.terminal_info() returned None")
        # ``path`` is the install directory containing terminal64.exe.
        # ``data_path`` is the per-instance MQL5 sandbox (under %APPDATA%).
        # ``commondata_path`` is the cross-instance Common\Files sandbox.
        return {
            "install": str(info.path or ""),
            "data":    str(info.data_path or ""),
            "common":  str(info.commondata_path or ""),
        }
    finally:
        mt5.shutdown()


# ── Install routine ───────────────────────────────────────────────────────────

def _copy_if_newer(src: Path, dst: Path) -> bool:
    """Copy ``src`` to ``dst`` if ``dst`` is missing or older. Returns True on copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
        return False
    shutil.copy2(src, dst)
    return True


def _compile_one(metaeditor: Path, source: Path) -> tuple[bool, str]:
    """Invoke metaeditor64.exe to build a single .mq5. Returns (ok, log_tail)."""
    log_file = source.with_suffix(".log")
    proc = subprocess.run(
        [str(metaeditor), "/compile:" + str(source), "/log:" + str(log_file)],
        capture_output=True, text=True, timeout=120,
    )
    log_tail = ""
    if log_file.is_file():
        try:
            log_tail = log_file.read_text(encoding="utf-16", errors="replace")
        except OSError:
            pass
    return (proc.returncode == 0, log_tail.strip())


def ensure_installed() -> dict[str, Any]:
    """Install BD indicators + helper EA into the live MT5 terminal.

    Idempotent: re-running is cheap (mtime checks) and safe.

    Returns:
        {
          "mt5_paths":     {install, data, common},
          "indicators":    {"copied": [...], "skipped": [...]},
          "helper_ea":     {"copied": bool, "path": "..."},
          "compiled":      [{"name": "...", "ok": bool, "log": "..."}],
          "metaeditor":    "found" | "missing",
          "next_steps":    [str, ...],   # human-readable
        }
    """
    paths = _resolve_mt5_paths()
    install_dir = Path(paths["install"])
    data_dir    = Path(paths["data"])
    if not data_dir.is_dir():
        raise RuntimeError(f"MT5 data_path does not exist: {data_dir}")

    src_root  = _resolve_source_dir()
    ind_src   = src_root / "indicators"
    svc_src   = src_root / "services"
    ind_dst   = data_dir / "MQL5" / "Indicators" / _INSTALL_SUBDIR
    ea_dst    = data_dir / "MQL5" / "Experts"    / _INSTALL_SUBDIR

    if not ind_src.is_dir():
        raise RuntimeError(f"Bundled indicators source missing: {ind_src}")

    copied:  list[str] = []
    skipped: list[str] = []
    for stem in _INDICATOR_STEMS:
        src = ind_src / f"{stem}.mq5"
        if not src.is_file():
            raise RuntimeError(f"Missing bundled indicator source: {src}")
        dst = ind_dst / f"{stem}.mq5"
        if _copy_if_newer(src, dst):
            copied.append(stem)
        else:
            skipped.append(stem)

    helper_src = svc_src / f"{_HELPER_EA_STEM}.mq5"
    helper_dst = ea_dst / f"{_HELPER_EA_STEM}.mq5"
    helper_copied = False
    if helper_src.is_file():
        helper_copied = _copy_if_newer(helper_src, helper_dst)
    else:
        raise RuntimeError(f"Missing bundled helper EA source: {helper_src}")

    # Also install the one-shot validation dumper (BD_FeatureDump) so users
    # can run the indicator-drift harness without manual file copies.
    dumper_src = svc_src / f"{_DUMPER_EA_STEM}.mq5"
    dumper_dst = ea_dst / f"{_DUMPER_EA_STEM}.mq5"
    dumper_copied = False
    if dumper_src.is_file():
        dumper_copied = _copy_if_newer(dumper_src, dumper_dst)

    metaeditor = install_dir / "metaeditor64.exe"
    compiled: list[dict[str, Any]] = []
    if metaeditor.is_file():
        for stem in _INDICATOR_STEMS:
            ok, log = _compile_one(metaeditor, ind_dst / f"{stem}.mq5")
            compiled.append({"name": stem, "ok": ok, "log": log[-1500:]})
        ok, log = _compile_one(metaeditor, helper_dst)
        compiled.append({"name": _HELPER_EA_STEM, "ok": ok, "log": log[-1500:]})
        if dumper_dst.is_file():
            ok, log = _compile_one(metaeditor, dumper_dst)
            compiled.append({"name": _DUMPER_EA_STEM, "ok": ok, "log": log[-1500:]})
        me_state = "found"
    else:
        me_state = "missing"

    next_steps: list[str] = []
    if me_state == "missing":
        next_steps.append(
            "metaeditor64.exe was not found next to terminal64.exe — open "
            "MetaEditor in MT5 once (F4) so it compiles the newly-installed "
            "indicators and EA. Then return here and retry."
        )
    if helper_copied or any(c["name"] == _HELPER_EA_STEM and c["ok"] for c in compiled):
        next_steps.append(
            "In MT5: drag Experts/BetterDiscovery/BD_AutoSetup onto any chart "
            "ONCE, tick 'Allow algorithmic trading' in the dialog, and click OK. "
            "Leave it attached — BETTER DISCOVERY drives it through "
            "Common/Files/bd_setup.json."
        )

    return {
        "mt5_paths":  paths,
        "indicators": {"copied": copied, "skipped": skipped},
        "helper_ea":  {"copied": helper_copied, "path": str(helper_dst)},
        "compiled":   compiled,
        "metaeditor": me_state,
        "next_steps": next_steps,
    }


# ── Setup-JSON / ack protocol ─────────────────────────────────────────────────

def _common_files_dir() -> Path:
    paths = _resolve_mt5_paths()
    common = Path(paths["common"]) / "Files"
    common.mkdir(parents=True, exist_ok=True)
    return common


def apply_chart_setup(
    symbol: str,
    timeframes: list[str],
    indicators: list[str] | None = None,
    htf_for_div: str = "M15",
) -> dict[str, Any]:
    """Write ``bd_setup.json`` so the in-MT5 helper EA opens charts.

    Args:
        symbol:        Broker-side ticker, e.g. ``"XAUUSD"``.
        timeframes:    Labels like ``["M5", "M15", "H1"]``.
        indicators:    Optional whitelist — defaults to all 12.
        htf_for_div:   Timeframe label passed to ``BD_HtfDiv`` (default M15).

    Returns dict containing the version we wrote (used by ``wait_for_ack``).
    """
    common = _common_files_dir()
    cfg_file = common / "bd_setup.json"
    prev = 0
    if cfg_file.is_file():
        try:
            prev = int(json.loads(cfg_file.read_text(encoding="utf-8")).get("version", 0))
        except Exception:
            prev = 0
    new_version = prev + 1
    payload = {
        "version":     new_version,
        "symbol":      symbol,
        "timeframes":  list(timeframes),
        "indicators":  list(indicators or _INDICATOR_STEMS),
        "htf_for_div": htf_for_div,
    }
    cfg_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"version": new_version, "config_path": str(cfg_file)}


def wait_for_ack(version: int, timeout_s: float = 10.0) -> dict[str, Any]:
    """Poll ``bd_setup_ack.json`` until the helper EA echoes ``version``.

    Returns the parsed ack object, or raises ``TimeoutError``.
    The helper EA writes the file atomically (FILE_WRITE truncates), so a
    half-written read is not a concern in practice.
    """
    common = _common_files_dir()
    ack_file = common / "bd_setup_ack.json"
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        if ack_file.is_file():
            try:
                ack = json.loads(ack_file.read_text(encoding="utf-8"))
                if int(ack.get("version_acked", -1)) == version:
                    return ack
            except Exception as exc:
                last_err = str(exc)
        time.sleep(0.2)
    raise TimeoutError(
        f"BD_AutoSetup did not acknowledge version {version} within {timeout_s:.1f}s. "
        "Confirm the helper EA is attached to a chart with algo-trading enabled. "
        f"Last read error: {last_err}"
    )
