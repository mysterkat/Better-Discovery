"""Bridge to mc_funded_test.py.

Exposes the four phase runners and all 15 advanced-metric functions. Results
are passed through _jsonify so pandas/numpy objects become JSON-safe.

Phase 7 addition: for phase1/phase2, a small satellite simulation (N_CURVE_SIMS
paths) is run after the main stats pass so the frontend can render 3-D equity
surface and drawdown cone charts without shipping the full 10,000-path array.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .. import paths  # ensure toolkit is on sys.path  # noqa: F401

# Maximum equity-curve samples returned to the frontend.
N_CURVE_SIMS = 200

# ── Lazy imports ───────────────────────────────────────────────────────────────
# mc_funded_test pulls heavy deps (numpy, pandas, scipy, plotly, sklearn).
# Import lazily so a missing package doesn't crash the backend on startup.

_mc_module: Any = None


def _get_mc() -> Any:
    global _mc_module
    if _mc_module is not None:
        return _mc_module
    import mc_funded_test as _m  # type: ignore[import-not-found]
    _mc_module = _m
    return _mc_module


def _np():
    import numpy as _numpy
    return _numpy


def _pd():
    import pandas as _pandas
    return _pandas


@dataclass
class MCParamMeta:
    label: str
    group: str
    type: str          # "int" | "float" | "bool" | "str"
    description: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[str] = field(default_factory=list)


MC_PARAM_META: dict[str, MCParamMeta] = {
    # ── Simulation globals ───────────────────────────────────────────────────
    "N_SIMULATIONS":      MCParamMeta("Simulations",         "Simulation", "int",
                                       "Number of Monte Carlo paths", min=1000, max=1_000_000, step=1000),
    "N_DISPLAY_CURVES":   MCParamMeta("Display Curves",      "Simulation", "int",
                                       "Equity curves rendered in charts", min=50, max=1000, step=50),
    "RANDOM_SEED":        MCParamMeta("Random Seed",         "Simulation", "int",
                                       "Seed for reproducible runs", min=0),
    # ── Phase 1 ─────────────────────────────────────────────────────────────
    "P1_BALANCE":         MCParamMeta("Balance",             "Phase 1", "float",
                                       "Starting account size", min=1000.0),
    "P1_LEVERAGE":        MCParamMeta("Leverage",            "Phase 1", "float",
                                       "Trade-size multiplier vs backtest", min=0.1, max=10.0, step=0.1),
    "P1_PROFIT_TARGET":   MCParamMeta("Profit Target",       "Phase 1", "float",
                                       "Required profit % (e.g. 0.10 = 10%)", min=0.01, max=0.30, step=0.01),
    "P1_MAX_DAILY_DD":    MCParamMeta("Max Daily DD",        "Phase 1", "float",
                                       "Daily loss limit %", min=0.01, max=0.15, step=0.01),
    "P1_MAX_TOTAL_DD":    MCParamMeta("Max Total DD",        "Phase 1", "float",
                                       "Total drawdown floor %", min=0.05, max=0.30, step=0.01),
    "P1_MIN_DAYS":        MCParamMeta("Min Trading Days",    "Phase 1", "int",
                                       "Min days with at least 1 trade", min=1, max=30, step=1),
    "P1_MAX_SIM_DAYS":    MCParamMeta("Max Sim Days",        "Phase 1", "int",
                                       "Safety cap on simulation length", min=30, max=730, step=10),
    # ── Phase 2 ─────────────────────────────────────────────────────────────
    "P2_BALANCE":         MCParamMeta("Balance",             "Phase 2", "float", min=1000.0),
    "P2_LEVERAGE":        MCParamMeta("Leverage",            "Phase 2", "float", min=0.1, max=10.0, step=0.1),
    "P2_PROFIT_TARGET":   MCParamMeta("Profit Target",       "Phase 2", "float", min=0.01, max=0.20, step=0.01),
    "P2_MAX_DAILY_DD":    MCParamMeta("Max Daily DD",        "Phase 2", "float", min=0.01, max=0.15, step=0.01),
    "P2_MAX_TOTAL_DD":    MCParamMeta("Max Total DD",        "Phase 2", "float", min=0.05, max=0.30, step=0.01),
    "P2_MIN_DAYS":        MCParamMeta("Min Trading Days",    "Phase 2", "int", min=1, max=30, step=1),
    "P2_MAX_SIM_DAYS":    MCParamMeta("Max Sim Days",        "Phase 2", "int", min=30, max=730, step=10),
    # ── Funded ──────────────────────────────────────────────────────────────
    "FD_BALANCE":         MCParamMeta("Balance",             "Funded", "float", min=1000.0),
    "FD_LEVERAGE":        MCParamMeta("Leverage",            "Funded", "float", min=0.1, max=10.0, step=0.1),
    "FD_MAX_DAILY_DD":    MCParamMeta("Max Daily DD",        "Funded", "float", min=0.01, max=0.15, step=0.01),
    "FD_MAX_TOTAL_DD":    MCParamMeta("Max Total DD",        "Funded", "float", min=0.05, max=0.30, step=0.01),
    "FD_PROFIT_SPLIT":    MCParamMeta("Profit Split",        "Funded", "float",
                                       "Trader's share of profits (e.g. 0.80 = 80%)", min=0.5, max=1.0, step=0.05),
    "FD_PAYOUT_MODE":     MCParamMeta("Payout Mode",         "Funded", "str",
                                       "threshold | schedule | both",
                                       options=["threshold", "schedule", "both"]),
    "FD_PAYOUT_THRESHOLD":MCParamMeta("Payout Threshold",   "Funded", "float",
                                       "Pay when profit ≥ this %", min=0.01, max=0.20, step=0.01),
    "FD_PAYOUT_SCHEDULE": MCParamMeta("Payout Schedule",    "Funded", "int",
                                       "Pay every N trading days", min=1, max=90, step=1),
    "FD_MIN_DAYS_PAYOUT": MCParamMeta("Min Days per Cycle", "Funded", "int",
                                       "Min trading days per payout cycle", min=1, max=60, step=1),
    "FD_BALANCE_RESET":   MCParamMeta("Balance Reset",      "Funded", "bool",
                                       "Reset balance to start after each payout"),
    "FD_MAX_SIM_DAYS":    MCParamMeta("Max Sim Days",       "Funded", "int", min=30, max=1000, step=10),
    # ── Long-term ───────────────────────────────────────────────────────────
    "LT_DAYS":            MCParamMeta("Sim Days",           "Long-term", "int",
                                       "Simulation horizon in trading days", min=30, max=2520, step=21),
    "LT_SIMS":            MCParamMeta("Simulations",        "Long-term", "int",
                                       "Paths for the long-term run", min=1000, max=1_000_000, step=1000),
    "LT_RUIN_PCT":        MCParamMeta("Ruin Level",         "Long-term", "float",
                                       "Account considered ruined below this drawdown %", min=0.05, max=0.99, step=0.01),
    "LT_BENCHMARK_TICKER":MCParamMeta("Benchmark Ticker",  "Long-term", "str",
                                       "Yahoo Finance ticker for comparison (e.g. ^GSPC). Leave blank to skip."),
}

MC_OVERRIDABLE: set[str] = set(MC_PARAM_META.keys())


def _mc_jsonify_val(val: Any) -> Any:
    if isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (list, tuple, set)):
        return list(val)
    return str(val)


def list_mc_defaults() -> dict[str, Any]:
    """Return current mc_funded_test module-level defaults."""
    try:
        mc = _get_mc()
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for name in MC_OVERRIDABLE:
        if hasattr(mc, name):
            out[name] = _mc_jsonify_val(getattr(mc, name))
    return out


def list_mc_defaults_with_meta() -> list[dict[str, Any]]:
    """Return values + UI rendering metadata for every MC overridable constant."""
    try:
        mc = _get_mc()
    except Exception:
        mc = None
    result: list[dict[str, Any]] = []
    for name, meta in MC_PARAM_META.items():
        val = (_mc_jsonify_val(getattr(mc, name)) if (mc is not None and hasattr(mc, name)) else None)
        entry: dict[str, Any] = {
            "key": name,
            "value": val,
            "label": meta.label,
            "group": meta.group,
            "type": meta.type,
            "description": meta.description,
        }
        if meta.min is not None:
            entry["min"] = meta.min
        if meta.max is not None:
            entry["max"] = meta.max
        if meta.step is not None:
            entry["step"] = meta.step
        if meta.options:
            entry["options"] = meta.options
        result.append(entry)
    return result

_PHASE_RUNNER_NAMES = ("run_mc_phase1", "run_mc_phase2", "run_mc_funded", "run_mc_longterm")
_PHASE_KEYS         = ("phase1",        "phase2",        "funded",        "longterm")

_ADVANCED_METRIC_NAMES = (
    "failure_mode_breakdown", "time_to_pass_distribution", "lot_size_sweep",
    "recovery_probability", "worst_streak_check", "conditional_phase2_pass_rate",
    "conservative_mode_simulator", "phase2_time_to_pass", "time_to_first_payout",
    "payout_cadence_optimizer", "funded_lifetime", "kelly_fraction",
    "risk_of_ruin_horizons", "multi_strategy_portfolio", "fat_tail_stress",
)

# These properties are populated lazily on first call via _get_phase_runners()
# and _get_advanced_metrics() so a bad import doesn't crash the server.
PHASE_RUNNERS:    dict[str, Callable[..., dict]] = {}
ADVANCED_METRICS: dict[str, Callable[..., Any]]  = {}


def _ensure_runners() -> None:
    """Populate PHASE_RUNNERS and ADVANCED_METRICS on first successful import."""
    if PHASE_RUNNERS:
        return
    mc = _get_mc()
    for key, fn_name in zip(_PHASE_KEYS, _PHASE_RUNNER_NAMES):
        PHASE_RUNNERS[key] = getattr(mc, fn_name)
    for fn_name in _ADVANCED_METRIC_NAMES:
        ADVANCED_METRICS[fn_name] = getattr(mc, fn_name)


def load_daily_pnl(data_source: str, file_path: str) -> Any:
    """Load a trade file and return a daily P&L numpy array.

    data_source: "tradingview" → CSV export, "mt5_html" → MT5 Strategy Tester HTML report.
    """
    mc = _get_mc()
    np = _np()
    if data_source == "mt5_html":
        df = mc.load_mt5_html(file_path)
    else:
        df = mc.load_tradingview_csv(file_path)
    return np.asarray(mc.get_daily_pnl(df, scale=1.0), dtype=float)


def run_all_phases(
    daily_pnl: Any,
    global_params: dict[str, Any],
    phase1_params: dict[str, Any],
    phase2_params: dict[str, Any],
    funded_params: dict[str, Any],
    longterm_params: dict[str, Any],
) -> dict[str, Any]:
    """Run all four phases in one job, sharing a single pre-drawn sample matrix.

    Pre-draws n_sims × max_days bootstrap samples once; passes the same array
    to all four runners so they consume identical random paths — true reuse.
    """
    mc = _get_mc()
    _ensure_runners()
    n_sims   = int(global_params.get("n_sims", 10_000))
    max_days = max(
        int(phase1_params.get("max_days", 365)),
        int(phase2_params.get("max_days", 365)),
        int(funded_params.get("months", 12)) * 21 + 10,
        int(longterm_params.get("years", 5)) * 252 + 10,
    )
    seed = global_params.get("seed")
    rng  = mc._make_rng(seed)
    predrawn = rng.choice(daily_pnl, size=(n_sims, max_days), replace=True).astype(float)

    p1 = _jsonify(mc.run_mc_phase1(daily_pnl, n_sims=n_sims, predrawn_pnl=predrawn,
                                    **{k: v for k, v in phase1_params.items() if k not in ("n_sims",)}))
    p2 = _jsonify(mc.run_mc_phase2(daily_pnl, n_sims=n_sims, predrawn_pnl=predrawn,
                                    **{k: v for k, v in phase2_params.items() if k not in ("n_sims",)}))
    fd = _jsonify(mc.run_mc_funded(daily_pnl, n_sims=n_sims, predrawn_pnl=predrawn,
                                    **{k: v for k, v in funded_params.items() if k not in ("n_sims",)}))
    lt = _jsonify(mc.run_mc_longterm(daily_pnl, n_sims=n_sims, predrawn_pnl=predrawn,
                                      **{k: v for k, v in longterm_params.items() if k not in ("n_sims",)}))

    # Attach equity-curve samples for phase1 + phase2.
    for phase_key, phase_label, params_dict in (("phase1", "phase1", phase1_params),
                                                 ("phase2", "phase2", phase2_params)):
        try:
            curves = _sample_equity_curves(phase_label, daily_pnl, params_dict, N_CURVE_SIMS)
            (p1 if phase_key == "phase1" else p2)["equity_curves"] = curves
        except Exception:
            pass

    return {"phase1": p1, "phase2": p2, "funded": fd, "longterm": lt}


def run_phase(phase: str, daily_pnl: Any, params: dict[str, Any]) -> dict[str, Any]:
    _ensure_runners()
    if phase not in PHASE_RUNNERS:
        raise ValueError(f"unknown phase '{phase}'; expected one of {list(PHASE_RUNNERS)}")
    fn = PHASE_RUNNERS[phase]
    stats = _jsonify(fn(daily_pnl, **params))

    # Add equity-curve samples for phases that run the evaluation phase loop.
    if phase in ("phase1", "phase2"):
        try:
            curves = _sample_equity_curves(phase, daily_pnl, params, N_CURVE_SIMS)
            stats["equity_curves"] = curves
        except Exception:
            pass  # charts degrade gracefully if curves are missing

    return stats


def _sample_equity_curves(
    phase: str,
    daily_pnl: Any,
    params: dict[str, Any],
    n_curves: int,
) -> list[list[float]]:
    mc = _get_mc()
    rng = mc._make_rng(params.get("seed"))
    balance      = float(params.get("balance", 100_000))
    daily_dd_pct = float(params.get("daily_dd_pct", 0.05))
    total_dd_pct = float(params.get("total_dd_pct", 0.10))
    min_days     = int(params.get("min_days", 4))
    max_days     = int(params.get("max_days", 60))
    profit_pct   = float(params.get("profit_pct",
                                    0.10 if phase == "phase1" else 0.05))

    _, raw_curves = mc.run_eval_phase(
        daily_pnl, balance, profit_pct,
        daily_dd_pct, total_dd_pct,
        min_days, max_days, rng, n_curves,
        phase_label="",
    )
    padded = mc.pad_curves(raw_curves)
    return (padded / balance * 100).tolist()


def run_advanced(metric: str, params: dict[str, Any]) -> Any:
    _ensure_runners()
    if metric not in ADVANCED_METRICS:
        raise ValueError(f"unknown metric '{metric}'; expected one of {list(ADVANCED_METRICS)}")
    fn = ADVANCED_METRICS[metric]
    # Pull the PnL arg out of params so advanced callers can pass it by name.
    # Some advanced functions take daily_pnl_list or daily_pnl_per_lot_unit.
    return _jsonify(fn(**params))


def _jsonify(obj: Any) -> Any:
    """Recursively convert numpy/pandas objects to JSON-safe types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    # Lazy numpy/pandas type checks — only import if we actually have an object to check.
    try:
        import numpy as np
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            return {"columns": list(obj.columns), "records": obj.to_dict(orient="records")}
        if isinstance(obj, pd.Series):
            return obj.tolist()
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {str(k): _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonify(x) for x in obj]
    return str(obj)
