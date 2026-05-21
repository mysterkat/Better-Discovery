"""Bridge to pattern_discovery_v6.py.

pattern_discovery_v6.main() is parameterless: it reads module-level constants
(RANDOM_SEED, OUTPUT_FOLDER, TRAIN_RATIO, ...) and writes CSV results into
OUTPUT_FOLDER. To expose parameters to the UI without editing the source file,
this bridge monkey-patches the imported module's attributes before calling
main(). Only known constant names are allowed.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths  # noqa: F401
from ..paths import DEFAULT_DISC_OUTPUT, DEFAULT_HIST_DATA


_STAGE_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.+)$")


class _ProgressCapture:
    """Wrap sys.stdout so we can parse `[i/N] StageName` lines emitted by
    pattern_discovery_v6.main()'s `_stage()` calls and report them to the
    job. Also opportunistically checks the cancel flag on each line — if
    the user has requested cancellation, the next print raises out of
    main()."""

    def __init__(self, original: Any, job: Any) -> None:
        self._orig = original
        self._job = job
        self._buf = ""

    def write(self, data: str) -> int:
        n = self._orig.write(data)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                m = _STAGE_RE.match(line)
                if m:
                    idx, total, name = int(m.group(1)), int(m.group(2)), m.group(3).strip()
                    self._job.mark_stage(name, idx, total)
            # Cooperative cancellation — raise so main() unwinds at the
            # next print boundary (which is at every stage transition).
            if self._job.is_cancel_requested():
                from ..jobs.runners import CancelledError
                raise CancelledError(f"job {self._job.job_id} cancelled at stage")
        return n

    def flush(self) -> None:
        self._orig.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._orig, name)


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
    # "core" = always visible in the per-run accordion.
    # "advanced" = hidden behind a per-group "Show advanced (N)" collapse.
    # Tier is assigned after PARAM_META is built — see _ADVANCED_KEYS below.
    tier: str = "core"


# Full metadata map for every overridable constant.
# Groups mirror the CONFIG sections in pattern_discovery_v6.py.
PARAM_META: dict[str, ParamMeta] = {
    # ── Data & Files ────────────────────────────────────────────────────────
    "DATA_FOLDER":          ParamMeta("Data Folder",           "Data & Files", "folder",
                                       "Folder containing TF CSV files"),
    "TF1_FILE":             ParamMeta("TF1 Filename",          "Data & Files", "str",
                                       "Slot 1 — CSV (e.g. xauusd_m5.csv). Empty = unused."),
    "TF2_FILE":             ParamMeta("TF2 Filename",          "Data & Files", "str",
                                       "Slot 2 — CSV. Empty = unused."),
    "TF3_FILE":             ParamMeta("TF3 Filename",          "Data & Files", "str",
                                       "Slot 3 — CSV. Empty = unused."),
    "TF4_FILE":             ParamMeta("TF4 Filename",          "Data & Files", "str",
                                       "Slot 4 — CSV. Empty = unused."),
    "TF5_FILE":             ParamMeta("TF5 Filename",          "Data & Files", "str",
                                       "Slot 5 — CSV. Empty = unused."),
    "PRIMARY_TF":           ParamMeta("Primary TF (1-5)",      "Data & Files", "int",
                                       "Which slot drives the bar stream (entries / exits / SL / TP). "
                                       "Other non-empty slots become signal TFs.",
                                       min=1, max=5, step=1),
    "MTF_SCORE_MODE":       ParamMeta("MTF Score Mode",         "Data & Files", "str",
                                       "How mtf_bull/bear_score is composed across signal TFs.\n"
                                       "  additive — sum primary + every signal trend (recommended; "
                                       "matches bundled MQL5 EA)\n"
                                       "  overwrite — primary + last-aligned signal only (legacy 0..2 "
                                       "range; for parity with pre-Group-C runs)",
                                       options=["additive", "overwrite"]),
    "HTF_DIV_TF":           ParamMeta("htf_div Source TF",      "Data & Files", "int",
                                       "Which signal slot's RSI feeds the htf_div feature. "
                                       "0 = auto (slowest available signal TF, recommended). "
                                       "1-5 = force a specific slot.",
                                       min=0, max=5, step=1),
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
    "GENETIC_GENERATIONS":  ParamMeta("Generations",           "Genetic Pass 1 (GA)", "int",
                                       "Evolution generations", min=5, max=100, step=5),
    "GENETIC_POPULATION":   ParamMeta("Population",            "Genetic Pass 1 (GA)", "int",
                                       "Individuals per generation", min=20, max=200, step=10),
    "GENETIC_MUTATE_RATE":  ParamMeta("Mutation Rate",         "Genetic Pass 1 (GA)", "float",
                                       "Gene mutation probability", min=0.05, max=0.5, step=0.05),
    "GENE_N_COLS_MIN":      ParamMeta("Min Gene Columns",      "Genetic Pass 1 (GA)", "int",
                                       "Min conditions in a rule", min=1, max=10, step=1),
    "GENE_N_COLS_MAX":      ParamMeta("Max Gene Columns",      "Genetic Pass 1 (GA)", "int",
                                       "Max conditions in a rule", min=2, max=15, step=1),
    "GENE_REPAIR_ATTEMPTS": ParamMeta("Repair Attempts",       "Genetic Pass 1 (GA)", "int",
                                       "Tries to fix an invalid individual", min=1, max=10, step=1),
    "GENE_DIVERSITY_THRESHOLD":ParamMeta("Diversity Threshold","Genetic Pass 1 (GA)", "float",
                                          "Similarity above which an individual is dropped", min=0.3, max=1.0, step=0.05),
    "GENE_ISLAND_COUNT":    ParamMeta("Island Count",          "Genetic Pass 1 (GA)", "int",
                                       "Parallel island sub-populations", min=1, max=8, step=1),
    "GENE_MIGRATION_INTERVAL":ParamMeta("Migration Interval",  "Genetic Pass 1 (GA)", "int",
                                          "Generations between island migrations", min=2, max=30, step=1),
    "GENE_USE_CROWDING":    ParamMeta("Deterministic Crowding","Genetic Pass 1 (GA)", "bool",
                                       "Replace island model with DC replacement (v0.9.x #24). "
                                       "Maintains diversity without migration; set False to revert to island model."),
    # ── Optimizer (Task #31 — moved out of "Genetic Pass 1" so it's clearly
    #   a cross-cutting selector, not a GA-specific knob) ─────────────────
    "GENE_OPTIMIZER":       ParamMeta("Optimizer",             "Optimizer", "str",
                                       "ga = evolutionary GA (default); optuna = Bayesian TPE search. "
                                       "Switching this hides the GA-specific accordions below.",
                                       options=["ga", "optuna"]),
    "SURROGATE_ENABLED":    ParamMeta("Surrogate Model",       "Optimizer", "bool",
                                       "Train a GBM on scored rules to predict fitness cheaply. "
                                       "After SURROGATE_MIN_SAMPLES real evals, ~90% of calls use the predictor. "
                                       "Wraps either optimizer."),
    "SURROGATE_REAL_FRAC":  ParamMeta("Surrogate Real Frac",   "Optimizer", "float",
                                       "Fraction of optimizer calls that hit the real scorer (rest use GBM).",
                                       min=0.05, max=0.5, step=0.05),
    # ── Genetic Pass 2 ──────────────────────────────────────────────────────
    "TOP_FRACTION_PASS2":      ParamMeta("Top Fraction",       "Genetic Pass 2 (GA)", "float",
                                          "Best-scoring fraction carried into pass 2", min=0.05, max=0.5, step=0.05),
    "MIN_TRADES_PER_DAY_PASS2":ParamMeta("Min Trades/Day",     "Genetic Pass 2 (GA)", "float",
                                          "Min trade frequency required in pass 2", min=0.1, max=5.0, step=0.1),
    "PASS2_GENERATIONS":    ParamMeta("Generations (P2)",      "Genetic Pass 2 (GA)", "int",  min=5, max=100, step=5),
    "PASS2_POPULATION":     ParamMeta("Population (P2)",       "Genetic Pass 2 (GA)", "int",  min=10, max=100, step=5),
    "PASS2_MUTATE_RATE":    ParamMeta("Mutation Rate (P2)",    "Genetic Pass 2 (GA)", "float", min=0.05, max=0.5, step=0.05),
    "PASS2_QUANTILE_LO":    ParamMeta("Quantile Low (P2)",     "Genetic Pass 2 (GA)", "float", min=0.05, max=0.45, step=0.05),
    "PASS2_QUANTILE_HI":    ParamMeta("Quantile High (P2)",    "Genetic Pass 2 (GA)", "float", min=0.55, max=0.95, step=0.05),
    # ── Bidirectional ───────────────────────────────────────────────────────
    "FORCE_DIRECTION":       ParamMeta("Force Direction",      "Bidirectional", "str",
                                        "Lock every cluster to a single trade direction. "
                                        "Use long_only or short_only when your MT5 EA is configured for a single side — "
                                        "this prevents the bidirectional discriminator from being emitted, "
                                        "guaranteeing the .set parity with single-direction MT5 runs. "
                                        "auto (default) = empirical per-cluster classification.",
                                        options=["auto", "long_only", "short_only"]),
    "BIDIR_MIN_WR":          ParamMeta("Min Win Rate",         "Bidirectional", "float",
                                        "Min win-rate % to run direction check", min=45.0, max=70.0, step=0.5),
    "BIDIR_MIN_TRADES":      ParamMeta("Min Trades",           "Bidirectional", "int",
                                        "Min trades to run bidirectional check", min=5, max=50, step=1),
    "DISCRIM_MIN_ACCURACY":  ParamMeta("Discriminator Min Acc","Bidirectional", "float",
                                        "Min accuracy for direction classifier", min=0.5, max=0.9, step=0.01),
    # ── Scoring ─────────────────────────────────────────────────────────────
    # v0.6.0: GA now hill-climbs toward user-set targets instead of blindly
    # maximising. Below target → quadratic penalty. At target → full credit.
    # Above target → tiny log bonus capped by EXCESS_BONUS_WEIGHT so the GA
    # won't sacrifice one objective to push another past its target.
    "ENABLE_TARGET_SCORING": ParamMeta("Target-Driven Scoring", "Scoring & Targets", "bool",
                                        # fix 2c: "GA" → "the optimizer" (Optuna now shares scorer)
                                        "When on (recommended), the optimizer evolves toward your target values "
                                        "instead of blindly maximising. Turn off to use legacy v0.5 behaviour."),
    "TARGET_WR_PCT":         ParamMeta("Target: Win Rate %",   "Scoring & Targets", "float",
                                        # fix 2c: "GA " → "the optimizer "
                                        "The optimizer will aim for this win-rate. Exceeding is OK if "
                                        "it costs nothing on other objectives.", min=40.0, max=85.0, step=0.5),
    "TARGET_PF":             ParamMeta("Target: Profit Factor","Scoring & Targets", "float",
                                        # fix 2c: "GA " → "the optimizer "
                                        "The optimizer aims for this profit factor.", min=1.1, max=4.0, step=0.05),
    "TARGET_RR":             ParamMeta("Target: R:R",          "Scoring & Targets", "float",
                                        # fix 2c: "GA " → "the optimizer "
                                        "The optimizer aims for this R:R as multiple of break-even.",
                                        min=0.8, max=3.0, step=0.05),
    "TARGET_STABILITY":      ParamMeta("Target: Stability",    "Scoring & Targets", "float",
                                        # fix 2c: "GA " → "the optimizer "
                                        "The optimizer aims for this time-consistency × distribution score (0..1).",
                                        min=0.3, max=0.95, step=0.05),
    "TARGET_TRADES_PER_DAY": ParamMeta("Target: Trades / Day", "Scoring & Targets", "float",
                                        # fix 2c: "GA " → "the optimizer "
                                        "The optimizer aims for this average trades-per-calendar-day rate "
                                        "(soft goal — doesn't reject patterns).",
                                        min=0.1, max=10.0, step=0.1),
    "SCORE_W_TRADES_PER_DAY": ParamMeta("Weight: Trades/Day",  "Scoring & Targets", "float",
                                        "Relative importance of the trades-per-day objective.",
                                        min=0.0, max=1.0, step=0.05),
    "SCORE_W_RULE_COMPLEXITY": ParamMeta("Weight: Rule Complexity", "Scoring & Targets", "float",
                                        "Bonus for rules that use more indicator columns. "
                                        "0 = off (default). 0.05 = mild tie-breaker. 0.15 = strong preference. "
                                        "Useful when too many strategies land on just 3-4 indicators.",
                                        min=0.0, max=0.5, step=0.01),
    "EXCESS_BONUS_WEIGHT":   ParamMeta("Excess Bonus Weight",  "Scoring & Targets", "float",
                                        # fix 2c: "GA " → "the optimizer "
                                        "How much the optimizer rewards exceeding targets. 0 = strict, "
                                        "0.1 = mild (recommended), 1.0 = legacy max-everything.",
                                        min=0.0, max=1.0, step=0.05),
    # SCORE_W_* are used in BOTH modes: with target scoring they weight each
    # objective's distance-to-target; in legacy mode they weight the absolute
    # metric. Same knob, different semantics — keep them visible in both modes.
    "SCORE_W_WR":            ParamMeta("Weight: Win Rate",     "Scoring & Targets", "float",
                                        "Relative importance of WR (active in both target and legacy modes)",
                                        min=0.0, max=1.0, step=0.05),
    "SCORE_W_PF":            ParamMeta("Weight: Profit Factor","Scoring & Targets", "float",
                                        "Relative importance of PF (active in both target and legacy modes)",
                                        min=0.0, max=1.0, step=0.05),
    "SCORE_W_RR":            ParamMeta("Weight: Risk/Reward",  "Scoring & Targets", "float",
                                        "Relative importance of R:R (active in both target and legacy modes)",
                                        min=0.0, max=1.0, step=0.05),
    "SCORE_W_STAB":          ParamMeta("Weight: Stability",    "Scoring & Targets", "float",
                                        "Relative importance of stability (active in both target and legacy modes)",
                                        min=0.0, max=1.0, step=0.05),
    "SCORE_WILSON_CONFIDENCE":ParamMeta("Wilson Confidence",   "Scoring & Targets", "float",
                                         "Confidence level for Wilson score interval. Drives the GA only "
                                         "in legacy mode (Target-Driven OFF), but also feeds the wilson_wr "
                                         "column shown in every result row.",
                                         min=0.5, max=0.99, step=0.01),
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
    # v0.8.0: was its own single-item "Ensemble" group; merged here since it's
    # functionally another quality gate (dedupes overlapping patterns).
    "ENSEMBLE_OVERLAP_THRESHOLD":ParamMeta("Max Trade Overlap","Quality Filters", "float",
                                            "Max trade overlap allowed between patterns (Jaccard)",
                                            min=0.3, max=1.0, step=0.05),
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


# Params demoted behind a per-group "Show advanced (N)" collapse in the UI.
# These are still fully active in the toolkit; they're just less commonly
# touched, so hiding them by default cuts visual clutter without removing
# any capability. Power users open the collapse to access them.
# Task #31: groups that only apply when another param has a specific value.
# The frontend reads this to dim/collapse irrelevant accordions when the user
# switches optimizer. Each entry: group_name -> {"key": ..., "value": ...}.
# When the gate's key holds the listed value, the group is "active"; otherwise
# the frontend shows it with reduced opacity and an "Inactive" badge.
GROUP_GATES: dict[str, dict[str, str]] = {
    "Genetic Pass 1 (GA)": {"key": "GENE_OPTIMIZER", "value": "ga"},
    "Genetic Pass 2 (GA)": {"key": "GENE_OPTIMIZER", "value": "ga"},
}


_ADVANCED_KEYS: set[str] = {
    # Data & Files
    "MTF_SCORE_MODE", "HTF_DIV_TF", "OUTPUT_FOLDER",
    # General
    "MT5_SERVER_UTC_OFFSET", "INDICATOR_WARMUP_BARS",
    # Regime & Features
    "SHAPE_MATCH_THRESHOLD",
    # Trade Simulation
    "MEANINGFUL_SUSTAIN_BARS", "COOLDOWN_BARS",
    # Genetic Pass 1 (GA)
    "GENE_N_COLS_MIN", "GENE_N_COLS_MAX", "GENE_REPAIR_ATTEMPTS",
    "GENE_DIVERSITY_THRESHOLD", "GENE_ISLAND_COUNT", "GENE_MIGRATION_INTERVAL",
    # Optimizer — surrogate tuning is advanced; main toggle stays core
    "SURROGATE_REAL_FRAC",
    # Genetic Pass 2 (GA) — entire group is tuning territory
    "TOP_FRACTION_PASS2", "MIN_TRADES_PER_DAY_PASS2",
    "PASS2_GENERATIONS", "PASS2_POPULATION", "PASS2_MUTATE_RATE",
    "PASS2_QUANTILE_LO", "PASS2_QUANTILE_HI",
    # Bidirectional — gating thresholds rarely tuned
    "BIDIR_MIN_WR", "BIDIR_MIN_TRADES", "DISCRIM_MIN_ACCURACY",
    # Scoring — targets are the primary knobs; weights + bonus + wilson are advanced
    # fix 2a: trades/day target + weight moved to advanced (less frequently tuned)
    "TARGET_TRADES_PER_DAY", "SCORE_W_TRADES_PER_DAY",
    "SCORE_W_RULE_COMPLEXITY",  # off by default; tuning knob for over-simple rules
    "EXCESS_BONUS_WEIGHT",
    "SCORE_W_WR", "SCORE_W_PF", "SCORE_W_RR", "SCORE_W_STAB",
    "SCORE_WILSON_CONFIDENCE",
    # Quality Filters
    "CORRELATION_THRESHOLD", "RECENT_BARS",
    # MC Auto-run — RUN_MC_ON_TOP_N is the master toggle; the rest are sizing
    "MC_N_SIMS", "MC_BALANCE", "MC_LOT", "MC_MAX_DAYS",
}
for _k in _ADVANCED_KEYS:
    if _k in PARAM_META:
        PARAM_META[_k].tier = "advanced"


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

    Path constants are special-cased: the module's hardcoded paths point
    at a developer's `MONTE CARLO` folder which doesn't exist on user
    machines. The bridge actually injects `DEFAULT_HIST_DATA` / `DEFAULT_DISC_OUTPUT`
    at run time, so we show those resolved paths in the UI instead — what
    the user sees as the "default" matches what actually gets used.
    """
    try:
        mod = _get_module()
    except Exception:
        mod = None
    # Override the displayed default for path-type constants so the UI shows
    # the actual app-resolved path (matches what the bridge injects in
    # run_discovery() when the user hasn't customized).
    runtime_defaults: dict[str, str] = {
        "OUTPUT_FOLDER": str(DEFAULT_DISC_OUTPUT),
        "DATA_FOLDER":   str(DEFAULT_HIST_DATA),
    }
    result: list[dict[str, Any]] = []
    for name, meta in PARAM_META.items():
        if name in runtime_defaults:
            val: Any = runtime_defaults[name]
        else:
            val = (_jsonify_val(getattr(mod, name)) if (mod is not None and hasattr(mod, name)) else None)
        entry: dict[str, Any] = {
            "key": name,
            "value": val,
            "label": meta.label,
            "group": meta.group,
            "type": meta.type,
            "description": meta.description,
            "tier": meta.tier,
        }
        if meta.min is not None:
            entry["min"] = meta.min
        if meta.max is not None:
            entry["max"] = meta.max
        if meta.step is not None:
            entry["step"] = meta.step
        if meta.options:
            entry["options"] = meta.options
        # Task #31: per-group gating (e.g. GA-only groups hidden when Optuna selected)
        if meta.group in GROUP_GATES:
            entry["gated_by"] = GROUP_GATES[meta.group]
        result.append(entry)
    return result


def _auto_fill_tf_files(mod: Any, overrides: dict[str, Any]) -> None:
    """If the user hasn't customized any TFn_FILE, auto-detect from the
    contents of DATA_FOLDER (the latest MT5 import).

    Sorts available CSVs by timeframe duration (smallest first) and fills
    slots TF1..TF5. If the user touched even one TFn_FILE, all five are left
    as the user specified.
    """
    tf_keys = ("TF1_FILE", "TF2_FILE", "TF3_FILE", "TF4_FILE", "TF5_FILE")
    if any(k in overrides for k in tf_keys):
        return  # user customized at least one — respect their full setup

    from . import mt5_import as _mt5
    inv = _mt5.list_current_import()
    files: list[str] = [tf["filename"] for tf in inv.get("timeframes", [])]
    if not files:
        return  # no import detected — leave module's hardcoded defaults

    for i, key in enumerate(tf_keys):
        overrides[key] = files[i] if i < len(files) else ""


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

    # Auto-detect TF files from the current MT5 import unless the user
    # explicitly customized them.
    _auto_fill_tf_files(mod, overrides)

    # Keep MULTI_SEED_BASE locked to RANDOM_SEED unless the user explicitly
    # overrode the base. The toolkit assigns `MULTI_SEED_BASE = RANDOM_SEED`
    # at import time, which freezes the base at whatever the file default was —
    # so when the user changes RANDOM_SEED via the UI but not MULTI_SEED_BASE,
    # the seed loop would otherwise still walk from the file's default base.
    # Resolving "what should the base be" against the *effective* RANDOM_SEED
    # (override if set, else current module value) gives the user the seed
    # they actually picked.
    if "MULTI_SEED_BASE" not in overrides:
        effective_seed = overrides.get("RANDOM_SEED", getattr(mod, "RANDOM_SEED", 0))
        overrides["MULTI_SEED_BASE"] = int(effective_seed)

    # Snapshot originals so we can restore them after the run.
    original: dict[str, Any] = {}
    for name, val in overrides.items():
        if not hasattr(mod, name):
            raise KeyError(f"pattern_discovery_v6 has no attribute '{name}'")
        original[name] = getattr(mod, name)
        setattr(mod, name, val)

    # Write `_app_override.json` next to pattern_discovery_v6.py so spawn-mode
    # pool workers — which re-import the module from disk and never see the
    # parent's setattr — pick up the same overrides at module-import time via
    # _load_app_overrides(). Without this, tunables like GENE_N_COLS_MAX,
    # COOLDOWN_BARS, GENE_DIVERSITY_THRESHOLD, PASS2_QUANTILE_LO/HI applied
    # only to the parent and the genetic search ran with file defaults.
    import json as _json
    _override_json_path = Path(mod.__file__).parent / "_app_override.json"
    try:
        # Only include JSON-serializable scalars/lists/dicts (which is what
        # PARAM_META exposes anyway). Non-serializable items are dropped.
        _serializable: dict[str, Any] = {}
        for _k, _v in overrides.items():
            try:
                _json.dumps(_v)
                _serializable[_k] = _v
            except (TypeError, ValueError):
                pass
        _override_json_path.write_text(
            _json.dumps(_serializable, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        # If the toolkit folder is read-only (rare), continue without the file —
        # workers will just use file defaults. The parent's setattr still works.
        _override_json_path = None  # type: ignore[assignment]

    # Wrap stdout for stage-progress reporting + cancel cooperation.
    # We do this conditionally: only when running on a job thread (i.e.,
    # via the FastAPI bridge), not when imported standalone.
    from ..jobs.runners import get_current_job
    job = get_current_job()
    orig_stdout = sys.stdout
    if job is not None:
        sys.stdout = _ProgressCapture(orig_stdout, job)  # type: ignore[assignment]

    aggregated_results: list[Any] = []  # collected across all seeds
    # Capture the output folder NOW, while the override is in effect.
    # We MUST NOT read mod.OUTPUT_FOLDER after the `finally` block — by then
    # the original (hardcoded MONTE CARLO) value has been restored, which
    # would make the .set-file glob look in the wrong directory.
    effective_output_folder: str = str(getattr(mod, "OUTPUT_FOLDER", ""))
    try:
        # The script's `if __name__ == "__main__"` block normally does these
        # steps before calling main(). When invoked via the FastAPI bridge,
        # the __main__ block doesn't run, so we mirror it here:
        #   - set spawn start method
        #   - prepare shared data ONCE before the seed loop
        #   - run main() per seed (single-seed path falls through naturally)
        #   - aggregate per-seed results and call write_combined_report
        # Skipping the seed loop is what made discovery only run for 1 seed
        # regardless of MULTI_SEED_COUNT.
        mod.mp.set_start_method("spawn", force=True)
        mod._prepare_shared_data(mod._n_workers())

        seed_count = max(1, int(getattr(mod, "MULTI_SEED_COUNT", 1) or 1))
        seed_base  = int(getattr(mod, "MULTI_SEED_BASE",
                                   getattr(mod, "RANDOM_SEED", 0)))
        # Capture the effective seed NOW — before the finally block restores
        # the module to its compiled default. Used to stamp single-seed results
        # so the post-run glob finds the correct seed_{N} subfolder.
        effective_seed = int(getattr(mod, "RANDOM_SEED", 0))

        if seed_count <= 1:
            if job is not None:
                job.mark_seed(1, 1, effective_seed)
            r = mod.main()
            if r:
                for result in r:
                    result.setdefault("seed", effective_seed)
                aggregated_results.extend(r)
        else:
            print(f"\n{'=' * 65}")
            print(f"  MULTI-SEED BATCH: {seed_count} seeds starting from {seed_base}")
            print(f"{'=' * 65}")
            for si in range(seed_count):
                seed = seed_base + si
                mod.RANDOM_SEED = seed
                mod.np.random.seed(seed)
                mod.random.seed(seed)
                if job is not None:
                    job.mark_seed(si + 1, seed_count, seed)
                print(f"\n\n{'#' * 65}")
                print(f"  SEED {si + 1}/{seed_count}  ->  {seed}")
                print(f"{'#' * 65}")
                seed_results = mod.main()
                if seed_results:
                    for r in seed_results:
                        r["seed"] = seed
                    aggregated_results.extend(seed_results)
            if hasattr(mod, "write_combined_report") and aggregated_results:
                mod.write_combined_report(aggregated_results, mod.OUTPUT_FOLDER)
                print(f"\n{'=' * 65}")
                print(f"  BATCH COMPLETE: {seed_count} seeds finished.")
                print(f"{'=' * 65}\n")
    finally:
        sys.stdout = orig_stdout
        for name, val in original.items():
            setattr(mod, name, val)
        # Remove the _app_override.json so a subsequent standalone run of
        # pattern_discovery_v6.py (or a future BD run with no overrides) is
        # not silently affected by what THIS run wrote.
        if _override_json_path is not None:
            try:
                _override_json_path.unlink(missing_ok=True)
            except OSError:
                pass

    # Build per-pattern summaries from the in-memory results. We pair each
    # qualifying pattern with the .set file the script wrote to disk so the
    # UI can offer copy/save/export actions.
    #
    # IMPORTANT: pattern_discovery_v6.main() writes outputs into a per-seed
    # subfolder: `OUTPUT_FOLDER/seed_{seed}/`. So the .set files live in
    # those subfolders, NOT directly in OUTPUT_FOLDER. Use the value we
    # captured *before* the `finally` block restored mod.OUTPUT_FOLDER to
    # its hardcoded original — otherwise we'd glob the wrong directory.
    out_folder = Path(effective_output_folder)
    patterns_summary: list[dict[str, Any]] = []
    for idx, r in enumerate(aggregated_results, start=1):
        seed = r.get("seed", getattr(mod, "RANDOM_SEED", 0))
        cid = r.get("cluster", -1)
        direction = r.get("direction", "?")
        # Match the filename convention in pattern_discovery_v6.py:
        # `OUTPUT_FOLDER/seed_{seed}/pattern_{NN}_C{cid}_{direction}_seed{seed}.set`
        # NN is reset per-seed, so we glob within the seed-specific subfolder
        # for any .set matching this cluster+direction.
        set_path: str | None = None
        seed_folder = out_folder / f"seed_{seed}"
        if seed_folder.is_dir():
            for candidate in seed_folder.glob(f"pattern_*_C{cid}_{direction}_seed{seed}.set"):
                set_path = str(candidate)
                break
        # Fallback: look in the root output folder too, in case a future
        # pattern_discovery_v6 stops nesting by seed. Cheap belt-and-braces.
        if set_path is None and out_folder.is_dir():
            for candidate in out_folder.glob(f"pattern_*_C{cid}_{direction}_seed{seed}.set"):
                set_path = str(candidate)
                break
        patterns_summary.append({
            "rank":            idx,
            "pattern_id":      r.get("pattern_id", f"C{cid}_{direction}_seed{seed}"),
            "cluster":         cid,
            "direction":       direction,
            "seed":            seed,
            "bidir_mode":      r.get("bidir_mode", "?"),
            "marginal":        bool(r.get("marginal", False)),
            "soft_fail":       r.get("soft_fail"),  # {name,value,threshold,mode} or None
            "composite_score": _jsonify_val(r.get("composite_score", 0)),
            # Train metrics — included so the UI can show train vs test side-by-side
            # (the `**tr` spread in pattern_discovery_v6 puts these at top level).
            "train_wr":        _jsonify_val(r.get("win_rate_", 0)),
            "train_wilson_wr": _jsonify_val(r.get("wilson_wr", 0)),
            "train_pf":        _jsonify_val(r.get("profit_factor", 0)),
            "train_trades":    _jsonify_val(r.get("total_trades", 0)),
            "train_per_day":   _jsonify_val(r.get("per_day", 0)),
            # Test (out-of-sample) metrics
            "test_score":      _jsonify_val(r.get("test_score", 0)),
            "test_wr":         _jsonify_val(r.get("test_wr", 0)),
            "test_pf":         _jsonify_val(r.get("test_pf", 0)),
            "test_trades":     _jsonify_val(r.get("test_trades", 0)),
            "overall_wr":      _jsonify_val(r.get("overall_wr", 0)),
            "recent_wr":       _jsonify_val(r.get("recent_wr", 0)),
            "consistency":     _jsonify_val(r.get("consistency", 0)),
            "implied_rr":      _jsonify_val(r.get("implied_rr", 0)),
            "sl_pct":          _jsonify_val(r.get("sl_pct", 0)),
            "tp_pct":          _jsonify_val(r.get("tp_pct", 0)),
            "set_file":        set_path,
            # v0.6.0: Rule conditions for the UI's "Indicators" table. Each
            # rule is {col_name: (lower, upper)} mapping a feature/indicator
            # to the inclusive range that must hold for the pattern to fire.
            "genetic_rule":    {
                str(col): [float(lo), float(hi)]
                for col, (lo, hi) in (r.get("genetic_rule") or {}).items()
            } if isinstance(r.get("genetic_rule"), dict) else {},
        })

    # Aggregate overview stats across all surviving patterns — handy
    # at-a-glance numbers above the table.
    def _avg(key: str) -> float | None:
        vals = [r.get(key) for r in patterns_summary if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else None
    overview = {
        "avg_test_wr":  _avg("test_wr"),
        "avg_test_pf":  _avg("test_pf"),
        "avg_train_wr": _avg("train_wr"),
        "avg_train_pf": _avg("train_pf"),
        "total_test_trades": sum(int(r.get("test_trades") or 0) for r in patterns_summary),
    } if patterns_summary else {}

    return {
        "ok": True,
        "patterns_found": len(patterns_summary),
        "patterns": patterns_summary,
        "overview": overview,
        "output_folder": effective_output_folder,
        "overrides_applied": list(overrides.keys()),
    }
