"""Bridge to pattern_discovery_v6.py.

pattern_discovery_v6.main() is parameterless: it reads module-level constants
(RANDOM_SEED, OUTPUT_FOLDER, TRAIN_RATIO, ...) and writes CSV results into
OUTPUT_FOLDER. To expose parameters to the UI without editing the source file,
this bridge monkey-patches the imported module's attributes before calling
main(). Only known constant names are allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import paths  # noqa: F401
from ..paths import DEFAULT_DISC_OUTPUT, DEFAULT_HIST_DATA


@dataclass
class ParamMeta:
    """UI rendering hint for one overridable constant."""
    label: str
    group: str
    type: str          # "int" | "float" | "bool" | "str" | "folder"
    description: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[str] = field(default_factory=list)  # for str enum


# Full metadata map for every overridable constant.
# Groups mirror the CONFIG sections in pattern_discovery_v6.py.
PARAM_META: dict[str, ParamMeta] = {
    # ── Data & Files ────────────────────────────────────────────────────────
    "DATA_FOLDER":          ParamMeta("Data Folder",           "Data & Files", "folder",
                                       "Folder containing TF CSV files"),
    "TF1_FILE":             ParamMeta("TF1 Filename",          "Data & Files", "str",
                                       "Primary timeframe CSV (e.g. xauusd_m5.csv)"),
    "TF2_FILE":             ParamMeta("TF2 Filename",          "Data & Files", "str",
                                       "Second timeframe CSV"),
    "TF3_FILE":             ParamMeta("TF3 Filename",          "Data & Files", "str",
                                       "Third timeframe CSV"),
    "OUTPUT_FOLDER":        ParamMeta("Output Folder",         "Data & Files", "folder",
                                       "Where discovery results are written"),
    # ── General ─────────────────────────────────────────────────────────────
    "CORES_RESERVED":       ParamMeta("Cores Reserved",        "General", "int",
                                       "CPU cores to leave free for OS", min=0, max=16, step=1),
    "RANDOM_SEED":          ParamMeta("Random Seed",           "General", "int",
                                       "Base seed for reproducible runs", min=0),
    "TRAIN_RATIO":          ParamMeta("Train Ratio",           "General", "float",
                                       "Fraction of bars used for training", min=0.5, max=0.95, step=0.05),
    "MT5_SERVER_UTC_OFFSET":ParamMeta("MT5 UTC Offset",        "General", "int",
                                       "Broker server UTC offset (e.g. 2=EET, 3=EEST)", min=-12, max=14, step=1),
    "INDICATOR_WARMUP_BARS":ParamMeta("Indicator Warmup Bars", "General", "int",
                                       "Bars discarded so EMA(200) converges", min=50, max=500, step=50),
    "MULTI_SEED_COUNT":     ParamMeta("Multi-Seed Count",      "General", "int",
                                       "Run N seeds and merge unique patterns (1=off)", min=1, max=20, step=1),
    "MULTI_SEED_BASE":      ParamMeta("Multi-Seed Base",       "General", "int",
                                       "Seeds run from BASE, BASE+1, …"),
    "WINDOW_SIZE":          ParamMeta("Window Size",           "General", "int",
                                       "Candle window fed into clustering", min=2, max=20, step=1),
    "N_CLUSTERS":           ParamMeta("N Clusters",            "General", "int",
                                       "Clusters per algorithm per regime", min=2, max=20, step=1),
    # ── Regime & Features ───────────────────────────────────────────────────
    "REGIME_MODE":          ParamMeta("Regime Mode",           "Regime & Features", "bool",
                                       "Split clustering by market regime"),
    "USE_SHAPE_MATCHING":   ParamMeta("Shape Matching",        "Regime & Features", "bool",
                                       "Enable DTW-based shape similarity"),
    "SHAPE_MATCH_THRESHOLD":ParamMeta("Shape Match Threshold", "Regime & Features", "float",
                                       "Minimum shape similarity score", min=0.5, max=1.0, step=0.05),
    "USE_SOFT_FILTER":      ParamMeta("Soft Filter",           "Regime & Features", "bool",
                                       "Tag marginal patterns ⚠ instead of dropping"),
    "USE_EXTRA_FEATURES":   ParamMeta("Extra Features",        "Regime & Features", "bool",
                                       "Stoch, candle patterns, S/D zones, rolling Sharpe"),
    # ── Trade Simulation ────────────────────────────────────────────────────
    "FORWARD_BARS":         ParamMeta("Forward Bars",          "Trade Simulation", "int",
                                       "Bars ahead to evaluate the trade", min=4, max=100, step=1),
    "MEANINGFUL_MOVE_ATR":  ParamMeta("Meaningful Move (ATR)", "Trade Simulation", "float",
                                       "Price must move ≥ N×ATR", min=0.1, max=3.0, step=0.1),
    "MEANINGFUL_SUSTAIN_BARS":ParamMeta("Sustain Bars",        "Trade Simulation", "int",
                                         "Bars the move must hold", min=1, max=20, step=1),
    "SPREAD_PTS":           ParamMeta("Spread (pts)",          "Trade Simulation", "float",
                                       "Bid-ask spread cost in price points", min=0.0, max=5.0, step=0.05),
    "REALISTIC_ENTRY":      ParamMeta("Realistic Entry",       "Trade Simulation", "bool",
                                       "Apply spread to entry price"),
    "MAX_HOLD_BARS":        ParamMeta("Max Hold Bars",         "Trade Simulation", "int",
                                       "Force-exit after N bars", min=4, max=200, step=1),
    "COOLDOWN_BARS":        ParamMeta("Cooldown Bars",         "Trade Simulation", "int",
                                       "Bars to skip after each trade", min=0, max=20, step=1),
    # ── SL / TP ─────────────────────────────────────────────────────────────
    "SL_PCT_QUANTILE":      ParamMeta("SL Quantile",           "SL / TP", "float",
                                       "Adverse excursion quantile for stop-loss", min=0.5, max=0.99, step=0.01),
    "TP_PCT_QUANTILE":      ParamMeta("TP Quantile",           "SL / TP", "float",
                                       "Favourable excursion quantile for take-profit", min=0.3, max=0.99, step=0.01),
    "MIN_DIST_RR":          ParamMeta("Min R:R Distance",      "SL / TP", "float",
                                       "Minimum risk-reward ratio", min=0.1, max=3.0, step=0.05),
    # ── Genetic Pass 1 ──────────────────────────────────────────────────────
    "GENETIC_GENERATIONS":  ParamMeta("Generations",           "Genetic Pass 1", "int",
                                       "Evolution generations", min=5, max=100, step=5),
    "GENETIC_POPULATION":   ParamMeta("Population",            "Genetic Pass 1", "int",
                                       "Individuals per generation", min=20, max=200, step=10),
    "GENETIC_MUTATE_RATE":  ParamMeta("Mutation Rate",         "Genetic Pass 1", "float",
                                       "Gene mutation probability", min=0.05, max=0.5, step=0.05),
    "GENE_N_COLS_MIN":      ParamMeta("Min Gene Columns",      "Genetic Pass 1", "int",
                                       "Min conditions in a rule", min=1, max=10, step=1),
    "GENE_N_COLS_MAX":      ParamMeta("Max Gene Columns",      "Genetic Pass 1", "int",
                                       "Max conditions in a rule", min=2, max=15, step=1),
    "GENE_REPAIR_ATTEMPTS": ParamMeta("Repair Attempts",       "Genetic Pass 1", "int",
                                       "Tries to fix an invalid individual", min=1, max=10, step=1),
    "GENE_DIVERSITY_THRESHOLD":ParamMeta("Diversity Threshold","Genetic Pass 1", "float",
                                          "Similarity above which an individual is dropped", min=0.3, max=1.0, step=0.05),
    "GENE_ISLAND_COUNT":    ParamMeta("Island Count",          "Genetic Pass 1", "int",
                                       "Parallel island sub-populations", min=1, max=8, step=1),
    "GENE_MIGRATION_INTERVAL":ParamMeta("Migration Interval",  "Genetic Pass 1", "int",
                                          "Generations between island migrations", min=2, max=30, step=1),
    # ── Genetic Pass 2 ──────────────────────────────────────────────────────
    "TOP_FRACTION_PASS2":      ParamMeta("Top Fraction",       "Genetic Pass 2", "float",
                                          "Best-scoring fraction carried into pass 2", min=0.05, max=0.5, step=0.05),
    "MIN_TRADES_PER_DAY_PASS2":ParamMeta("Min Trades/Day",     "Genetic Pass 2", "float",
                                          "Min trade frequency required in pass 2", min=0.1, max=5.0, step=0.1),
    "PASS2_GENERATIONS":    ParamMeta("Generations (P2)",      "Genetic Pass 2", "int",  min=5, max=100, step=5),
    "PASS2_POPULATION":     ParamMeta("Population (P2)",       "Genetic Pass 2", "int",  min=10, max=100, step=5),
    "PASS2_MUTATE_RATE":    ParamMeta("Mutation Rate (P2)",    "Genetic Pass 2", "float", min=0.05, max=0.5, step=0.05),
    "PASS2_QUANTILE_LO":    ParamMeta("Quantile Low (P2)",     "Genetic Pass 2", "float", min=0.05, max=0.45, step=0.05),
    "PASS2_QUANTILE_HI":    ParamMeta("Quantile High (P2)",    "Genetic Pass 2", "float", min=0.55, max=0.95, step=0.05),
    # ── Ensemble ────────────────────────────────────────────────────────────
    "ENSEMBLE_OVERLAP_THRESHOLD":ParamMeta("Overlap Threshold","Ensemble", "float",
                                            "Max trade overlap allowed between patterns", min=0.3, max=1.0, step=0.05),
    # ── Bidirectional ───────────────────────────────────────────────────────
    "BIDIR_MIN_WR":          ParamMeta("Min Win Rate",         "Bidirectional", "float",
                                        "Min win-rate % to run direction check", min=45.0, max=70.0, step=0.5),
    "BIDIR_MIN_TRADES":      ParamMeta("Min Trades",           "Bidirectional", "int",
                                        "Min trades to run bidirectional check", min=5, max=50, step=1),
    "DISCRIM_MIN_ACCURACY":  ParamMeta("Discriminator Min Acc","Bidirectional", "float",
                                        "Min accuracy for direction classifier", min=0.5, max=0.9, step=0.01),
    # ── Scoring ─────────────────────────────────────────────────────────────
    "SCORE_W_WR":            ParamMeta("Weight: Win Rate",     "Scoring", "float", min=0.0, max=1.0, step=0.05),
    "SCORE_W_PF":            ParamMeta("Weight: Profit Factor","Scoring", "float", min=0.0, max=1.0, step=0.05),
    "SCORE_W_RR":            ParamMeta("Weight: Risk/Reward",  "Scoring", "float", min=0.0, max=1.0, step=0.05),
    "SCORE_W_STAB":          ParamMeta("Weight: Stability",    "Scoring", "float", min=0.0, max=1.0, step=0.05),
    "SCORE_WILSON_CONFIDENCE":ParamMeta("Wilson Confidence",   "Scoring", "float",
                                         "Confidence level for Wilson score interval", min=0.5, max=0.99, step=0.01),
    # ── Quality Filters ─────────────────────────────────────────────────────
    "MIN_FREQ_PER_DAY":      ParamMeta("Min Freq/Day",         "Quality Filters", "float",
                                        "Min pattern occurrences per trading day", min=0.05, max=5.0, step=0.05),
    "MIN_WIN_RATE":          ParamMeta("Min Win Rate %",       "Quality Filters", "float",
                                        "Minimum win-rate % to keep a pattern", min=30.0, max=70.0, step=0.5),
    "MIN_PROFIT_FACTOR":     ParamMeta("Min Profit Factor",    "Quality Filters", "float",
                                        "Minimum gross profit / gross loss", min=1.0, max=3.0, step=0.05),
    "MAX_DRAWDOWN_R":        ParamMeta("Max Drawdown (R)",     "Quality Filters", "float",
                                        "Max drawdown in R-multiples", min=2.0, max=50.0, step=0.5),
    "MAX_CONSEC_LOSSES":     ParamMeta("Max Consec. Losses",   "Quality Filters", "int",
                                        "Max tolerable consecutive losses", min=3, max=20, step=1),
    "MIN_TIME_CONSISTENCY":  ParamMeta("Min Time Consistency", "Quality Filters", "float",
                                        "Min fraction of months with any trade", min=0.1, max=1.0, step=0.05),
    "MIN_TEST_TRADES_PER_DAY":ParamMeta("Min Test Trades/Day", "Quality Filters", "float",
                                         "Min frequency on the out-of-sample split", min=0.05, max=3.0, step=0.05),
    "CORRELATION_THRESHOLD": ParamMeta("Correlation Threshold","Quality Filters", "float",
                                        "Max allowed pattern-pair trade correlation", min=0.3, max=1.0, step=0.05),
    "RECENT_BARS":           ParamMeta("Recent Bars",          "Quality Filters", "int",
                                        "Recency window for stability scoring", min=1000, max=20000, step=500),
    # ── MC Auto-run ─────────────────────────────────────────────────────────
    "RUN_MC_ON_TOP_N":       ParamMeta("Auto-MC Top N",        "MC Auto-run", "int",
                                        "Run quick MC on top-N patterns after discovery", min=0, max=20, step=1),
    "MC_N_SIMS":             ParamMeta("Auto-MC Simulations",  "MC Auto-run", "int",
                                        "Quick-MC simulation count", min=1000, max=50000, step=1000),
    "MC_BALANCE":            ParamMeta("Auto-MC Balance",      "MC Auto-run", "float",
                                        "Starting balance for quick MC", min=1000.0),
    "MC_LOT":                ParamMeta("Auto-MC Lot",          "MC Auto-run", "float",
                                        "Lot size for quick MC", min=0.01, max=10.0, step=0.01),
    "MC_MAX_DAYS":           ParamMeta("Auto-MC Max Days",     "MC Auto-run", "int",
                                        "Simulation horizon for quick MC", min=10, max=365, step=5),
}

# Flat set used as whitelist — derived from the metadata map so the two stay in sync.
OVERRIDABLE_CONSTANTS: set[str] = set(PARAM_META.keys())


def _get_module():
    # Lazy import: pattern_discovery_v6 is ~3k lines and pulls heavy deps.
    # Only import when the user actually starts a discovery run.
    import importlib

    return importlib.import_module("pattern_discovery_v6")


def _jsonify_val(val: Any) -> Any:
    """Make a module constant JSON-safe."""
    if isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (list, tuple, set)):
        return list(val)
    return str(val)


def list_defaults() -> dict[str, Any]:
    """Return the current values of overridable constants, for UI defaults."""
    mod = _get_module()
    out: dict[str, Any] = {}
    for name in OVERRIDABLE_CONSTANTS:
        if hasattr(mod, name):
            out[name] = _jsonify_val(getattr(mod, name))
    return out


def list_defaults_with_meta() -> list[dict[str, Any]]:
    """Return values + UI rendering metadata for every overridable constant.

    If pattern_discovery_v6 cannot be imported (missing deps, wrong env),
    the metadata is still returned with None values so the UI can render
    the form — the user just won't see live defaults.
    """
    try:
        mod = _get_module()
    except Exception:
        mod = None
    result: list[dict[str, Any]] = []
    for name, meta in PARAM_META.items():
        val = (_jsonify_val(getattr(mod, name)) if (mod is not None and hasattr(mod, name)) else None)
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


def run_discovery(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Monkey-patch globals and call pattern_discovery_v6.main().

    Returns a minimal summary dict; the full artifacts land in OUTPUT_FOLDER
    as CSVs and are picked up by a separate read step.
    """
    mod = _get_module()
    overrides = dict(overrides or {})

    unknown = set(overrides) - OVERRIDABLE_CONSTANTS
    if unknown:
        raise KeyError(f"not overridable: {sorted(unknown)}")

    # Inject app-relative defaults for path constants when not user-overridden,
    # so the module never falls back to its hardcoded MONTE CARLO paths.
    if "OUTPUT_FOLDER" not in overrides:
        overrides["OUTPUT_FOLDER"] = str(DEFAULT_DISC_OUTPUT)
    if "DATA_FOLDER" not in overrides:
        overrides["DATA_FOLDER"] = str(DEFAULT_HIST_DATA)

    # Snapshot originals so we can restore them after the run.
    original: dict[str, Any] = {}
    for name, val in overrides.items():
        if not hasattr(mod, name):
            raise KeyError(f"pattern_discovery_v6 has no attribute '{name}'")
        original[name] = getattr(mod, name)
        setattr(mod, name, val)

    try:
        # The script's `if __name__ == "__main__"` block normally does these two
        # steps before calling main(). When invoked via the FastAPI bridge, the
        # __main__ block doesn't run, so we mirror it explicitly. Without
        # _prepare_shared_data the module-level _SHARED_DATA dict stays empty
        # and main() raises `KeyError: 'df'` on first access.
        mod.mp.set_start_method("spawn", force=True)
        mod._prepare_shared_data(mod._n_workers())
        mod.main()
    finally:
        for name, val in original.items():
            setattr(mod, name, val)

    return {
        "ok": True,
        "output_folder": str(getattr(mod, "OUTPUT_FOLDER", "")),
        "overrides_applied": list(overrides.keys()),
    }
