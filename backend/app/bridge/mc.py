"""Bridge to mc_funded_test.py.

Exposes the four phase runners and all 15 advanced-metric functions. Results
are passed through _jsonify so pandas/numpy objects become JSON-safe.

Equity-curve sampling is now done INSIDE the runners (via ``keep_curves``)
rather than re-running a satellite simulation here, so the curves shown in
the dashboard are guaranteed to come from the same RNG paths as the headline
stats. The earlier ``_sample_equity_curves`` / ``_sample_funded_curves``
helpers were removed in favour of this single-pass design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .. import paths  # ensure toolkit is on sys.path  # noqa: F401
from ..jobs.runners import get_current_job

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
    "INTRADAY_DD_FACTOR": MCParamMeta("Intraday DD Factor",  "Simulation", "float",
                                       "Tightens DD limits to approximate intraday floating "
                                       "drawdown risk. 1.0 = end-of-day only (sim default), "
                                       "0.7 = treat 5% daily limit as ~3.5% effective. "
                                       "Lower = more conservative.",
                                       min=0.1, max=1.0, step=0.05),
    "BOOTSTRAP_BLOCK_SIZE": MCParamMeta("Bootstrap Block Size", "Simulation", "int",
                                       "Sample N-day blocks instead of single days to "
                                       "preserve autocorrelation. 1 = i.i.d. bootstrap "
                                       "(default). 5 = preserves week-long streaks.",
                                       min=1, max=30, step=1),
    "CHALLENGE_FEE":      MCParamMeta("Challenge Fee",       "Simulation", "float",
                                       "Cost of the evaluation account in $.",
                                       min=0, max=5000, step=10),
    "FEE_REFUNDED_ON_FIRST_PAYOUT": MCParamMeta(
                                       "Fee Refunded on First Payout", "Simulation", "bool",
                                       "Most prop firms refund the challenge fee on the first payout."),
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
    "FD_COMPOUND_PROFITS":MCParamMeta("Compound Profits",   "Funded", "bool",
                                       "v0.6.0: leave the trader's share in the account (scaling-account model). "
                                       "Overrides Balance Reset when on. Floor ratchets up to a fresh % "
                                       "of the new equity. Default OFF."),
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

# ── Param-name maps (UI key → runner kwarg). ──────────────────────────────
# The MC_PARAM_META registry uses verbose UI keys (P1_BALANCE, FD_PAYOUT_MODE,
# …). The actual runner functions in mc_funded_test.py take terser kwargs
# (balance, payout_mode, …). The frontend lowercases meta keys before sending
# (e.g. "fd_payout_mode"), so we map lowercased ⇒ runner kwarg here.
# Value of None means "explicitly unsupported by the runner — drop silently".
_EVAL_KEY_MAP = {
    "balance":          "balance",
    "leverage":         None,
    "profit_target":    "profit_pct",
    "max_daily_dd":     "daily_dd_pct",
    "max_total_dd":     "total_dd_pct",
    "min_days":         "min_days",
    "max_sim_days":     "max_days",
}
_FD_KEY_MAP = {
    "balance":          "balance",
    "leverage":         None,
    "max_daily_dd":     "daily_dd_pct",
    "max_total_dd":     "total_dd_pct",
    "profit_split":     "profit_split",
    "payout_mode":      "payout_mode",
    "payout_threshold": "payout_threshold",
    "payout_schedule":  "payout_cadence_days",
    "min_days_payout":  "min_days_payout",
    "balance_reset":    "balance_reset",
    "compound_profits": "compound_profits",
    "max_sim_days":     "max_days",
}
_LT_KEY_MAP = {
    "days":             "n_days",
    "sims":             "n_sims",
    "ruin_pct":         "ruin_pct",
    "benchmark_ticker": "benchmark_ticker",
}


def _normalize_params(prefix: str, params: dict[str, Any], key_map: dict[str, str | None]) -> dict[str, Any]:
    """Normalize a UI param dict for consumption by an mc_funded_test runner.

    Strips the phase prefix (e.g. "fd_") off keys, then maps remaining keys
    via ``key_map``. Keys that map to ``None`` or aren't in the map are dropped
    silently — that's intentional so the meta registry can carry "display only"
    options without crashing the runner.
    """
    out: dict[str, Any] = {}
    for k, v in (params or {}).items():
        kl = k.lower()
        if kl.startswith(prefix):
            kl = kl[len(prefix):]
        target = key_map.get(kl)
        if target is None:
            continue
        out[target] = v
    return out

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
    np = _np()
    if data_source == "local_ledger":
        pd = _pd()
        frame = pd.read_parquet(file_path) if str(file_path).lower().endswith(".parquet") else pd.read_csv(file_path)
        required = {"exit_time", "net_pnl"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"local replay ledger missing columns: {sorted(missing)}")
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
        frame["net_pnl"] = pd.to_numeric(frame["net_pnl"], errors="coerce")
        daily = frame.dropna(subset=["exit_time", "net_pnl"]).groupby(frame["exit_time"].dt.date)["net_pnl"].sum()
        return np.asarray(daily, dtype=float)
    mc = _get_mc()
    if data_source == "mt5_html":
        df = mc.load_mt5_html(file_path)
    else:
        df = mc.load_tradingview_csv(file_path)
    return np.asarray(mc.get_daily_pnl(df, scale=1.0), dtype=float)


def compute_regime_from_file(data_source: str, file_path: str) -> dict[str, Any] | None:
    """Load a trade file and compute the Markov regime transition matrix + per-regime P&L pools.

    Returns ``None`` when the trades have no parseable regime markers (e.g. a
    plain TradingView CSV without ``R:<n>`` comments). The dashboard renders
    the regime heatmap only when this returns non-None.

    The pools are used by the runners for regime-conditional bootstrap sampling.
    """
    try:
        if data_source == "local_ledger":
            return _compute_local_regime(file_path)
        mc = _get_mc()
        if data_source == "mt5_html":
            df = mc.load_mt5_html(file_path)
        else:
            df = mc.load_tradingview_csv(file_path)
        trans_matrix, stationary_dist, _ = mc.compute_regime_transitions(df)
        if trans_matrix is None or stationary_dist is None:
            return None
        # Build per-regime daily P&L pools so runners can sample conditionally.
        try:
            _, regime_pnl_pools = mc._build_regime_daily(df, scale=1.0)
        except Exception:
            regime_pnl_pools = None
        # Fix 1.6 — sanitise rows. ``rng.choice(p=row)`` crashes mid-sim if
        # any row sums to 0 or contains NaN; replace such rows with a uniform
        # 5-way prior so the Markov chain stays well-formed.
        import math
        sanitized: list[list[float]] = []
        for row in trans_matrix:
            try:
                vals = [float(v) for v in row]
            except (TypeError, ValueError):
                vals = [float("nan")] * 5
            row_sum = sum(v for v in vals if not math.isnan(v))
            if row_sum <= 0 or any(math.isnan(v) for v in vals) or len(vals) != 5:
                vals = [0.2, 0.2, 0.2, 0.2, 0.2]
            sanitized.append(vals)
        result: dict[str, Any] = {
            "trans_matrix":    sanitized,
            "stationary_dist": [float(v) for v in stationary_dist],
            "labels":          list(mc.REGIME_LABELS),
        }
        if regime_pnl_pools:
            # JSON-safe nested floats; keys coerced to str so _jsonify is happy.
            result["regime_pnl_pools"] = {
                str(k): [float(v) for v in pool] for k, pool in regime_pnl_pools.items()
            }
        return result
    except Exception:
        return None


def _compute_local_regime(file_path: str) -> dict[str, Any] | None:
    pd = _pd()
    np = _np()
    frame = pd.read_parquet(file_path) if str(file_path).lower().endswith(".parquet") else pd.read_csv(file_path)
    if not {"regime", "exit_time", "net_pnl"}.issubset(frame.columns) or frame.empty:
        return None
    frame["regime"] = pd.to_numeric(frame["regime"], errors="coerce").fillna(4).clip(0, 4).astype(int)
    counts = np.zeros((5, 5), dtype=float)
    values = frame["regime"].to_numpy()
    for previous, current in zip(values[:-1], values[1:]):
        counts[previous, current] += 1
    for row in range(5):
        counts[row] = counts[row] / counts[row].sum() if counts[row].sum() else np.full(5, 0.2)
    stationary = np.bincount(values, minlength=5).astype(float)
    stationary = stationary / stationary.sum()
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
    pools: dict[str, list[float]] = {}
    for regime, group in frame.dropna(subset=["exit_time"]).groupby("regime"):
        daily = group.groupby(group["exit_time"].dt.date)["net_pnl"].sum()
        pools[str(int(regime))] = [float(value) for value in daily]
    return {
        "trans_matrix": counts.tolist(), "stationary_dist": stationary.tolist(),
        "labels": ["TrendUp", "TrendDn", "Squeeze", "VolatileRange", "Choppy"],
        "regime_pnl_pools": pools,
    }


def _build_predrawn(
    mc: Any,
    daily_pnl: Any,
    n_sims: int,
    max_days: int,
    rng: Any,
    block_size: int,
) -> Any:
    """Pre-draw an ``(n_sims, max_days)`` bootstrap matrix in float32.

    When ``block_size > 1`` we sample N-day BLOCKS of consecutive trades to
    preserve autocorrelation (Politis-Romano moving-block bootstrap, simplified).
    Block 1 is the i.i.d. fallback. Always returns a contiguous float32 array.
    """
    import numpy as _np
    arr = _np.asarray(daily_pnl, dtype=_np.float32)
    n   = len(arr)
    block_size = max(1, int(block_size))
    if block_size == 1 or n <= block_size:
        return rng.choice(arr, size=(n_sims, max_days), replace=True).astype(_np.float32)
    n_blocks = max_days // block_size + 1
    starts   = rng.integers(0, n - block_size + 1, size=(n_sims, n_blocks))
    out      = _np.empty((n_sims, n_blocks * block_size), dtype=_np.float32)
    for b in range(block_size):
        out[:, b::block_size] = arr[starts + b]
    return out[:, :max_days]


def _wilson_ci(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% CI for a binomial proportion. Returns (low, high) as 0..1 floats."""
    if total <= 0:
        return (0.0, 0.0)
    p_hat = passed / total
    z2    = z * z
    denom = 1.0 + z2 / total
    center = p_hat + z2 / (2.0 * total)
    half   = z * ((p_hat * (1.0 - p_hat) / total + z2 / (4.0 * total * total)) ** 0.5)
    low    = max(0.0, (center - half) / denom)
    high   = min(1.0, (center + half) / denom)
    return (low, high)


def _sweep_worker(fn_name: str, args: tuple, kwargs: dict):
    """Worker entry point for parallel sweep helpers (v0.6.0).

    Must be a top-level function (picklable) so ProcessPoolExecutor can spawn
    it. Re-imports mc_funded_test on first call per worker — the embedded
    Python sidecar's sys.path already includes ``backend/toolkit`` (set up by
    ``app.paths``), so the import resolves the same module the main process
    uses. Returns the raw helper output; the parent jsonifies.
    """
    # Re-add the toolkit dir defensively in case the worker forked before
    # app.paths had a chance to run (only matters on Windows spawn mode).
    import sys, os
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    toolkit = os.path.join(here, "toolkit")
    if os.path.isdir(toolkit) and toolkit not in sys.path:
        sys.path.insert(0, toolkit)
    import mc_funded_test as _mc
    fn = getattr(_mc, fn_name)
    return fn(*args, **kwargs)


def _kelly_verdict(k: float) -> str:
    if k <= 0:
        return "negative"
    if k < 0.25:
        return "underleveraged"
    if k < 0.75:
        return "near optimal"
    if k < 1.5:
        return "aggressive"
    return "very aggressive"


def _dominant_fail(stats: dict[str, Any]) -> tuple[str, float]:
    """Pick the largest fail bucket. Returns ("none", 0.0) if no failures."""
    pcts = stats.get("fail_pcts") or {}
    if not pcts:
        return ("none", 0.0)
    name, pct = max(pcts.items(), key=lambda kv: float(kv[1] or 0.0))
    return (str(name), float(pct or 0.0))


def run_all_phases(
    daily_pnl: Any,
    global_params: dict[str, Any],
    phase1_params: dict[str, Any],
    phase2_params: dict[str, Any],
    funded_params: dict[str, Any],
    longterm_params: dict[str, Any],
    regime_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run all four phases in one job.

    With Markov regime data: each runner samples its own paths via the regime
    chain (no shared predraw — Fix 1.1 — so the runners actually exercise the
    Markov branch instead of being shadowed by ``predrawn_pnl``).

    Without regime data: per-phase predraw matrices sized to that phase's own
    ``max_days`` are built in float32 (Fix 2.5), optionally with N-day blocks
    (Fix 4.5) for autocorrelation preservation. Skipping the giant global
    matrix in regime mode is intentional — it would never be consumed.
    """
    mc = _get_mc()
    _ensure_runners()
    import numpy as _np  # local to avoid mandatory module-level dep

    n_sims   = int(global_params.get("n_sims", 10_000))

    # Normalize UI keys → runner kwargs. Drops unsupported (e.g. P1_LEVERAGE)
    # keys silently so the runner sees only what it can consume.
    p1_kwargs = _normalize_params("p1_", phase1_params, _EVAL_KEY_MAP)
    p2_kwargs = _normalize_params("p2_", phase2_params, _EVAL_KEY_MAP)
    fd_kwargs = _normalize_params("fd_", funded_params, _FD_KEY_MAP)
    lt_kwargs = _normalize_params("lt_", longterm_params, _LT_KEY_MAP)

    # Funded runner uses ``max_days`` rather than ``months`` when supplied.
    fd_max_days = int(fd_kwargs.get("max_days") or 12 * 21)
    p1_max_days = int(p1_kwargs.get("max_days", 365))
    p2_max_days = int(p2_kwargs.get("max_days", 365))
    lt_n_days   = int(lt_kwargs.get("n_days", 252 * 5))

    seed = global_params.get("seed")
    rng  = mc._make_rng(seed)

    # Long-term may override n_sims via LT_SIMS — pop so we don't double-pass.
    lt_n_sims = int(lt_kwargs.pop("n_sims", n_sims))

    # Global intraday DD safety factor (1.0 = end-of-day only, <1.0 tightens limits).
    intraday_factor = float(global_params.get("intraday_dd_factor", 1.0))
    block_size      = int(global_params.get("bootstrap_block_size", 1))

    # ── Regime-conditional sampling (Fix 1.1) ─────────────────────────────────
    # When regime data is present, the runners' Markov branch only fires if
    # ``predrawn_pnl is None``. We honour that by NOT building any predraw at
    # all and NOT passing the kwarg, so the Markov sampler runs as designed.
    # Trade-off: regime mode no longer benefits from the shared-RNG variance
    # reduction across phases. That's intentional — Markov's path-dependent
    # state would be broken by reusing a flat matrix anyway.
    extra: dict[str, Any] = {"intraday_dd_factor": intraday_factor}
    use_regime = bool(regime_data and regime_data.get("trans_matrix") is not None)
    if use_regime:
        try:
            extra["trans_matrix"] = _np.asarray(regime_data["trans_matrix"], dtype=float)
            pools = regime_data.get("regime_pnl_pools") or {}
            extra["regime_pnl_pools"] = {int(k): list(v) for k, v in pools.items()}
        except Exception:
            use_regime = False
            extra.pop("trans_matrix", None)
            extra.pop("regime_pnl_pools", None)

    # Per-phase predraws (Fix 2.5). Skipped entirely under regime mode.
    if use_regime:
        pre_p1 = pre_p2 = pre_fd = pre_lt = None
    else:
        pre_p1 = _build_predrawn(mc, daily_pnl, n_sims, p1_max_days, rng, block_size)
        pre_p2 = _build_predrawn(mc, daily_pnl, n_sims, p2_max_days, rng, block_size)
        pre_fd = _build_predrawn(mc, daily_pnl, n_sims, fd_max_days, rng, block_size)
        pre_lt = _build_predrawn(mc, daily_pnl, lt_n_sims, lt_n_days, rng, block_size)

    # Curve count + extra eval kwargs (consistency / dd_style). The toolkit
    # tolerates **kwargs flexibility, so it's safe to pass these even if a
    # given runner version doesn't yet recognise them — but we keep the call
    # sites explicit so future signature drift fails loudly.
    eval_extra: dict[str, Any] = {"keep_curves": N_CURVE_SIMS}
    fd_extra:   dict[str, Any] = {"keep_curves": N_CURVE_SIMS}
    if (dd_style := global_params.get("dd_style")):
        eval_extra["dd_style"] = str(dd_style)
        fd_extra["dd_style"]   = str(dd_style)
    if (cmax := global_params.get("consistency_max_daily_pct")) is not None:
        eval_extra["consistency_max_daily_pct"] = float(cmax)
        fd_extra["consistency_max_daily_pct"]   = float(cmax)
    if (mdfp := global_params.get("min_days_first_payout")):
        fd_extra["min_days_first_payout"] = int(mdfp)

    def _safe_call(fn: Callable[..., Any], **kwargs: Any) -> dict[str, Any]:
        """Call a runner; if it rejects a Tier-3 kwarg, drop it and retry once."""
        try:
            return fn(**kwargs)
        except TypeError:
            # Strip the new optional kwargs (toolkit may not yet accept them)
            for opt in ("keep_curves", "dd_style", "consistency_max_daily_pct",
                        "min_days_first_payout"):
                kwargs.pop(opt, None)
            return fn(**kwargs)

    def _notify_phase(label: str, frac: float) -> None:
        """Update sidecar progress so the UI doesn't appear to stall between phases."""
        job = get_current_job()
        if job is None:
            return
        job.stage_name = label
        job.progress   = max(0.0, min(1.0, frac))

    _notify_phase("Running Phase 1 (Challenge)…", 0.05)
    p1 = _jsonify(_safe_call(mc.run_mc_phase1, daily_pnl=daily_pnl, n_sims=n_sims,
                             predrawn_pnl=pre_p1, **p1_kwargs, **extra, **eval_extra))

    # Phase 2 only runs on the sims that survived Phase 1 — that's the real
    # funnel ("of the X who passed P1, how many also pass P2?"). Falls back
    # to a minimum floor so n_sims=0 doesn't crash the runner.
    n_p1_passed = int(p1.get("n_passed", 0))
    p2_n_sims   = max(n_p1_passed, 1)
    # Slice the Phase-2 predraw to match the reduced sim count (avoids wasted
    # rows + keeps RNG paths aligned). When predraw is None (Markov mode), this
    # is a no-op.
    pre_p2_sliced = pre_p2[:p2_n_sims] if (pre_p2 is not None and len(pre_p2) > p2_n_sims) else pre_p2
    _notify_phase("Running Phase 2 (Verification)…", 0.35)
    p2 = _jsonify(_safe_call(mc.run_mc_phase2, daily_pnl=daily_pnl, n_sims=p2_n_sims,
                             predrawn_pnl=pre_p2_sliced, **p2_kwargs, **extra, **eval_extra))

    _notify_phase("Running Funded simulation…", 0.60)
    fd = _jsonify(_safe_call(mc.run_mc_funded, daily_pnl=daily_pnl, n_sims=n_sims,
                             predrawn_pnl=pre_fd, **fd_kwargs, **extra, **fd_extra))
    _notify_phase("Running Long-term projection…", 0.82)
    lt = _jsonify(_safe_call(mc.run_mc_longterm, daily_pnl=daily_pnl, n_sims=lt_n_sims,
                             predrawn_pnl=pre_lt, **lt_kwargs, **extra))
    _notify_phase("Building verdict…", 0.95)

    # Echo the parameter dicts back so the dashboard can render KPI tables
    # without round-tripping to /mc/params.
    fd_balance = float(fd_kwargs.get("balance", 100_000))
    fd["balance"]              = fd_balance
    fd["daily_dd_pct"]         = float(fd_kwargs.get("daily_dd_pct", 0.05))
    fd["total_dd_pct"]         = float(fd_kwargs.get("total_dd_pct", 0.10))
    fd["max_days"]             = int(fd_kwargs.get("max_days", 252))
    fd["payout_mode"]          = str(fd_kwargs.get("payout_mode", "schedule"))
    fd["payout_threshold"]     = float(fd_kwargs.get("payout_threshold", 0.05))
    fd["payout_cadence_days"]  = int(fd_kwargs.get("payout_cadence_days", 30))
    fd["profit_split"]         = float(fd_kwargs.get("profit_split", 0.80))
    fd["balance_reset"]        = bool(fd_kwargs.get("balance_reset", True))
    fd["compound_profits"]     = bool(fd_kwargs.get("compound_profits", False))

    # ── Earnings-per-payout enrichment (#5 + #6 — v0.4.0.1) ────────────────
    # avg_earnings_per_payout: sum(total_earnings) / sum(payout_count) across
    #   sims, ignoring sims that never paid out. Better than avg/avg because
    #   it weights by activity, not by sim count.
    # earnings_by_payout_count: list of {payout_count, mean_earnings, count}
    #   used to overlay a per-bucket average marker on the scatter chart.
    try:
        df_records = (fd.get("results_df") or {}).get("records") or []
        total_paid    = 0.0
        total_payouts = 0
        bucket: dict[int, list[float]] = {}
        for r in df_records:
            pc = int(r.get("payout_count") or 0)
            te = float(r.get("total_earnings") or 0.0)
            if pc > 0:
                total_paid    += te
                total_payouts += pc
            bucket.setdefault(pc, []).append(te)
        fd["avg_earnings_per_payout"] = (
            float(total_paid / total_payouts) if total_payouts > 0 else 0.0
        )
        fd["earnings_by_payout_count"] = sorted(
            [
                {
                    "payout_count": int(k),
                    "mean_earnings": float(sum(v) / len(v)) if v else 0.0,
                    "count": len(v),
                }
                for k, v in bucket.items()
            ],
            key=lambda d: d["payout_count"],
        )
    except Exception:
        fd["avg_earnings_per_payout"]   = 0.0
        fd["earnings_by_payout_count"]  = []

    p1["balance"]      = float(p1_kwargs.get("balance", 100_000))
    p1["profit_pct"]   = float(p1_kwargs.get("profit_pct", 0.10))
    p1["daily_dd_pct"] = float(p1_kwargs.get("daily_dd_pct", 0.05))
    p1["total_dd_pct"] = float(p1_kwargs.get("total_dd_pct", 0.10))
    p1["min_days"]     = int(p1_kwargs.get("min_days", 4))

    p2["balance"]      = float(p2_kwargs.get("balance", 100_000))
    p2["profit_pct"]   = float(p2_kwargs.get("profit_pct", 0.05))
    p2["daily_dd_pct"] = float(p2_kwargs.get("daily_dd_pct", 0.05))
    p2["total_dd_pct"] = float(p2_kwargs.get("total_dd_pct", 0.10))
    p2["min_days"]     = int(p2_kwargs.get("min_days", 4))
    # Funnel: how many P1 sims passed (== n_sims input to P2).
    p2["n_p1_passed"]  = int(p1.get("n_passed", 0))
    n_total = int(p1.get("n_passed", 0) + p1.get("n_failed", 0))
    if n_total:
        combined = (int(p2.get("n_passed", 0)) / n_total) * 100.0
        p2["combined_pass_rate"] = combined

    lt_balance = float(lt_kwargs.get("balance", 100_000))

    result: dict[str, Any] = {
        "phase1":   p1,
        "phase2":   p2,
        "funded":   fd,
        "longterm": lt,
        "regime":   regime_data,
    }

    # ── Tier 3 helpers (each isolated; failure must not poison the whole run) ──
    # Cap sweep n_sims at 1000 — for the secondary charts (decision aids), the
    # confidence loss vs a 10k run is negligible.
    SWEEP_N_SIMS = min(n_sims, 1000)

    p1_subset = {k: v for k, v in p1_kwargs.items()
                 if k in ("balance", "profit_pct", "daily_dd_pct",
                          "total_dd_pct", "min_days", "max_days")}
    p1_subset.setdefault("n_sims", SWEEP_N_SIMS)
    p1_subset.setdefault("seed", seed)

    # v0.6.0: parallelize the 4 heavy sweeps via ProcessPoolExecutor. Kelly is
    # analytic (~ms) so we keep it inline. Each task is a (name, callable,
    # kwargs) tuple. The workers DON'T need to share state — each sweep is
    # fully self-contained given daily_pnl + its kwargs. Cap at 4 workers
    # because there are 4 sweeps to run; more workers wouldn't help.
    sweep_tasks: list[tuple[str, dict]] = [
        ("lot_sweep", {
            "fn":   "lot_size_sweep",
            "args": (daily_pnl,),
            "kwargs": dict(base_lot=1.0, lots=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
                            **p1_subset),
        }),
        ("payout_cadence_sweep", {
            "fn":   "payout_cadence_optimizer",
            "args": (daily_pnl,),
            "kwargs": dict(balance=fd_balance, cadences=(14, 30, 60),
                            months=12, n_sims=SWEEP_N_SIMS, seed=seed),
        }),
        ("ruin_horizons", {
            "fn":   "risk_of_ruin_horizons",
            "args": (daily_pnl,),
            "kwargs": dict(balance=lt_balance,
                            horizons_days=(30, 90, 180, 365, 730, 1825),
                            ruin_dd_pct=float(lt_kwargs.get("ruin_pct", 0.20)),
                            n_sims=min(SWEEP_N_SIMS, lt_n_sims), seed=seed),
        }),
        ("funded_lifetime", {
            "fn":   "funded_lifetime",
            "args": (daily_pnl,),
            "kwargs": dict(balance=fd_balance, max_months=36,
                            n_sims=SWEEP_N_SIMS, seed=seed),
        }),
    ]

    # Kelly first (analytic, no point parallelizing).
    print("[SWEEP] kelly_fraction (analytic)", flush=True)
    try:
        result["kelly"] = _jsonify(mc.kelly_fraction(daily_pnl))
    except Exception as e:
        result["kelly"] = {"error": str(e)}

    print(f"[SWEEP] launching {len(sweep_tasks)} parallel sweeps "
          f"(lot_size, payout_cadence, ruin_horizons, funded_lifetime)", flush=True)
    try:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        import os as _os
        # max_workers = min(cpu_count, sweep_count) — Windows spawn overhead is
        # high (~500ms per worker) so anything past 4 is waste here.
        max_workers = min(len(sweep_tasks), max(2, (_os.cpu_count() or 4) - 1))
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(_sweep_worker, t["fn"], t["args"], t["kwargs"]): name
                for name, t in sweep_tasks
            }
            for fut in as_completed(futures):
                key = futures[fut]
                try:
                    result[key] = _jsonify(fut.result())
                    print(f"[SWEEP] OK {key}", flush=True)
                except Exception as e:
                    result[key] = {"error": str(e)}
                    print(f"[SWEEP] FAIL {key}: {e}", flush=True)
                # Honor cancel between sweeps even though we can't cancel
                # an already-running worker mid-loop.
                try:
                    from app.jobs.runners import check_cancelled
                    check_cancelled()
                except ImportError:
                    pass
    except Exception as e:
        # Fall back to serial if process-pool itself crashes — better to be
        # slow than to lose all sweep data.
        print(f"[SWEEP] parallel pool failed, falling back to serial: {e}", flush=True)
        for name, t in sweep_tasks:
            if name in result:
                continue
            try:
                result[name] = _jsonify(_sweep_worker(t["fn"], t["args"], t["kwargs"]))
            except Exception as e:
                result[name] = {"error": str(e)}

    print("[SWEEP] verdict block + finalize", flush=True)

    # ── Verdict block ────────────────────────────────────────────────────────
    challenge_fee = float(global_params.get("challenge_fee", 0.0))
    fee_refund    = bool(global_params.get("fee_refunded_on_first_payout", True))
    try:
        result["verdict"] = _build_verdict(
            p1, p2, fd, lt,
            kelly=result.get("kelly") or {},
            challenge_fee=challenge_fee,
            fee_refund=fee_refund,
            intraday_dd_factor=intraday_factor,
        )
    except Exception as e:
        result["verdict"] = {"error": str(e)}

    # Echo the intraday factor so the dashboard can hide the disclaimer banner
    # whenever the user has opted into a tighter safety margin (factor < 1.0).
    result["intraday_dd_factor"] = intraday_factor

    # ── Normalize sweep/helper shapes to match frontend types ──────────────
    # The toolkit helpers return DataFrames (which _jsonify wraps as
    # {columns, records}) and use Python-side field names. The frontend
    # expects flat record arrays with shorter, UI-friendly field names.
    result["lot_sweep"]            = _flatten_records(result.get("lot_sweep"), {})
    result["payout_cadence_sweep"] = _flatten_records(result.get("payout_cadence_sweep"), {
        "avg_total_payouts_usd": "total_earnings",
        "blowup_rate":           "breach_rate",
    })
    result["ruin_horizons"]        = _flatten_records(result.get("ruin_horizons"), {
        "horizon_days":      "days",
        "ruin_probability":  "p_ruin",
    })
    # Kelly is a dict, not a DataFrame — just rename the keys.
    k = result.get("kelly") or {}
    if isinstance(k, dict) and "kelly_f" in k:
        result["kelly"] = {
            "kelly_fraction":      float(k.get("kelly_f", 0.0)),
            "half_kelly":          float(k.get("half_kelly", 0.0)),
            "expected_log_growth": float(k.get("expected_growth_rate", 0.0)),
        }
    # Trim funded_lifetime survival_curve (long array, not used by frontend).
    fl = result.get("funded_lifetime")
    if isinstance(fl, dict):
        fl.pop("survival_curve", None)

    return result


def _flatten_records(value: Any, rename: dict[str, str]) -> Any:
    """Convert {columns, records} → list of records with renamed keys.

    Idempotent: if ``value`` is already a list (or None / error dict), returns it
    as-is. Used to bridge pandas-DataFrame outputs to the frontend's flat-array
    contract without losing data.
    """
    if value is None:
        return None
    if isinstance(value, dict) and "records" in value and "columns" in value:
        records = value["records"]
    elif isinstance(value, list):
        records = value
    else:
        return value  # error dict or unexpected shape — pass through

    if not rename:
        return records
    return [
        {rename.get(k, k): v for k, v in row.items()}
        for row in records
    ]


def _build_verdict(
    p1: dict[str, Any],
    p2: dict[str, Any],
    fd: dict[str, Any],
    lt: dict[str, Any],
    *,
    kelly: dict[str, Any],
    challenge_fee: float,
    fee_refund: bool,
    intraday_dd_factor: float = 1.0,
) -> dict[str, Any]:
    """Compute the per-phase verdict block — raw numbers only, no prose.

    The frontend renders both numbers and a plain-English summary; we hand
    over the data, not the words.
    """
    p1_passed = int(p1.get("n_passed", 0))
    p1_total  = p1_passed + int(p1.get("n_failed", 0))
    p1_low, p1_high = _wilson_ci(p1_passed, p1_total)
    p1_dom_name, p1_dom_pct = _dominant_fail(p1)

    p2_passed = int(p2.get("n_passed", 0))
    p2_total  = p2_passed + int(p2.get("n_failed", 0))
    p2_low, p2_high = _wilson_ci(p2_passed, p2_total)
    p2_dom_name, p2_dom_pct = _dominant_fail(p2)

    # Funded: payout_rate is in 0..100; expected lifetime months from
    # avg_days_active / 21; expected monthly USD = avg_total_earnings / months.
    payout_rate     = float(fd.get("payout_rate", 0.0))
    avg_earnings    = float(fd.get("avg_total_earnings", 0.0))
    avg_days_active = float(fd.get("avg_days_active", 0.0))
    months_active   = max(avg_days_active / 21.0, 1e-9)
    expected_monthly = avg_earnings / months_active if months_active > 0 else 0.0
    breach_pcts     = fd.get("breach_pcts") or {}
    if breach_pcts:
        dom_breach_name, _dom_breach_pct = max(
            breach_pcts.items(), key=lambda kv: float(kv[1] or 0.0))
    else:
        dom_breach_name = "none"

    # Long-term: build P(ruin within 1y, 5y) from the per-sim final equity
    # arrays if available, falling back to ruin_floor crossing detection.
    median_eq = float(lt.get("median_equity", 0.0))
    median_sh = float(lt.get("median_sharpe", 0.0))
    # Long-term reports `pass_rate` = P(survival over n_days). Approximate
    # 1y / 5y ruin from this when available.
    pass_rate_lt = float(lt.get("pass_rate", 0.0))
    n_days_lt    = max(int(lt.get("n_days", 252 * 5)), 1)
    ruin_total   = max(0.0, 1.0 - pass_rate_lt)
    # Linear-in-time approximation when only a single horizon was simulated.
    p_ruin_1y = min(1.0, ruin_total * (252.0 / n_days_lt))
    p_ruin_5y = min(1.0, ruin_total * (1260.0 / n_days_lt))

    avg_fp = float(fd.get("avg_first_payout_day", 0.0)) if "avg_first_payout_day" in fd else 0.0

    # v0.5.0: replaced the previous "Fee Recoup Rate" (which was redundant with
    # payout_rate when fee_refund=True and had an off-by-100 display bug) with
    # an actual expected-return calculation per challenge attempt.
    #
    # Refunded-on-first-payout case (FTMO et al.):
    #   funded sims (prob = payout_rate/100):  net = +avg_earnings  (fee refunded)
    #   failed sims (prob = 1 - payout_rate/100): net = -challenge_fee
    #
    # Non-refunded case:
    #   funded sims: net = avg_earnings - challenge_fee
    #   failed sims: net = -challenge_fee
    payout_p = float(payout_rate) / 100.0
    if challenge_fee <= 0:
        avg_roi_pct = 0.0  # no fee → ROI undefined; display as N/A
    elif fee_refund:
        expected_net = payout_p * avg_earnings - (1.0 - payout_p) * challenge_fee
        avg_roi_pct  = (expected_net / challenge_fee) * 100.0
    else:
        expected_net = payout_p * (avg_earnings - challenge_fee) - (1.0 - payout_p) * challenge_fee
        avg_roi_pct  = (expected_net / challenge_fee) * 100.0

    kelly_f = float((kelly or {}).get("kelly_f", 0.0))

    return {
        "phase1": {
            "pass_rate":         float(p1.get("pass_rate", 0.0)),
            "pass_rate_ci_low":  p1_low * 100.0,
            "pass_rate_ci_high": p1_high * 100.0,
            "median_days":       float(p1.get("days_p50", 0.0)),
            "dominant_fail":     p1_dom_name,
            "dominant_fail_pct": p1_dom_pct,
        },
        "phase2": {
            "pass_rate":         float(p2.get("pass_rate", 0.0)),
            "pass_rate_ci_low":  p2_low * 100.0,
            "pass_rate_ci_high": p2_high * 100.0,
            "median_days":       float(p2.get("days_p50", 0.0)),
            "dominant_fail":     p2_dom_name,
            "dominant_fail_pct": p2_dom_pct,
        },
        "combined_days_to_funded": float(p1.get("days_p50", 0.0)) + float(p2.get("days_p50", 0.0)),
        "funded": {
            "payout_rate":              payout_rate,
            "expected_monthly_usd":     expected_monthly,
            "expected_lifetime_months": months_active,
            "breach_rate":                  float(fd.get("breach_rate", 0.0)),
            "breach_before_payout_rate":   float(fd.get("breach_before_payout_rate", 0.0)),
            "dominant_breach":              dom_breach_name,
            "avg_first_payout_day":         avg_fp,
        },
        "longterm": {
            "p_ruin_1y":     p_ruin_1y,
            "p_ruin_5y":     p_ruin_5y,
            "median_equity": median_eq,
            "median_sharpe": median_sh,
        },
        "global": {
            "challenge_fee":                 challenge_fee,
            "fee_refunded_on_first_payout":  fee_refund,
            # v0.5.0 — replaces ``roi_pass_rate``. Real expected return on
            # the fee invested per challenge attempt (refund-aware).
            "avg_roi_pct":                   avg_roi_pct,
            "kelly_fraction":                kelly_f,
            "kelly_verdict":                 _kelly_verdict(kelly_f),
            "intraday_dd_factor":            intraday_dd_factor,
        },
    }


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
    # Return raw equity values (not normalised) so the dashboard can render
    # absolute account-equity scales identical to the legacy charts.
    return padded.tolist()


def _sample_funded_curves(
    daily_pnl: Any,
    params: dict[str, Any],
    n_curves: int,
) -> dict[str, Any]:
    """Sample N_CURVE_SIMS funded sims and collect equity/floor curves + survival.

    Day-by-day mirror of mc_funded_test._run_funded_loop, but each path also
    records its equity curve, floor curve, and last-active day so the dashboard
    can plot the equity fan + survival curve.
    """
    mc = _get_mc()
    np = _np()
    daily_pnl_arr    = np.asarray(daily_pnl, dtype=float)
    rng              = mc._make_rng(params.get("seed"))
    balance          = float(params.get("balance", 100_000))
    daily_dd_pct     = float(params.get("daily_dd_pct", 0.05))
    total_dd_pct     = float(params.get("total_dd_pct", 0.10))
    cadence          = int(params.get("payout_cadence_days", 30))
    max_days         = int(params.get("max_days") or (12 * 21))
    payout_mode      = str(params.get("payout_mode", "schedule")).lower()
    payout_threshold = float(params.get("payout_threshold", 0.05))
    profit_split     = float(params.get("profit_split", 0.80))
    balance_reset    = bool(params.get("balance_reset", True))
    min_days_payout  = int(params.get("min_days_payout", 4))
    daily_loss_abs   = balance * daily_dd_pct
    total_floor      = balance * (1.0 - total_dd_pct)

    eq_curves: list[list[float]] = []
    fl_curves: list[list[float]] = []
    last_day:  list[int]         = []

    for _ in range(n_curves):
        equity            = balance
        current_floor     = total_floor
        days_since_payout = 0
        path              = [equity]
        floor_path        = [current_floor]
        active_to         = 0
        for day in range(max_days):
            day_pnl = float(rng.choice(daily_pnl_arr))
            day_open = equity
            days_since_payout += 1
            equity = max(equity + day_pnl, 0.0)

            # Daily floor reference ratchets each midnight to today's open.
            today_floor = day_open - daily_loss_abs

            path.append(equity)
            floor_path.append(today_floor)
            active_to = day + 1

            if equity < today_floor or equity < current_floor:
                break

            profit_above = equity - balance
            sched_hit = (payout_mode in ("schedule", "both")
                         and days_since_payout >= cadence
                         and profit_above > 0)
            thr_hit   = (payout_mode in ("threshold", "both")
                         and profit_above >= balance * payout_threshold)
            if days_since_payout >= min_days_payout and (sched_hit or thr_hit):
                payout = profit_above * profit_split
                if balance_reset:
                    equity        = balance
                    current_floor = total_floor
                else:
                    equity       -= payout
                days_since_payout = 0
                path[-1]          = equity
                floor_path[-1]    = current_floor

        # Pad to (max_days + 1).
        if len(path) < max_days + 1:
            pad_n = max_days + 1 - len(path)
            path        = path + [path[-1]] * pad_n
            floor_path  = floor_path + [floor_path[-1]] * pad_n
        eq_curves.append([float(v) for v in path])
        fl_curves.append([float(v) for v in floor_path])
        last_day.append(active_to)

    # Survival = % of these sampled paths still alive on each day.
    survival = [1.0]
    for d in range(1, max_days + 1):
        alive = sum(1 for ld in last_day if ld >= d)
        survival.append(alive / max(len(last_day), 1))

    return {
        "equity_curves": eq_curves,
        "floor_curves":  fl_curves,
        "survival":      survival,
        "max_sim_days":  int(max_days),
    }


def run_advanced(metric: str, params: dict[str, Any]) -> Any:
    _ensure_runners()
    if metric not in ADVANCED_METRICS:
        raise ValueError(f"unknown metric '{metric}'; expected one of {list(ADVANCED_METRICS)}")
    fn = ADVANCED_METRICS[metric]
    # Pull the PnL arg out of params so advanced callers can pass it by name.
    # Some advanced functions take daily_pnl_list or daily_pnl_per_lot_unit.
    return _jsonify(fn(**params))


def _jsonify(obj: Any) -> Any:
    """Recursively convert numpy/pandas objects to JSON-safe types.

    Coerces NaN / ±Inf to ``None`` so the result survives ``json.dumps`` on
    the FastAPI side. Standard CPython json.dumps emits literal ``NaN``
    tokens, which then make ``JSON.parse`` on the frontend reject the whole
    response — a silent failure mode that historically broke MC results
    whenever a no-pass case bubbled up.
    """
    import math
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    # Lazy numpy/pandas type checks — only import if we actually have an object to check.
    try:
        import numpy as np
        if isinstance(obj, np.floating):
            f = float(obj)
            return None if (math.isnan(f) or math.isinf(f)) else f
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return [_jsonify(v) for v in obj.tolist()]
    except ImportError:
        pass
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            return {
                "columns": list(obj.columns),
                "records": [_jsonify(r) for r in obj.to_dict(orient="records")],
            }
        if isinstance(obj, pd.Series):
            return [_jsonify(v) for v in obj.tolist()]
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {str(k): _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonify(x) for x in obj]
    return str(obj)
