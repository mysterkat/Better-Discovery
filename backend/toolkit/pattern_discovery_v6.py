"""
XAUUSD — Autonomous Pattern Discovery Engine  v6
==================================================
NEW IN v6 (vs v5):
  - shapes_seed PNG removed (plot_cluster_shapes dropped entirely)
  - Regime context fully present in all trade CSVs (regime col was
    already captured in v5; confirmed and kept)
  - Version bumped; combined report header updated to v6

NEW IN v5 (vs v4):
  PERFORMANCE / SPEED
  - Test-set centroid assignment vectorised (matrix subtract, no Python loop)
  - Price-distribution inner loop replaced with fully vectorised NumPy
  - Bidirectional analysis inner loops vectorised with NumPy
  - _score_genetic uses fast NumPy rule evaluation (no Python per-bar loop)
  - POC distance: moving-window via stride_tricks instead of O(n) histogram loop
  - DBSCAN eps estimation uses random subsample (capped at 500 pts)
  - AgglomerativeClustering subset raised to 3 000; nearest-centroid via cdist
  - imap chunksize tuned per stage (encoding 4 000, shape 5 000)
  - Multi-seed batch runner: run N seeds sequentially, merge best results

  NEW STRATEGIES / PATTERNS
  - Stochastic %K / %D oscillator features (added to GENE_COLS + COND_LABELS)
  - Candle-pattern features: inside bar, outside bar, pin-bar score
  - Higher-timeframe momentum divergence score (HTF RSI vs LTF RSI)
  - Rolling Sharpe (20-bar) as a volatility-adjusted signal feature
  - Supply/Demand zone proximity feature (swing-high/swing-low proximity)
  - VWAP distance feature (when volume available)

  ALGORITHM IMPROVEMENTS
  - Genetic: Hall-of-Fame archive (keeps top-10 rules across all generations)
  - Genetic: Gaussian boundary mutation (not just shift)
  - Direction discriminator: Decision-Tree fallback (up to depth-2) for
    multi-condition discrimination (was single-threshold only)
  - Soft filter: patterns that fail only 1 filter get a ⚠ MARGINAL tag
    instead of being silently dropped — still exported, marked yellow
  - Multi-seed runner in CONFIG: set MULTI_SEED_COUNT > 1 to auto-run
    multiple seeds and collect unique patterns across all runs

  BUG FIXES
  - _bt_worker_dir: exit-spread sign was wrong for SHORT (tp_v_eff)
  - compute_price_distributions: ATR normalisation used entry price instead
    of cl[bi], causing inflated fav_atr_max on gap opens
  - time_consistency_score: rewritten to be cleaner (Python 3.14 compatible)

Requirements:
    pip install scikit-learn scipy
    Python 3.14+
"""
from __future__ import annotations
import sys, warnings, random, time, os, textwrap, re


# v1.4.2 — raise per-process CRT stdio handle limit BEFORE any worker spawn.
#
# Why this lives here and not just in backend/app/main.py:
#   The FastAPI parent calls _setmaxstdio(8192) at import time (v1.1.5), but
#   on Windows every mp.Pool worker is spawned as a FRESH Python interpreter
#   (multiprocessing spawn-mode default).  Workers re-import this module and
#   inherit NONE of the parent's CRT state — they start at the default 512
#   handle limit.
#
#   Each worker opens many fds: matplotlib figures, csv writers, pickled
#   shared-memory arrays, intermediate result files.  With MULTI_SEED_COUNT
#   > 1 the accumulated open-file count crosses 512 mid-discovery and the
#   worker crashes with `OSError [Errno 24] Too many open files`.
#
#   Putting the bump in this module ensures it runs once per worker process
#   at spawn time, BEFORE any of the heavy ops touch the fd table.
#
#   Best-effort: missing ctypes / non-Windows / older CRT all fall through
#   silently.  The fix only matters on Windows; POSIX rlimits are handled
#   separately by backend/app/main.py.
def _raise_worker_fd_limit() -> None:
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ucrt = ctypes.CDLL("ucrtbase")
            ucrt._setmaxstdio.restype = ctypes.c_int
            ucrt._setmaxstdio.argtypes = [ctypes.c_int]
            ucrt._setmaxstdio(8192)
    except Exception:
        pass


_raise_worker_fd_limit()

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import multiprocessing as mp
from pathlib import Path
from scipy.stats import norm as _scipy_norm
from scipy.spatial.distance import cdist
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from sklearn.cluster import MiniBatchKMeans, DBSCAN, AgglomerativeClustering
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

# =============================================================================
#  CONFIG
# =============================================================================

# Defaults for standalone/script-mode runs. The Better Discovery app
# always overrides these to point at userdata/hist_data and userdata/discovery
# inside the app tree, so the module-level values only matter if you invoke
# `python pattern_discovery_v6.py` directly. They resolve relative to this
# file's location → portable across machines.
import os as _os
_TOOLKIT_DIR  = _os.path.dirname(_os.path.abspath(__file__))
_REPO_ROOT    = _os.path.dirname(_os.path.dirname(_TOOLKIT_DIR))  # …/BETTER DISCOVERY
DATA_FOLDER   = _os.path.join(_REPO_ROOT, "userdata", "hist_data")
# Up to 5 timeframe slots. Empty filename = slot unused.
TF1_FILE      = "xauusd_m10.csv"
TF2_FILE      = "xauusd_m15.csv"
TF3_FILE      = "xauusd_h1.csv"
TF4_FILE      = ""
TF5_FILE      = ""
# PRIMARY_TF (1-5): which slot drives the bar stream, entries, exits, SL/TP.
# The other non-empty slots become SIGNAL timeframes — their trend/RSI/EMAs
# are merge-asof-joined onto the primary timeline as feature columns
# (tfN_trend, tfN_rsi14, tfN_ema20, ...). The MTF score sums the primary
# trend with every signal trend (range 0..total-non-empty-slots).
PRIMARY_TF    = 1
OUTPUT_FOLDER = _os.path.join(_REPO_ROOT, "userdata", "discovery")

# How the multi-TF bull/bear score is composed across the loaded signal TFs.
#  "additive"  — score = primary_trend + sum(every signal trend),
#                range [0..K] where K = 1 + number of signal TFs.
#                More expressive: every signal TF contributes a discriminative
#                bit. Recommended; matches the bundled MQL5 EA.
#  "overwrite" — score = primary_trend + (last-aligned signal trend),
#                range [0..2]. Legacy behavior. Use only if you must replicate
#                a pre-Group-C run for parity. NOTE: in this mode you should
#                limit the EA's SignalTF1..4 inputs to a single non-empty slot,
#                otherwise live trades will fire on different bars than the
#                .set file's discovery results.
MTF_SCORE_MODE = "additive"

# Which signal-TF RSI feeds the htf_div feature.
#  0 = auto: pick the highest available signal slot (slowest TF, most reliable
#      divergence signal).
#  1..5 = force a specific slot. Must be a non-empty signal slot (not the
#      primary). Falls back to auto if the chosen slot is empty.
HTF_DIV_TF = 0

CORES_RESERVED = 2
RANDOM_SEED    = 3473452712
TRAIN_RATIO    = 0.75

# Set this to the UTC offset of your MT5 broker's server time.
# E.g. UTC+2 in winter (EET), UTC+3 in summer (EEST) for most EU brokers.
# Your CSV must have been exported in this same timezone.
# Verify by checking that known market opens (e.g. London 07:00 local)
# appear at the expected hour in the CSV before applying this offset.
MT5_SERVER_UTC_OFFSET = 2   # adjust to match your broker

# Discard this many bars from the start of each split before evaluation
# to allow EMA(200) and other long-period indicators to converge with MT5's
# values. After ~250 bars of warmup the divergence is negligible (<0.01%).
INDICATOR_WARMUP_BARS = 250

# Multi-seed batch: set to 1 to run once (original behaviour).
# Set to e.g. 8 to run 8 different seeds automatically and collect all
# unique patterns across runs into a combined report.
MULTI_SEED_COUNT = 4   # box-only is slower; 4 diverse seeds for a calibration run (crank after timing known)
MULTI_SEED_BASE  = RANDOM_SEED   # seeds = BASE, BASE+1, BASE+2, …

WINDOW_SIZE = 5
N_CLUSTERS  = 5   # per algorithm per regime

REGIME_MODE           = False
USE_SHAPE_MATCHING    = True
SHAPE_MATCH_THRESHOLD = 0.75
USE_SOFT_FILTER       = False  # FTMO: hard-drop marginal patterns (no ⚠ pass-through)
USE_EXTRA_FEATURES    = True   # v5: stoch, candle patterns, SD zones, rolling Sharpe

FORWARD_BARS            = 24
MEANINGFUL_MOVE_ATR     = 0.6   # price must move >= this x ATR
MEANINGFUL_SUSTAIN_BARS = 4     # and hold for this many bars
SPREAD_PTS              = 0.30
REALISTIC_ENTRY         = True
MAX_HOLD_BARS           = 32
# Per-trade trading costs, expressed in R (risk multiples) so they subtract
# directly from booked outcomes. COMMISSION_R = round-turn commission charged
# once per trade; SWAP_R_PER_BAR = financing/swap charged per bar held.
# Both default 0.0 → behaviour unchanged until a non-zero value is set.
COMMISSION_R            = 0.0
SWAP_R_PER_BAR          = 0.0
ALLOWED_SESSIONS        = []
COOLDOWN_BARS           = 4    # FTMO: longer cooldown → fewer clustered losses

# ── RESEARCH EXTENSIONS (v6.1) ───────────────────────────────────────────────
# Optional research modules (features_ext.py / labels_ext.py) wired in behind
# these flags. ALL DEFAULT OFF — with the defaults below a run is byte-identical
# to the historical engine (the gated code never executes, and the modules are
# only imported when a flag is set). Flip the flags (or override them from the
# UI / a wrapper) to enable the experimental "edge bundle".
#
# USE_EXT_FEATURES      : set of features_ext block tokens to append to the
#                         feature matrix used for clustering. Empty set = OFF.
#                         Valid tokens: {'structure','time','normalize',
#                         'cross_asset'}. The 'rank' idea maps to 'normalize'.
#                         When non-empty, add_all_ext_features() is called in the
#                         feature-build path AND the produced columns are spliced
#                         into the encoded vector X (else they cannot influence
#                         clustering).
# USE_TRIPLE_BARRIER    : route the per-cluster price-distribution / forward-move
#                         stage through labels_ext.triple_barrier (ATR-scaled
#                         López-de-Prado barriers) instead of the raw MFE/MAE
#                         percentile sizing. OFF = current behaviour.
# USE_BETA_NEUTRAL_LABELS: when sizing/scoring the forward move, subtract the
#                         self-detrended drift (labels_ext.beta_neutral_forward_
#                         return) so "long-bias == uptrend" is removed. OFF =
#                         current behaviour. (If both label flags are set,
#                         triple_barrier wins for SL/TP sizing; beta-neutral only
#                         annotates the drift-adjusted direction tally.)
# FORWARD_BARS_SWEEP    : list of forward horizons to evaluate; the engine keeps
#                         the horizon with the strongest sustained-move signal
#                         and reports the choice. Empty list = OFF (use the
#                         single fixed FORWARD_BARS, unchanged behaviour).
USE_EXT_FEATURES        = set()    # e.g. {'structure','time','normalize'}
USE_TRIPLE_BARRIER      = True     # match the app run's labeling (triple_barrier SL/TP)
USE_BETA_NEUTRAL_LABELS = True     # match the app run's labeling (beta-neutral direction)
FORWARD_BARS_SWEEP      = []       # e.g. [12, 24, 48]
# UI-friendly toggles (bool → JSON-serialisable, so spawn-pool workers inherit
# them via _app_override.json; sets/lists do not serialise). These let the app
# drive the research bundle without the UI needing set/list field types.
USE_RESEARCH_FEATURES   = False    # True => ext-feature bundle {'time','structure','normalize'}
USE_FORWARD_SWEEP       = False    # True => sweep [12,24,48,96] when FORWARD_BARS_SWEEP is empty
#
# ── RESEARCH PROFILE (copy-paste to enable the full edge bundle) ─────────────
# To turn on the experimental research stack, set the four flags like so
# (leave everything else at its tuned default):
#
#   USE_EXT_FEATURES        = {'structure', 'time', 'normalize'}
#   USE_TRIPLE_BARRIER      = True
#   USE_BETA_NEUTRAL_LABELS = True
#   FORWARD_BARS_SWEEP      = [12, 24, 48]
#
# Each piece degrades gracefully: if features_ext.py / labels_ext.py are missing
# the engine prints a one-line note and falls back to current behaviour.
# ─────────────────────────────────────────────────────────────────────────────

SL_PCT_QUANTILE = 0.70   # FTMO: tighter stops → smaller worst-case excursions
TP_PCT_QUANTILE = 0.60
MIN_DIST_RR     = 0.30

# REPORT-ONLY (no filtering). The EA fires on the exported feature box (a superset
# of the discovery shape-cluster), so live trade count is inflated vs the cluster.
# signal_retention = total_trades / member_count surfaces that ratio. This cap is
# diagnostic only; None = never used to drop/select patterns.
MAX_BOX_INFLATION = None

# v2.1: optional SL/TP evolution.  When True the GA co-optimizes each rule's
# stop and target as independent multipliers of the cluster's quantile-derived
# baseline, bounded by [SLTP_EVOLVE_MIN, SLTP_EVOLVE_MAX].  Default off → runs
# are identical to fixed-SL/TP behaviour (multiplier pinned at 1.0).
EVOLVE_SLTP     = False
SLTP_EVOLVE_MIN = 0.5
SLTP_EVOLVE_MAX = 2.0

# Genetic
# Budget bumped (was 20/70). Larger pop + more gens lets the GA explore the
# feasible region harder once graded fitness gives it a gradient to climb.
# Runtime scales ~linearly with gens×pop; tune down if a run is too slow.
GENETIC_GENERATIONS      = 120
GENETIC_POPULATION       = 160
GENETIC_MUTATE_RATE      = 0.25
GENE_N_COLS_MIN          = 3
GENE_N_COLS_MAX          = 7
GENE_REPAIR_ATTEMPTS     = 5
GENE_DIVERSITY_THRESHOLD = 0.70
GENE_ISLAND_COUNT        = 4
GENE_MIGRATION_INTERVAL  = 10
# v0.9.x #24 — deterministic crowding replaces island model in pass 1.
# Single island of GENE_ISLAND_COUNT × pop_size with DC replacement;
# maintains diversity without spawning GENE_ISLAND_COUNT separate processes.
# Set to False to revert to island model (for A/B benchmarking).
GENE_USE_CROWDING        = True

# Graded (continuous) fitness. When True, _score_genetic returns small graded
# "sub-feasible" credit for rules that ALMOST qualify (close on retention, trade
# count, or break-even RR) instead of a flat 0.0. This turns the previously flat
# zero-plateau into a gradient the GA can climb, so it reaches the feasible
# region far more reliably (the #1 reason the GA struggled to find passers).
# Feasible rules are offset by _GENE_FEASIBLE_OFFSET so they ALWAYS outrank any
# sub-feasible rule — the final quality bar is unchanged. Set False to revert.
GENE_GRADED_FITNESS      = True
_GENE_FEASIBLE_OFFSET    = 0.30   # feasible scores shifted above the [0,0.30) sub-feasible band

# Box-only (EA-faithful) scoring inside the GA fitness. When True, the GA scores
# every rule on ALL train bars matching its box — what the EA fires — instead of
# cluster/shape members. This is the most honest but also the HEAVIEST path (it can
# hang on loose boxes until the perf work in _score_genetic lands). Mode B keeps
# this OFF (fast cluster-gated GA) while still gating/reporting box-only below.
# MODE A (now active): hang-proofed by MAX_MATCH_FRAC + GENE_CACHE_MAX below.
GENE_SCORE_BOX_ONLY      = True

# Gate + reported metrics use box-only (EA-faithful) numbers regardless of how the
# GA scored. This is the honest-reporting half: the GA may explore cluster-gated
# (fast), but a pattern only PASSES / is reported on what the EA will actually do
# (bt_final + bt_test run box_only, gate reads those). Keep True for honest results.
GATE_BOX_ONLY            = True

# ── Box-only performance guards (make GENE_SCORE_BOX_ONLY viable, no hang) ────
# A loose box (few/no conditions) matches a huge fraction of the 108k-bar train
# set; the per-trade cooldown sim then loops over tens of thousands of bars per
# fitness eval → the GA stalls. MAX_MATCH_FRAC rejects such boxes cheaply BEFORE
# the sim (only in box-only mode), which also steers the GA toward selective,
# tradeable rules. GENE_CACHE_MAX bounds the per-worker match-mask cache so it
# can't balloon over the full bar set (the memory wall that killed workers).
MAX_MATCH_FRAC           = 0.25   # box-only: reject boxes firing on >25% of bars
GENE_CACHE_MAX           = 4000   # max cached (col,lo,hi) masks per worker

# v1.0 surrogate fitness model — wraps the GA optimizer.
# After SURROGATE_MIN_SAMPLES real evals the GBM predicts fitness;
# only SURROGATE_REAL_FRAC of subsequent calls hit the real scorer.
SURROGATE_ENABLED        = False
SURROGATE_REAL_FRAC      = 0.10   # fraction of evals using real fitness
SURROGATE_MIN_SAMPLES    = 40     # real evals before first surrogate fit
SURROGATE_RETRAIN_EVERY  = 20     # retrain after every N new real evals

# v1.3 candidate-generation method.
# "kmeans"   — statistical clustering on standardised features (default,
#              the historical behaviour).  Finds bar groupings, then the
#              optimizer post-hoc tests whether each group is tradeable.
# "lightgbm" — train a GBM regressor on (features → signed best forward
#              move) and use the trees' LEAF PARTITION as cluster labels.
#              Each leaf is, by construction, a feature-conjunction rule
#              whose member bars share similar predicted forward returns.
#              Replaces clustering with profit-aware partitioning; the
#              optimizer then polishes the bounds.  Requires lightgbm.
CLUSTERING_METHOD        = "lightgbm"
# Default ensemble = 1 tree so the cluster count is bounded by num_leaves
# (comparable to KMeans's N_CLUSTERS).  Raising n_estimators multiplies
# cluster count combinatorially — 3 trees × 32 leaves can yield 100-200
# clusters in practice, which is too granular for the downstream pipeline.
LIGHTGBM_N_ESTIMATORS    = 1
LIGHTGBM_NUM_LEAVES      = 32      # more leaf-clusters = more diverse warm-start seeds for box-only GA
LIGHTGBM_MIN_SAMPLES_LEAF = 30     # minimum bars per leaf
LIGHTGBM_LEARNING_RATE   = 0.05    # tree shrinkage

# Pass 2
TOP_FRACTION_PASS2       = 0.25
MIN_TRADES_PER_DAY_PASS2 = 0.5
PASS2_GENERATIONS        = 30
PASS2_POPULATION         = 50
PASS2_MUTATE_RATE        = 0.15
PASS2_QUANTILE_LO        = 0.25
PASS2_QUANTILE_HI        = 0.75

ENSEMBLE_OVERLAP_THRESHOLD = 0.60

# Bidirectional
BIDIR_MIN_WR         = 52.0
BIDIR_MIN_TRADES     = 15
DISCRIM_MIN_ACCURACY = 0.62

# FORCE_DIRECTION — lock all clusters to a single trade direction at the
# engine level.  Solves the "MT5 long-only doesn't match Discovery" gap:
# when BIDIRECTIONAL clusters get DirectionMode=2 (.set), the EA relies on
# the discriminator to pick LONG vs SHORT per-bar.  If the user then forces
# the EA long-only, ~50% of intended trades silently vanish.
#   "auto"       — empirical classification (default, current behaviour)
#   "long_only"  — force every cluster to LONG_ONLY, no discriminator emitted
#   "short_only" — force every cluster to SHORT_ONLY, no discriminator emitted
FORCE_DIRECTION      = "auto"

# Scoring weights
SCORE_W_WR              = 0.30
SCORE_W_PF              = 0.30
SCORE_W_RR              = 0.25
SCORE_W_STAB            = 0.25   # FTMO: weight consistency higher in GA fitness
SCORE_WILSON_CONFIDENCE = 0.85

# Rule-complexity bonus (Bug #32 — "force more indicators").
# Soft additive bonus that grows linearly with len(rule) above GENE_N_COLS_MIN,
# reaching the full weight at GENE_N_COLS_MAX.  Set > 0 to bias the GA toward
# richer rules when many candidates land on 3-4 columns.
# 0.00 = off (default)
# 0.05 = mild preference (tie-breaker)
# 0.15 = strong preference
SCORE_W_RULE_COMPLEXITY = 0.0

# ── v0.6.0: Target-driven scoring ────────────────────────────────────────────
# When ENABLE_TARGET_SCORING is True, _score_genetic switches from "maximise
# everything" mode to "hit specific targets". Below target → quadratic penalty
# pulls the GA up hard. At target → full reward. Above target → tiny log bonus
# so the GA doesn't sacrifice another objective to push one metric higher.
#
# The EXCESS_BONUS_WEIGHT knob controls how strict the targeting is:
#   0.0  → strict, no benefit to exceeding (GA stops climbing once targets hit)
#   0.1  → mild (default) — exceed only when it's free
#   0.3  → lenient — modest excess reward
#   1.0  → legacy maximise-everything behaviour
ENABLE_TARGET_SCORING   = True
TARGET_WR_PCT           = 55.0   # win-rate goal (%)
TARGET_PF               = 1.5    # profit-factor goal
TARGET_RR               = 1.3    # R:R goal (avg payout / risk)
TARGET_STABILITY        = 0.75   # FTMO: demand higher time-consistency (0..1)
TARGET_TRADES_PER_DAY   = 1.0    # trades-per-day goal
EXCESS_BONUS_WEIGHT     = 0.1

SCORE_W_TRADES_PER_DAY = 0.15   # weight for trades/day target component

# Quality filters
MIN_FREQ_PER_DAY        = 0.3
# WR and PF floors are derived from their targets rather than set by hand:
#   floor = breakeven + FILTER_EDGE_K * (target - breakeven)
# Breakeven is each metric's no-edge point (50% WR, 1.0 PF), so the floor
# demands a fixed fraction of the edge the target aims for. One knob keeps the
# floors coherent across the two scales and auto-tracks the targets. See
# _wr_floor()/_pf_floor(). FTMO: at k=0.45: WR 52.25, PF 1.225.
FILTER_EDGE_K           = 0.30   # realistic floors for honest box-only: PF>=1.15, WR>=51.5
WR_BREAKEVEN            = 50.0
PF_BREAKEVEN            = 1.0
MAX_DRAWDOWN_R          = 12.0   # soft now; raised so it's not a constant marginal-fail
MAX_CONSEC_LOSSES       = 8      # soft now; realistic for natural variance
MIN_TIME_CONSISTENCY    = 0.35   # soft now; realistic OOS consistency bar
MIN_TEST_TRADES_PER_DAY = 0.3
# Hard/soft filter gate. A pattern is DROPPED if it fails any HARD filter
# (true disqualifiers: must be profitable + actually trade enough out-of-sample).
# Otherwise it PASSES — tagged MARGINAL — as long as it fails at most
# MAX_SOFT_FAILS of the remaining SOFT (quality-proxy) filters. This stops a
# profitable, tradeable rule from being rejected for a few proxy breaks; the real
# FTMO verdict comes from the MC sim + EA-OOS + your MT5 backtest, not this
# pre-screen. Move any name into HARD_FILTERS to make it a hard veto again, or set
# MAX_SOFT_FAILS=0 to require ALL soft filters (the old strict behaviour).
HARD_FILTERS            = {"profit_factor", "per_day", "test_trades"}
MAX_SOFT_FAILS          = 4
CORRELATION_THRESHOLD   = 0.70
RECENT_BARS             = 8000

# Auto-run MC on top-N passing patterns (test split only)
RUN_MC_ON_TOP_N   = 5
MC_N_SIMS         = 10000
MC_BALANCE        = 100000
MC_LOT            = 0.10
MC_MAX_DAYS       = 60

# ── Optional AI-in-the-loop review (ADVISORY ONLY, OFF by default) ────────────
# When enabled, the finished run's ranked patterns + a config snapshot are sent
# to an OpenAI-compatible LLM endpoint (local Ollama / LM Studio, or DeepSeek
# cloud) and a markdown critique is written next to the report
# (ai_review_seed{seed}.md). The reviewer NEVER gates, drops, or re-ranks
# patterns — with this off the run is byte-identical to the engine without it,
# and any AI failure degrades to a one-line note. See backend/toolkit/ai_review.py.
# Enable via this flag (app-overridable) or env BD_AI_REVIEW=1. Endpoint/model
# resolve from BD_AI_BASE_URL / BD_AI_MODEL / BD_AI_API_KEY (or
# DEEPSEEK_API_KEY); with no key the default is a local Ollama at
# http://localhost:11434/v1.
AI_REVIEW_ENABLED   = False
AI_REVIEW_BASE_URL  = ""     # "" = auto (DeepSeek if a key is set, else local Ollama)
AI_REVIEW_MODEL     = ""     # "" = auto (deepseek-chat / llama3.1)
AI_REVIEW_TIMEOUT_S = 120

# =============================================================================
#  INTERNALS
# =============================================================================

def _edge_floor(target: float, breakeven: float, k: float) -> float:
    """Floor = breakeven + k*(target - breakeven): demand fraction k of the
    edge above the no-edge point. Read globals at call-time so UI overrides of
    the targets / k (via setattr or _app_override.json) take effect."""
    return breakeven + k * (target - breakeven)

def _wr_floor() -> float:
    k  = float(globals().get("FILTER_EDGE_K",  0.30))
    be = float(globals().get("WR_BREAKEVEN",  50.0))
    return _edge_floor(float(globals().get("TARGET_WR_PCT", 55.0)), be, k)

def _pf_floor() -> float:
    k  = float(globals().get("FILTER_EDGE_K", 0.30))
    be = float(globals().get("PF_BREAKEVEN",   1.0))
    return _edge_floor(float(globals().get("TARGET_PF", 1.5)), be, k)

BG    = "#0d1117"; PANEL = "#161b22"; GRID  = "#21262d"
UP    = "#26a69a"; DOWN  = "#ef5350"; TEXT  = "#e6edf3"; MUTED = "#8b949e"
PALETTE = ["#00e676","#ffd600","#40c4ff","#ff9100","#ea80fc",
           "#ff1744","#00b0ff","#76ff03","#ff6d00","#e040fb",
           "#80cbc4","#fff176","#b39ddb","#ef9a9a","#a5d6a7",
           "#90caf9","#ffcc80","#ce93d8","#80deea","#ffab91"]

REGIME_NAMES = {0:"TREND_UP",1:"TREND_DOWN",2:"RANGE_SQUEEZE",
                3:"RANGE_WIDE",4:"CHOPPY"}

GENE_COLS = ["rsi14","macd_norm","atr_pct","bb_width","trend",
             "mtf_bull_score","body_pct","rng_atr","session",
             "vol_ratio","vol_body_conf","regime","vol_price_div",
             "bb_expanding","prev_sess_bias",
             # v5 new features
             "stoch_k","stoch_d","pin_bar","inside_bar","outside_bar",
             "htf_div","rolling_sharpe","sd_zone","vwap_dist"]

# The EA's feat[27] vector, in COLUMN INDEX TABLE order (must stay in sync
# with PatternDiscoveryEA.mq5 and set_to_mql._COLS). Used to snapshot the
# full feature state at each trade's signal bar into the exported trade CSVs
# (feat_* columns) — the training data for the ONNX trade filter.
EA_FEATURE_COLS = [
    "rsi14", "macd_norm", "atr_pct", "bb_width", "trend", "mtf_bull_score",
    "body_pct", "rng_atr", "vol_ratio", "vol_body_conf", "regime",
    "vol_price_div", "bb_expanding", "prev_sess_bias", "poc_dist", "bull",
    "uwk_pct", "lwk_pct", "stoch_k", "stoch_d", "pin_bar", "inside_bar",
    "outside_bar", "htf_div", "rolling_sharpe", "sd_zone", "vwap_dist",
]

DIRECTIONAL_COLS = {"trend","bull","macd_norm","rsi14","stoch_k","stoch_d","htf_div"}

DISCRIM_COLS = ["trend","bull","macd_norm","rsi14","session",
                "poc_dist","mtf_bull_score","vol_ratio","bb_width",
                "vol_price_div","prev_sess_bias",
                "stoch_k","stoch_d","htf_div","rolling_sharpe","sd_zone"]

COND_LABELS = {
    "rsi14":          "RSI(14)            [0-100]",
    "macd_norm":      "MACD_hist/ATR      [normalised]",
    "atr_pct":        "ATR/Close          [volatility%]",
    "bb_width":       "BB width/midline   [squeeze<0.01]",
    "trend":          "EMA trend          [-1=dn 0=range 1=up]",
    "mtf_bull_score": "MTF bull score     [0-2]",
    "mtf_bear_score": "MTF bear score     [0-2]",
    "body_pct":       "Body/Range         [0-1]",
    "rng_atr":        "Range/ATR          [1=avg bar]",
    "session":        "Session            [0=Asian..4=Off]",
    "vol_ratio":      "Volume/MA20        [1=avg vol]",
    "vol_body_conf":  "Vol x Body         [confirmation]",
    "poc_dist":       "Dist from POC      [% of price]",
    "regime":         "Market regime      [0=TrendUp..4=Choppy]",
    "bull":           "Bullish candle     [0=bear 1=bull]",
    "vol_price_div":  "Vol-price diverge  [+1=accum -1=distrib]",
    "bb_expanding":   "BB expanding       [0=no 1=yes]",
    "prev_sess_bias": "Prev session bias  [-1=bear 0=flat 1=bull]",
    # v5
    "stoch_k":        "Stochastic %K      [0-100]",
    "stoch_d":        "Stochastic %D      [0-100]",
    "pin_bar":        "Pin-bar score      [0=none 1=strong]",
    "inside_bar":     "Inside bar         [0=no 1=yes]",
    "outside_bar":    "Outside bar        [0=no 1=yes]",
    "htf_div":        "HTF RSI divergence [+1=bull -1=bear 0=none]",
    "rolling_sharpe": "Rolling Sharpe(20) [risk-adj momentum]",
    "sd_zone":        "S/D zone proximity [+1=near supp -1=near res]",
    "vwap_dist":      "VWAP distance      [% from VWAP, 0 if no vol]",
}


# ── Spawn-worker override propagation ────────────────────────────────────────
# Multiprocessing workers (mp.Pool) re-import this module under spawn-mode,
# which means setattr() overrides applied in the parent process never reach
# them. To work around this, the launcher (BD bridge or __main__) writes
# `_app_override.json` next to this file BEFORE spawning workers; the loader
# below runs at module-import time so each worker also picks the overrides up.
def _load_app_overrides() -> None:
    try:
        from pathlib import Path as _P
        import json as _json
        _ov = _P(__file__).parent / "_app_override.json"
        if not _ov.exists():
            return
        for _k, _v in _json.loads(_ov.read_text()).items():
            if _k in globals():
                globals()[_k] = _v
    except Exception:
        # Don't let a malformed JSON kill the import — workers will just
        # fall back to file defaults, which is no worse than before.
        pass

_load_app_overrides()


def _n_workers(): return max(1,(os.cpu_count() or 4)-CORES_RESERVED)
def _elapsed(t0):
    s=int(time.time()-t0); return f"{s//60}m {s%60}s"
def _pbar(cur,tot,label="",width=42,t0=None):
    pct=cur/max(tot,1); filled=int(width*pct)
    bar="#"*filled+"-"*(width-filled); eta=""
    if t0 and cur>0:
        rem=(time.time()-t0)/pct-(time.time()-t0) if pct>0 else 0
        eta=f"  ETA {int(rem//60)}m{int(rem%60):02d}s"
    print(f"\r  [{bar}] {pct*100:5.1f}%  {cur}/{tot}  {label}{eta}",
          end="",flush=True)
    if cur>=tot: print()

# ─────────────────────────────────────────────────────────────────────────────
# DATA & INDICATORS
# ─────────────────────────────────────────────────────────────────────────────
def _load_raw(path):
    df=pd.read_csv(path); df.columns=df.columns.str.strip().str.lower()
    tc=next((c for c in df.columns if c in
             {"time","date","datetime","timestamp"}),df.columns[0])
    df[tc]=pd.to_datetime(df[tc])
    df=df.set_index(tc).sort_index(); df.index.name="time"
    for vc in ("tick_volume","volume","vol"):
        if vc in df.columns:
            df.rename(columns={vc:"volume"},inplace=True); break
    cols=["open","high","low","close"]+(["volume"] if "volume" in df.columns else [])
    return df[cols].astype(float)

def _ema(s,n): return s.ewm(span=n,adjust=False).mean()
def _rsi(s,n=14):
    d=s.diff(); g=d.clip(lower=0).ewm(com=n-1,adjust=False).mean()
    l=(-d.clip(upper=0)).ewm(com=n-1,adjust=False).mean()
    return 100-100/(1+g/l.replace(0,np.nan))
def _atr(h,lo,c,n=14):
    tr=pd.concat([h-lo,(h-c.shift()).abs(),(lo-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(com=n-1,adjust=False).mean()
def _bbands(s,n=20,std=2.0):
    m=s.rolling(n).mean(); sg=s.rolling(n).std(ddof=0); return m+std*sg,m,m-std*sg

def _add_indicators(df):
    c=df["close"]
    df["ema20"]=_ema(c,20); df["ema50"]=_ema(c,50); df["ema200"]=_ema(c,200)
    df["rsi14"]=_rsi(c,14); df["atr14"]=_atr(df["high"],df["low"],c,14)
    df["bb_up"],df["bb_mid"],df["bb_lo"]=_bbands(c)
    df["bb_width"]=(df["bb_up"]-df["bb_lo"])/df["bb_mid"]
    macd=_ema(c,12)-_ema(c,26); df["macd_hist"]=macd-_ema(macd,9)
    up=(df["ema20"]>df["ema50"])&(df["ema50"]>df["ema200"])
    dn=(df["ema20"]<df["ema50"])&(df["ema50"]<df["ema200"])
    df["trend"]=np.where(up,1,np.where(dn,-1,0))
    df["rng"]=df["high"]-df["low"]; df["body"]=(c-df["open"]).abs()
    df["uwk"]=df["high"]-df[["open","close"]].max(axis=1)
    df["lwk"]=df[["open","close"]].min(axis=1)-df["low"]
    df["bull"]=(c>=df["open"]).astype(int)
    atr_s=df["atr14"].replace(0,np.nan); rng_s=df["rng"].replace(0,np.nan)
    df["atr_pct"]=df["atr14"]/c; df["rng_atr"]=df["rng"]/atr_s
    df["body_pct"]=df["body"]/rng_s; df["uwk_pct"]=df["uwk"]/rng_s
    df["lwk_pct"]=df["lwk"]/rng_s; df["macd_norm"]=df["macd_hist"]/atr_s
    # Shift index to match MT5 broker server time before computing sessions.
    # MT5's Hour() returns broker server time; align Python to the same timezone.
    h=(df.index.hour+MT5_SERVER_UTC_OFFSET)%24; sess=np.full(len(df),4,dtype=np.int8)
    # OFF=4 default; assign in order so later writes override earlier
    # No overlap: ASIAN 02-07, LONDON 07-12, NY 12-17, OVERLAP 17-21
    sess[(h>=2)&(h<7)]=0   # ASIAN (02:00-06:59)
    sess[(h>=7)&(h<12)]=1  # LONDON (07:00-11:59)
    sess[(h>=12)&(h<17)]=2 # NY (12:00-16:59)
    sess[(h>=17)&(h<21)]=3 # OVERLAP (17:00-20:59)
    df["session"]=sess
    return df.fillna(0)

def add_extended_features(df):
    """Volume profile, divergence, BB expansion, cross-session carry."""
    if "volume" not in df.columns:
        for col in ["vol_ratio","vol_trend","vol_body_conf","poc_dist",
                    "vol_price_div","bb_expanding","prev_sess_bias"]:
            df[col]=0.0
        return df

    vol=df["volume"].replace(0,np.nan).fillna(1)
    vol_ma=vol.rolling(20,min_periods=1).mean()
    df["vol_ratio"]=(vol/vol_ma).clip(0,5)
    df["vol_body_conf"]=(df["vol_ratio"]*df["body_pct"]).clip(0,5)

    # Volume-price divergence
    # +1 = price down on high vol (accumulation), -1 = price up on low vol (distribution)
    price_dir=(df["close"]-df["open"]).apply(np.sign)
    vol_above_avg=(df["vol_ratio"]>1.2).astype(int)
    diverge=np.zeros(len(df))
    # price up + below avg vol = possible distribution = -1
    diverge[(price_dir.values>0)&(vol_above_avg.values==0)]=-1
    # price down + above avg vol = possible accumulation = +1
    diverge[(price_dir.values<0)&(vol_above_avg.values==1)]=1
    df["vol_price_div"]=diverge

    # BB width expanding (1 = expanding, 0 = contracting/flat)
    bb_w=df["bb_width"]
    df["bb_expanding"]=(bb_w>bb_w.shift(3)).astype(float)

    # POC distance — v5 speedup: compute every STEP bars, forward-fill the rest
    # This gives ~5x speedup with negligible accuracy loss (POC changes slowly)
    poc_dist=np.zeros(len(df))
    closes=df["close"].values; vols=vol.values
    highs=df["high"].values; lows=df["low"].values
    lookback=100; n_bins=20; STEP=5
    last_poc=0.0
    for i in range(lookback,len(df)):
        if i % STEP == 0:
            sl_hi=highs[i-lookback:i]; sl_lo=lows[i-lookback:i]
            sl_vol=vols[i-lookback:i]; sl_cl=closes[i-lookback:i]
            pr=sl_hi.max()-sl_lo.min()
            if pr>0:
                lo_=sl_lo.min(); hi_=sl_hi.max()
                bv,edges=np.histogram(sl_cl,bins=n_bins,range=(lo_,hi_),weights=sl_vol)
                last_poc=edges[np.argmax(bv)]+(edges[1]-edges[0])/2
        if last_poc>0:
            poc_dist[i]=(closes[i]-last_poc)/(closes[i]+1e-9)
    df["poc_dist"]=poc_dist

    # Cross-session carry — vectorised via session boundary detection
    sess_arr=df["session"].values; close_arr=df["close"].values
    prev_bias=np.zeros(len(df))
    # find where session changes
    sess_changes=np.where(np.diff(sess_arr)!=0)[0]+1  # indices where new session starts
    sess_changes=np.concatenate([[0],sess_changes,[len(df)]])
    for k in range(1,len(sess_changes)-1):
        seg_start=sess_changes[k]; seg_end=sess_changes[k+1]
        prev_start=sess_changes[k-1]; prev_end=sess_changes[k]
        if prev_end<=prev_start: continue
        prev_open=close_arr[prev_start]; prev_close=close_arr[prev_end-1]
        bias=1 if prev_close>prev_open else (-1 if prev_close<prev_open else 0)
        prev_bias[seg_start:seg_end]=bias
    df["prev_sess_bias"]=prev_bias
    return df

def add_v5_features(df):
    """v5 features: Stochastic, candle patterns, HTF divergence, rolling Sharpe, S/D zone, VWAP."""
    hi=df["high"]; lo=df["low"]; cl=df["close"]; op=df["open"]
    atr=df["atr14"].replace(0,np.nan)
    lo14=lo.rolling(14,min_periods=1).min(); hi14=hi.rolling(14,min_periods=1).max()
    df["stoch_k"]=(100*(cl-lo14)/(hi14-lo14+1e-9)).clip(0,100)
    df["stoch_d"]=df["stoch_k"].rolling(3,min_periods=1).mean()
    rng=(hi-lo).replace(0,np.nan)
    uwk=hi-pd.concat([op,cl],axis=1).max(axis=1)
    lwk=pd.concat([op,cl],axis=1).min(axis=1)-lo
    df["pin_bar"]=(pd.concat([uwk,lwk],axis=1).max(axis=1)/rng).clip(0,1).fillna(0)
    df["inside_bar"]=((hi<hi.shift(1))&(lo>lo.shift(1))).astype(float)
    df["outside_bar"]=((hi>hi.shift(1))&(lo<lo.shift(1))).astype(float)
    rsi=df["rsi14"]; ltf_slope=rsi-rsi.shift(5)
    # Pick the signal-TF RSI that drives htf_div.
    #  - If HTF_DIV_TF in 1..5 and that slot's RSI column exists, use it.
    #  - Otherwise auto-pick the HIGHEST-numbered (= slowest, most reliable)
    #    available signal slot. Standalone v6 hardcoded tf2; auto picks the
    #    slowest non-primary slot for stronger divergence signals.
    #  - Falls back to a flat 50 series if no signal TFs are loaded.
    _htf_rsi_choice = int(globals().get("HTF_DIV_TF", 0) or 0)
    _signal_rsi_cols = [
        f"tf{n}_rsi14" for n in (5, 4, 3, 2)  # slowest first for auto-pick
        if f"tf{n}_rsi14" in df.columns
    ]
    if 1 <= _htf_rsi_choice <= 5 and f"tf{_htf_rsi_choice}_rsi14" in df.columns:
        htf_rsi = df[f"tf{_htf_rsi_choice}_rsi14"]
    elif _signal_rsi_cols:
        htf_rsi = df[_signal_rsi_cols[0]]
    else:
        htf_rsi = pd.Series(50, index=df.index)
    htf_slope=(htf_rsi-htf_rsi.shift(3)).fillna(0)
    htf_div=np.zeros(len(df))
    htf_div[(ltf_slope.values>2)&(htf_slope.values<-1)]=1
    htf_div[(ltf_slope.values<-2)&(htf_slope.values>1)]=-1
    df["htf_div"]=htf_div
    ret=cl.pct_change(); roll_std=ret.rolling(20,min_periods=5).std().replace(0,np.nan)
    df["rolling_sharpe"]=(ret.rolling(20,min_periods=5).mean()/roll_std).clip(-3,3).fillna(0)
    swing_hi=hi.rolling(25,min_periods=5).max(); swing_lo=lo.rolling(25,min_periods=5).min()
    sd=np.zeros(len(df))
    sd[((cl-swing_lo)/(atr+1e-9)).values<1.0]=1
    sd[((swing_hi-cl)/(atr+1e-9)).values<1.0]=-1
    df["sd_zone"]=sd
    if "volume" in df.columns:
        vol=df["volume"].replace(0,np.nan).fillna(1); tp=(hi+lo+cl)/3
        vwap=(tp*vol).rolling(96,min_periods=1).sum()/vol.rolling(96,min_periods=1).sum()
        df["vwap_dist"]=((cl-vwap)/vwap*100).clip(-5,5).fillna(0)
    else:
        df["vwap_dist"]=0.0
    # ── RESEARCH: optional extended features (gated on USE_EXT_FEATURES) ──────
    # When the flag set is non-empty, append the features_ext blocks here so the
    # new columns exist on df BEFORE build_features_parallel encodes the matrix.
    # The columns are also registered (ext_feature_columns) and spliced into X
    # by the encoder — see _ext_feature_cols()/build_features_parallel. Default
    # (empty set) leaves df untouched: pure no-op, byte-identical output.
    df=_maybe_add_ext_features(df)
    return df.fillna(0)


# ── RESEARCH helper: extended-feature column registry ────────────────────────
# add_all_ext_features (features_ext.py) appends a deterministic, fixed set of
# columns for a given `enable` set. We materialise that column list ONCE on a
# tiny synthetic frame so the encoder knows exactly which columns to read — and
# in which order — without depending on a particular data frame. The list is
# cached at module level and passed into the mp.Pool workers via initargs
# (Windows spawn re-imports this module and resets globals to their defaults, so
# the worker MUST receive the list explicitly, never read USE_EXT_FEATURES).
_EXT_FEATURE_COLS_CACHE = None   # None = not computed yet; list once resolved

def _resolved_ext_enable():
    """Resolve the active extended-feature set.

    Explicit USE_EXT_FEATURES wins (power users / file edits). Otherwise the
    UI-friendly USE_RESEARCH_FEATURES bool turns on a curated bundle. Read at
    call time so UI overrides AND spawn workers (which inherit the bool via
    _app_override.json) both resolve identically. Returns a set (may be empty)."""
    explicit = set(globals().get("USE_EXT_FEATURES", set()) or set())
    if explicit:
        return explicit
    if bool(globals().get("USE_RESEARCH_FEATURES", False)):
        return {"time", "structure", "normalize"}
    return set()

def _ext_feature_cols():
    """Return the ordered list of column names add_all_ext_features adds for the
    currently-enabled USE_EXT_FEATURES set (read at call time so UI overrides
    apply). Returns [] when the flag is empty OR the module is unavailable.

    Deterministic: derived from a fixed synthetic OHLC frame, so the same
    enable-set always yields the same columns in the same order. Cached after
    the first successful resolution.
    """
    global _EXT_FEATURE_COLS_CACHE
    enable=_resolved_ext_enable()
    if not enable:
        return []
    if _EXT_FEATURE_COLS_CACHE is not None:
        return _EXT_FEATURE_COLS_CACHE
    try:
        import features_ext as _fx
    except ImportError as e:
        print(f"  [research] features_ext unavailable ({e}); "
              f"USE_EXT_FEATURES ignored, falling back to base features.")
        _EXT_FEATURE_COLS_CACHE=[]
        return []
    # Synthetic 64-bar OHLC frame with a DatetimeIndex (time block needs one).
    idx=pd.date_range("2020-01-01", periods=64, freq="5min")
    base=np.linspace(100.0,101.0,64)
    probe=pd.DataFrame({"open":base,"high":base+0.5,"low":base-0.5,
                        "close":base,"volume":1.0}, index=idx)
    try:
        out=_fx.add_all_ext_features(probe, enable=enable)
    except Exception as e:
        print(f"  [research] features_ext probe failed ({e}); "
              f"USE_EXT_FEATURES ignored.")
        _EXT_FEATURE_COLS_CACHE=[]
        return []
    new_cols=[c for c in out.columns if c not in probe.columns]
    _EXT_FEATURE_COLS_CACHE=new_cols
    print(f"  [research] USE_EXT_FEATURES={sorted(enable)} -> "
          f"{len(new_cols)} extra feature column(s) enter X: {new_cols}")
    return new_cols

def _maybe_add_ext_features(df):
    """Append features_ext columns to `df` when USE_EXT_FEATURES is non-empty.
    Lazy-imports features_ext; on ImportError prints a note and returns df
    unchanged (current behaviour). No-op when the flag set is empty."""
    enable=_resolved_ext_enable()
    if not enable:
        return df
    try:
        import features_ext as _fx
    except ImportError as e:
        print(f"  [research] features_ext unavailable ({e}); "
              f"USE_EXT_FEATURES ignored, using base features only.")
        return df
    try:
        df=_fx.add_all_ext_features(df, enable=enable)
    except Exception as e:
        print(f"  [research] add_all_ext_features failed ({e}); "
              f"continuing with base features.")
        return df
    # Guarantee every registered ext column exists (defensive: keeps X width
    # constant even if a block silently skips a column on degenerate data).
    for c in _ext_feature_cols():
        if c not in df.columns:
            df[c]=0.0
    return df

def detect_regimes(df):
    up=(df["ema20"]>df["ema50"])&(df["ema50"]>df["ema200"])
    dn=(df["ema20"]<df["ema50"])&(df["ema50"]<df["ema200"])
    atr_med=df["atr_pct"].rolling(200,min_periods=50).median()
    hi_vol=df["atr_pct"]>atr_med*1.1; lo_vol=df["atr_pct"]<atr_med*0.9
    squeeze=df["bb_width"]<df["bb_width"].rolling(100,min_periods=20).quantile(0.25)
    wide_bb=df["bb_width"]>df["bb_width"].rolling(100,min_periods=20).quantile(0.75)
    regime=np.full(len(df),4,dtype=np.int8)
    regime[squeeze.values&lo_vol.values]=2
    regime[(~up.values)&(~dn.values)&wide_bb.values]=3
    regime[dn.values&hi_vol.values]=1
    regime[up.values&hi_vol.values]=0
    df["regime"]=regime
    return df

def _resample(df,rule):
    agg={"open":"first","high":"max","low":"min","close":"last"}
    if "volume" in df.columns: agg["volume"]="sum"
    return df.resample(rule).agg(agg).dropna(subset=["open"])

def _align_htf(base, htf, prefix):
    """Merge-asof a signal timeframe's columns onto the primary timeline and
    fold its trend into the MTF score.

    Score model is governed by MTF_SCORE_MODE:
      "additive"  — sum primary + every signal trend, range [0..K].
      "overwrite" — score = primary + last signal trend, range [0..2].
                    Each call reseeds with primary so only the *last* signal
                    aligned contributes (matches pre-Group-C standalone).
    """
    cols = [c for c in ["trend", "rsi14", "ema20", "ema50", "atr14"] if c in htf.columns]
    htf_s = htf[cols].reset_index().rename(columns={"time": "htf_time"})
    merged = pd.merge_asof(
        base.reset_index().sort_values("time"),
        htf_s.sort_values("htf_time"),
        left_on="time", right_on="htf_time",
        direction="backward", suffixes=("", f"_{prefix}"),
    ).set_index("time").sort_index()
    for col in cols:
        src = f"{col}_{prefix}" if f"{col}_{prefix}" in merged.columns else col
        if src in merged.columns:
            base[f"{prefix}_{col}"] = merged[src].values

    t_sig = base.get(f"{prefix}_trend", pd.Series(0, index=base.index))
    t_primary = base["trend"]

    mode = str(globals().get("MTF_SCORE_MODE", "additive")).lower()
    if mode == "overwrite":
        # Reseed every call with primary, then add this signal's trend.
        # Final value = primary + last-aligned signal. Range [0..2].
        base["mtf_bull_score"] = (t_primary == 1).astype(int) + (t_sig == 1).astype(int)
        base["mtf_bear_score"] = (t_primary == -1).astype(int) + (t_sig == -1).astype(int)
    else:
        # additive (default)
        if "mtf_bull_score" not in base.columns:
            base["mtf_bull_score"] = (t_primary == 1).astype(int)
            base["mtf_bear_score"] = (t_primary == -1).astype(int)
        base["mtf_bull_score"] = base["mtf_bull_score"] + (t_sig == 1).astype(int)
        base["mtf_bear_score"] = base["mtf_bear_score"] + (t_sig == -1).astype(int)
    return base

def load_raw_data():
    """Load the user-selected primary TF and merge any non-empty signal TFs.

    PRIMARY_TF (1..5) names which slot is the bar stream. Every other
    non-empty slot is folded in as a signal — its OHLC-derived columns
    are renamed `tf{N}_*` and asof-joined onto the primary timeline, and
    its trend contributes to the cumulative `mtf_bull/bear_score`.
    """
    tf_files = [TF1_FILE, TF2_FILE, TF3_FILE, TF4_FILE, TF5_FILE]
    primary_idx = max(1, min(5, int(PRIMARY_TF))) - 1   # 0-based slot index
    primary_file = tf_files[primary_idx]
    if not primary_file:
        raise ValueError(
            f"PRIMARY_TF={PRIMARY_TF} but TF{primary_idx + 1}_FILE is empty"
        )

    primary_path = str(Path(DATA_FOLDER) / primary_file)
    print(f"  TF{primary_idx + 1} (PRIMARY): {primary_path}")
    df = _add_indicators(_load_raw(primary_path))
    print(f"     {len(df):,} bars  {df.index[0]} -> {df.index[-1]}")

    for slot_idx, fname in enumerate(tf_files):
        if slot_idx == primary_idx or not fname:
            continue
        prefix = f"tf{slot_idx + 1}"
        path = Path(DATA_FOLDER) / fname
        print(f"  TF{slot_idx + 1} (signal): {path}")
        df_sig = _add_indicators(_load_raw(str(path)))
        df = _align_htf(df, df_sig, prefix)

    # NOTE: add_extended_features and detect_regimes are called AFTER
    # the train/test split in main() to prevent look-ahead contamination
    return df.fillna(0)

# ─────────────────────────────────────────────────────────────────────────────
# ENCODING (parallel) — snapshot + sequence
# ─────────────────────────────────────────────────────────────────────────────
_ENC={}
# RESEARCH: names of the extended-feature columns spliced into the encoded
# vector, in append order. Set inside _init_enc from the value passed via the
# mp.Pool initargs (worker-safe: NOT read from the USE_EXT_FEATURES global,
# which resets to its default on Windows spawn). Empty => no ext columns.
_ENC_EXT_NAMES=[]

def _init_enc(*arrs):
    """Pool initializer. The FIRST 18 positional arrays are the historical base
    feature columns (order matches `keys` below). RESEARCH: any FURTHER arrays
    are extended-feature columns whose names arrive as the FINAL argument (a
    tuple/list). When no ext features are enabled the call is exactly the
    historical 18-array form and the tail logic is skipped — byte-identical."""
    global _ENC,_ENC_EXT_NAMES
    keys=["closes","bodies","rngs","uwks","lwks","bulls","rsi","macd","bbw",
          "trend","mtf","atr","vol_ratio","vol_body","regime",
          "vol_div","bb_exp","prev_sess"]
    n_base=len(keys)
    if len(arrs)>n_base:
        # Trailing element is the ordered list of ext column names; the arrays
        # between the base block and that name list are the ext columns.
        ext_names=list(arrs[-1])
        ext_arrs=arrs[n_base:n_base+len(ext_names)]
        _ENC_EXT_NAMES=ext_names
        _ENC=dict(zip(keys,arrs[:n_base]))
        for nm,a in zip(ext_names,ext_arrs):
            _ENC[f"ext::{nm}"]=a
    else:
        _ENC_EXT_NAMES=[]
        _ENC=dict(zip(keys,arrs))

def _encode_one(args):
    i,w=args; e=_ENC
    if not e or i<w: return None
    bl=e["closes"][i-w]; atr_v=e["atr"][i]
    if bl==0 or atr_v==0 or np.isnan(atr_v): return None

    # ── snapshot features ──
    feats=list((e["closes"][i-w:i]-bl)/bl*100)
    for j in range(w):
        rng_j=e["rngs"][i-w+j]
        if rng_j>0:
            feats+=[e["bodies"][i-w+j]/rng_j,
                    e["uwks"][i-w+j]/rng_j,
                    e["lwks"][i-w+j]/rng_j]
        else: feats+=[0.0,0.0,0.0]
        feats.append(float(e["bulls"][i-w+j]))

    # ── sequence features: momentum and volatility paths ──
    rsi_path=e["rsi"][i-w:i]
    atr_path=e["atr"][i-w:i]
    if len(rsi_path)==w and len(atr_path)==w:
        # RSI slope over window (positive = rising, negative = falling)
        feats.append(float(np.polyfit(range(w),rsi_path,1)[0])/100.0)
        # ATR slope (expanding or contracting volatility)
        feats.append(float(np.polyfit(range(w),atr_path,1)[0])/(atr_v+1e-9))
        # RSI path position: where did it come from vs where is it now
        feats.append((float(rsi_path[-1])-float(rsi_path[0]))/100.0)
    else:
        feats+=[0.0,0.0,0.0]

    # ── volatility transition ──
    feats.append(float(e["bb_exp"][i]))

    # ── context features ──
    feats+=[float(e["rsi"][i])/100.0,
            float(e["macd"][i])/(atr_v+1e-9),
            float(e["bbw"][i]),
            float(e["trend"][i]),
            float(e["mtf"][i])/3.0,
            float(e["closes"][i]-e["closes"][i-w])/(bl*w)*100,
            float(e["vol_ratio"][i]),
            float(e["vol_body"][i]),
            float(e["regime"][i])/4.0,
            float(e["vol_div"][i]),
            float(e["prev_sess"][i])]

    # ── RESEARCH: extended-feature context values (current bar i) ────────────
    # Appended in registry order so every encoded vector shares the same layout.
    # Empty when USE_EXT_FEATURES is off => `feats` is identical to the base
    # encoding and X is byte-for-byte unchanged. Non-finite values are coerced
    # to 0.0 so a single NaN does not drop the whole window (these are auxiliary
    # context features, not the core shape).
    for nm in _ENC_EXT_NAMES:
        col=e.get(f"ext::{nm}")
        val=float(col[i]) if col is not None else 0.0
        feats.append(val if np.isfinite(val) else 0.0)

    v=np.array(feats,dtype=np.float32)
    return None if np.any(np.isnan(v)) else v

def build_features_parallel(df,w,n_workers):
    t0=time.time()
    print(f"  Encoding {len(df):,} windows on {n_workers} cores ...")
    cols_needed=["close","body","rng","uwk","lwk","bull","rsi14","macd_norm",
                 "bb_width","trend","mtf_bull_score","atr14","vol_ratio",
                 "vol_body_conf","regime","vol_price_div","bb_expanding","prev_sess_bias"]
    arrs=[df[c].values.copy() if c in df.columns else np.zeros(len(df))
          for c in cols_needed]
    # ── RESEARCH: append extended-feature columns so they ENTER X ────────────
    # _ext_feature_cols() returns [] unless USE_EXT_FEATURES is non-empty (and
    # features_ext imported), so the initargs reduce to the historical 18-array
    # form and the encoded matrix is byte-identical by default. When enabled, the
    # ext arrays follow the base block and the NAME LIST is the final initarg, so
    # workers reconstruct the same column layout without reading any global.
    ext_names=_ext_feature_cols()
    if ext_names:
        ext_arrs=[df[c].values.copy() if c in df.columns else np.zeros(len(df))
                  for c in ext_names]
        init_args=(*arrs, *ext_arrs, ext_names)
    else:
        init_args=tuple(arrs)
    args=[(i,w) for i in range(w,len(df))]
    with mp.Pool(n_workers,initializer=_init_enc,initargs=init_args) as pool:
        results=[]; t0e=time.time()
        for i,r in enumerate(pool.imap(_encode_one,args,chunksize=2000)):
            results.append(r)
            if i%5000==0 or i==len(args)-1:
                _pbar(i+1,len(args),"encoding",t0=t0e)
    vectors,indices=[],[]
    for off,v in enumerate(results):
        if v is not None:
            vectors.append(v); indices.append(w+off)
    X=np.vstack(vectors)
    indices_arr = np.asarray(indices, dtype=np.int64)
    print(f"  Encoded {len(indices_arr):,} windows  [{_elapsed(t0)}]")
    return X, indices_arr

# ─────────────────────────────────────────────────────────────────────────────
# MULTI-ALGORITHM CLUSTERING
# ─────────────────────────────────────────────────────────────────────────────
def cluster_multi_algo(X, indices, df, n_per_regime, seed):
    """
    Run K-Means + DBSCAN + Agglomerative per regime.
    Returns combined labels and total cluster count.
    A cluster found by 2+ algorithms gets a confidence=2 flag.
    """
    regime_arr=df["regime"].values
    sc=StandardScaler()
    Xs=sc.fit_transform(X)
    global_labels=np.full(len(indices),-1,dtype=np.int32)
    offset=0

    for rid in range(5):
        mask=regime_arr[indices]==rid
        if mask.sum()<max(20,n_per_regime*3): continue
        X_r=Xs[mask]; pos=np.where(mask)[0]
        n=min(n_per_regime,max(2,mask.sum()//50))

        # K-Means
        km=MiniBatchKMeans(n_clusters=n,random_state=seed+rid,
                           n_init=5,max_iter=200,
                           batch_size=min(5000,len(X_r)))
        lbl_km=km.fit_predict(X_r)

        # DBSCAN — auto-finds dense clusters, labels noise as -1
        # v5: subsample for eps estimation (faster on large regimes)
        _subsamp = min(500, len(X_r))
        _idx_sub_eps = np.random.default_rng(seed+rid+200).choice(len(X_r), _subsamp, replace=False)
        _X_sub_eps = X_r[_idx_sub_eps]
        eps=float(np.percentile(
            [np.linalg.norm(_X_sub_eps[i]-_X_sub_eps[max(0,i-1)]) for i in range(1, len(_X_sub_eps))],
            15))
        eps=max(eps,0.1)
        db=DBSCAN(eps=eps,min_samples=max(5,len(X_r)//200),n_jobs=-1)
        lbl_db=db.fit_predict(X_r)
        n_db=max(lbl_db)+1 if max(lbl_db)>=0 else 0

        # Agglomerative — use subset for speed on large data
        subset=min(3000,len(X_r))
        idx_sub=np.random.default_rng(seed+rid+100).choice(len(X_r),subset,replace=False)
        ag=AgglomerativeClustering(n_clusters=n,linkage="ward")
        lbl_ag_sub=ag.fit_predict(X_r[idx_sub])
        # assign remaining via nearest centroid (cdist is much faster than manual loop)
        ag_centroids=np.array([X_r[idx_sub[lbl_ag_sub==c]].mean(axis=0)
                                for c in range(n) if (lbl_ag_sub==c).any()])
        lbl_ag=np.argmin(cdist(X_r, ag_centroids, metric="euclidean"), axis=1)

        # Combine: primary = K-Means, DBSCAN + Agglomerative add confidence
        # Remap DBSCAN clusters to nearest K-Means cluster
        for p,l in zip(pos,lbl_km):
            global_labels[p]=offset+l

        n_total=n
        # Add DBSCAN clusters that are notably different from K-Means clusters
        if n_db>0:
            for db_cid in range(n_db):
                db_members=X_r[lbl_db==db_cid]
                if len(db_members)<10: continue
                db_center=db_members.mean(axis=0)
                # find nearest K-Means cluster
                km_centers=km.cluster_centers_
                dists=np.linalg.norm(km_centers-db_center,axis=1)
                nearest=dists.min()
                # if this DBSCAN cluster is far from existing K-Means clusters,
                # add it as a new cluster
                if nearest>np.percentile(dists,60) and n_total<n+2:  # fix 3.1: much stricter
                    db_pos=pos[lbl_db==db_cid]
                    for p in db_pos:
                        global_labels[p]=offset+n_total
                    n_total+=1

        print(f"  Regime {rid} ({REGIME_NAMES[rid]:>15}): "
              f"{mask.sum():>6,} bars -> {n_total} clusters "
              f"(KM:{n} DBSCAN:{n_db} AG:{n})")
        offset+=n_total

    unassigned=global_labels==-1
    if unassigned.any(): global_labels[unassigned]=0
    return global_labels, offset

def cluster_per_regime_kmeans(X,indices,df,n_per_regime,seed):
    """Fallback: K-Means only per regime."""
    regime_arr=df["regime"].values
    sc=StandardScaler(); Xs=sc.fit_transform(X)
    global_labels=np.full(len(indices),-1,dtype=np.int32); offset=0
    for rid in range(5):
        mask=regime_arr[indices]==rid
        if mask.sum()<max(20,n_per_regime*3): continue
        X_r=Xs[mask]; n=min(n_per_regime,max(2,mask.sum()//50))
        km=MiniBatchKMeans(n_clusters=n,random_state=seed+rid,
                           n_init=5,max_iter=200,batch_size=min(5000,len(X_r)))
        lbl=km.fit_predict(X_r)
        pos=np.where(mask)[0]
        for p,l in zip(pos,lbl): global_labels[p]=offset+l
        print(f"  Regime {rid} ({REGIME_NAMES[rid]:>15}): "
              f"{mask.sum():>6,} bars -> {n} clusters (ids {offset}..{offset+n-1})")
        offset+=n
    global_labels[global_labels==-1]=0
    return global_labels,offset

# ─────────────────────────────────────────────────────────────────────────────
# SHAPE MATCHING (parallel)
# ─────────────────────────────────────────────────────────────────────────────
_SM={}

def _init_sm(closes,centroids,w,threshold):
    global _SM
    _SM=dict(closes=closes,centroids=centroids,w=w,threshold=threshold)

def _sm_one(args):
    pos,bi,cur=args; e=_SM
    w=e["w"]; cl=e["closes"]; th=e["threshold"]
    sl=cl[bi-w:bi]
    if len(sl)!=w or sl[0]==0: return pos,cur
    seq=(sl-sl[0])/sl[0]*100
    best_cid=cur; best_r=th-0.001
    for cid,centroid in e["centroids"].items():
        try:
            r=float(np.corrcoef(seq,centroid)[0,1])
            if r>best_r: best_r=r; best_cid=cid
        except: pass
    return pos,best_cid

def refine_shape_matching(df,indices,labels,w,threshold,n_workers):
    t0=time.time(); print(f"  Building centroids ...")
    closes=df["close"].values.copy(); n_cl=labels.max()+1; centroids={}
    for cid in range(n_cl):
        mb=[indices[i] for i in range(len(indices)) if labels[i]==cid]
        seqs=[]
        for bi in mb[:400]:
            sl=closes[bi-w:bi]
            if len(sl)==w and sl[0]!=0:
                seqs.append((sl-sl[0])/sl[0]*100)
        if seqs: centroids[cid]=np.mean(seqs,axis=0)
    print(f"  Shape matching {len(indices):,} windows on {n_workers} cores ...")
    args=[(pos,bi,int(labels[pos])) for pos,bi in enumerate(indices)]
    with mp.Pool(n_workers,initializer=_init_sm,
                 initargs=(closes,centroids,w,threshold)) as pool:
        results=[]; t0s=time.time()
        for i,r in enumerate(pool.imap(_sm_one,args,chunksize=3000)):
            results.append(r)
            if i%10000==0 or i==len(args)-1:
                _pbar(i+1,len(args),"shape match",t0=t0s)
    new_labels=labels.copy()
    for pos,cid in results: new_labels[pos]=cid
    print(f"  Shape matching done  [{_elapsed(t0)}]")
    return new_labels

# ─────────────────────────────────────────────────────────────────────────────
# PRICE DISTRIBUTIONS — ATR-normalised sustained move
# ─────────────────────────────────────────────────────────────────────────────
def _research_labels(df, fwd):
    """RESEARCH: precompute labels_ext arrays for the whole frame, once.

    Returns a dict with the pieces the price-distribution stage needs, or
    ``None`` when neither label flag is set or labels_ext is unavailable (in
    which case the caller keeps its current MFE/MAE behaviour).

    Keys when active:
      tp_dist, sl_dist : per-bar fractional TP / SL distances implied by the
          ATR-scaled triple barrier (|barrier - entry| / entry). Present only
          when USE_TRIPLE_BARRIER. Used to size each cluster's SL/TP from the
          MEDIAN member-bar barrier distance — same fractional-price units as
          the percentile sizing it replaces, so downstream math is unchanged.
      excess : per-bar drift-removed forward return (self-detrended). Present
          only when USE_BETA_NEUTRAL_LABELS. Used to decide LONG vs SHORT from
          *excess* (alpha) sign rather than raw price drift.
    """
    want_tb=bool(globals().get("USE_TRIPLE_BARRIER", False))
    want_bn=bool(globals().get("USE_BETA_NEUTRAL_LABELS", False))
    if not (want_tb or want_bn):
        return None
    try:
        import labels_ext as _lx
    except ImportError as e:
        print(f"  [research] labels_ext unavailable ({e}); "
              f"USE_TRIPLE_BARRIER / USE_BETA_NEUTRAL_LABELS ignored, "
              f"using MFE/MAE percentile labels.")
        return None
    out={}
    try:
        if want_tb:
            # Long-side barriers sized from the engine's quantile multipliers
            # (kept as ATR multiples) over the same horizon as the forward window.
            tb=_lx.triple_barrier(
                df,
                tp_atr_mult=float(globals().get("TP_PCT_QUANTILE",0.6))+1.0,
                sl_atr_mult=float(globals().get("SL_PCT_QUANTILE",0.7))+0.5,
                max_hold=max(1,int(fwd)), side=1, entry="close")
            entry_px=df["close"].to_numpy(float)
            exit_px=tb["exit_price"].to_numpy(float)
            # Fractional realized distance per bar; split into fav/adv by sign of
            # the trade outcome so medians approximate cluster TP / SL sizing.
            dist=np.abs(exit_px-entry_px)/(np.abs(entry_px)+1e-9)
            lbl=tb["label"].to_numpy()
            out["tp_dist"]=np.where(lbl> 0, dist, np.nan)
            out["sl_dist"]=np.where(lbl< 0, dist, np.nan)
        if want_bn:
            out["excess"]=_lx.beta_neutral_forward_return(
                df, max(1,int(fwd)), price_col="close").to_numpy(float)
    except Exception as e:
        print(f"  [research] labels_ext computation failed ({e}); "
              f"falling back to MFE/MAE percentile labels.")
        return None
    note=[]
    if want_tb and "tp_dist" in out: note.append("triple_barrier SL/TP")
    if want_bn and "excess"  in out: note.append("beta-neutral direction")
    if note:
        print(f"  [research] labeling via labels_ext: {', '.join(note)} (fwd={fwd}).")
    return out or None

def compute_price_distributions(df,indices,labels,n_cl,fwd):
    """v5: inner bar loop replaced with NumPy slice operations per trade.

    RESEARCH: when USE_TRIPLE_BARRIER / USE_BETA_NEUTRAL_LABELS is set the SL/TP
    sizing and/or LONG-SHORT direction are overridden per cluster via labels_ext
    (see _research_labels). With both flags off this is the historical routine.
    """
    hi=df["high"].values; lo=df["low"].values
    cl=df["close"].values; op=df["open"].values
    atr=df["atr14"].values; n=len(df)
    results={}
    _rlab=_research_labels(df, fwd)   # None unless a label flag is set

    for cid in range(n_cl):
        member_bi=[indices[i] for i in range(len(indices)) if labels[i]==cid]
        if len(member_bi)<10: results[cid]=None; continue

        mae_list=[]; mfe_list=[]
        long_count=short_count=0
        meaningful_long=meaningful_short=0

        for bi in member_bi:
            if bi+1>=n: continue
            entry=op[bi+1] if bi+1<n else cl[bi]
            if entry==0: continue
            atr_v=float(atr[bi])
            if atr_v==0 or np.isnan(atr_v): continue

            # direction: whichever side moves more in first 3 bars
            end3=min(bi+4,n-1)
            hi3=hi[bi+1:end3+1].max(); lo3=lo[bi+1:end3+1].min()
            is_long=(hi3-entry)>(entry-lo3)
            if is_long: long_count+=1
            else:       short_count+=1

            # vectorised forward window
            j_end=min(bi+fwd+1,n)
            h_arr=hi[bi+1:j_end]; l_arr=lo[bi+1:j_end]

            if is_long:
                fav_arr=(h_arr-entry)/entry
                adv_arr=(entry-l_arr)/entry
            else:
                fav_arr=(entry-l_arr)/entry
                adv_arr=(h_arr-entry)/entry

            max_fav=float(fav_arr.max()) if len(fav_arr) else 0.0
            max_adv=float(adv_arr.max()) if len(adv_arr) else 0.0
            mae_list.append(max_adv); mfe_list.append(max_fav)

            # sustained meaningful move: count max consecutive bars above ATR threshold
            # use cl[bi] as base (not entry, which may gap) — v5 bug fix
            threshold=MEANINGFUL_MOVE_ATR*0.5
            above=(fav_arr*(entry/(cl[bi]+1e-9)))>=threshold
            # find max run of True in boolean array
            max_consec_fav=0; run=0
            for b in above:
                if b: run+=1; max_consec_fav=max(max_consec_fav,run)
                else: run=0

            fav_atr_max=(max_fav*cl[bi])/(atr_v+1e-9)
            if fav_atr_max>=MEANINGFUL_MOVE_ATR and max_consec_fav>=MEANINGFUL_SUSTAIN_BARS:
                if is_long: meaningful_long+=1
                else:       meaningful_short+=1

        if len(mae_list)<10: results[cid]=None; continue
        mae_arr=np.array(mae_list,dtype=np.float32)
        mfe_arr_np=np.array(mfe_list,dtype=np.float32)
        sl_pct=max(float(np.percentile(mae_arr,SL_PCT_QUANTILE*100)),0.0002)
        tp_pct=max(float(np.percentile(mfe_arr_np,TP_PCT_QUANTILE*100)),0.0002)

        results[cid]={
            "sl_pct":          round(sl_pct,5),
            "tp_pct":          round(tp_pct,5),
            "sl_pct_label":    f"{sl_pct*100:.3f}%",
            "tp_pct_label":    f"{tp_pct*100:.3f}%",
            "implied_rr":      round(tp_pct/sl_pct,2),
            "direction":       "LONG" if long_count>=short_count else "SHORT",
            "long_pct":        round(long_count/max(len(member_bi),1)*100,1),
            "n_samples":       len(mae_list),
            "cluster_size":    len(member_bi),
            "meaningful_long": meaningful_long,
            "meaningful_short":meaningful_short,
            "sustained_pct":   round((meaningful_long+meaningful_short)/
                               max(len(member_bi),1)*100,1),
        }

        # ── RESEARCH override (gated) ────────────────────────────────────────
        # Replace SL/TP sizing and/or direction with labels_ext-derived values
        # for this cluster's member bars. No-op when _rlab is None (flags off),
        # so the default result above is returned verbatim.
        if _rlab is not None:
            rd=results[cid]
            if "tp_dist" in _rlab:
                tp_m=_rlab["tp_dist"][member_bi]; sl_m=_rlab["sl_dist"][member_bi]
                tp_m=tp_m[np.isfinite(tp_m)]; sl_m=sl_m[np.isfinite(sl_m)]
                if len(tp_m)>=3 and len(sl_m)>=3:
                    sl_pct=max(float(np.median(sl_m)),0.0002)
                    tp_pct=max(float(np.median(tp_m)),0.0002)
                    rd["sl_pct"]=round(sl_pct,5); rd["tp_pct"]=round(tp_pct,5)
                    rd["sl_pct_label"]=f"{sl_pct*100:.3f}%"
                    rd["tp_pct_label"]=f"{tp_pct*100:.3f}%"
                    rd["implied_rr"]=round(tp_pct/sl_pct,2)
                    rd["label_source"]="triple_barrier"
            if "excess" in _rlab:
                ex=_rlab["excess"][member_bi]; ex=ex[np.isfinite(ex)]
                if len(ex)>0:
                    # Net drift-removed forward move decides direction: positive
                    # excess => the entry condition has long alpha (not just the
                    # market drifting up); negative => short alpha.
                    long_share=float(np.mean(ex>0))*100.0
                    rd["direction"]="LONG" if np.mean(ex)>=0 else "SHORT"
                    rd["long_pct"]=round(long_share,1)
                    rd["label_source"]=(rd.get("label_source","")+"+beta_neutral").lstrip("+")
    return results

def select_forward_horizon(df, indices, labels, n_cl, candidates):
    """RESEARCH: pick the forward horizon with the strongest sustained-move edge.

    For each candidate horizon we run the (already-existing) price-distribution
    routine and score the horizon by the member-weighted average sustained-move
    percentage across clusters — i.e. how often clusters produce a meaningful,
    held move at that horizon. The horizon with the highest score is returned.

    Parameters
    ----------
    candidates : list[int]
        Forward-bar horizons to try. Empty => returns (FORWARD_BARS, None):
        a pure no-op so the engine keeps its single fixed horizon.

    Returns
    -------
    (best_fwd, table) where `table` is a list of (fwd, score) for logging, or
    (FORWARD_BARS, None) when the sweep is disabled.
    """
    cands=[int(c) for c in (candidates or []) if int(c) >= 1]
    if not cands and bool(globals().get("USE_FORWARD_SWEEP", False)):
        cands=[12, 24, 48, 96]   # UI-friendly default sweep
    if not cands:
        return int(globals().get("FORWARD_BARS", 24)), None
    table=[]
    for fb in cands:
        pdist=compute_price_distributions(df, indices, labels, n_cl, fb)
        num=den=0.0
        for cid in range(n_cl):
            d=pdist.get(cid)
            if not d:
                continue
            w=float(d.get("n_samples", 0))
            num+=float(d.get("sustained_pct", 0.0))*w
            den+=w
        score=(num/den) if den > 0 else 0.0
        table.append((fb, round(score, 3)))
    # Deterministic tie-break: highest score, then SHORTER horizon (less lag).
    best_fwd=max(table, key=lambda t: (t[1], -t[0]))[0]
    return best_fwd, table

# ─────────────────────────────────────────────────────────────────────────────
# BIDIRECTIONAL ANALYSIS & DIRECTION DISCRIMINATOR
# ─────────────────────────────────────────────────────────────────────────────
def analyze_bidirectionality(df,indices,labels,n_cl,price_dists,fwd):
    """
    For each cluster, test both LONG and SHORT independently.
    Returns per-cluster direction mode and discriminator if bidirectional.
    """
    hi=df["high"].values; lo=df["low"].values
    cl=df["close"].values; op=df["open"].values
    atr=df["atr14"].values; n=len(df)
    col_arrays={col:df[col].values for col in DISCRIM_COLS if col in df.columns}
    results={}

    for cid in range(n_cl):
        d=price_dists.get(cid)
        if not d: results[cid]={"mode":"UNKNOWN"}; continue
        sl_p=d["sl_pct"]; tp_p=d["tp_pct"]
        member_bi=[indices[i] for i in range(len(indices)) if labels[i]==cid]

        long_wins=long_losses=short_wins=short_losses=0
        long_trades=[]; short_trades=[]

        for bi in member_bi:
            if bi+1>=n: continue
            entry=op[bi+1] if bi+1<n else cl[bi]
            if entry==0: continue
            atr_v=atr[bi]
            if atr_v==0 or np.isnan(atr_v): continue
            end=min(bi+4,n-1)
            hi3=hi[bi+1:end+1].max(); lo3=lo[bi+1:end+1].min()
            momentum_long=(hi3-entry)>(entry-lo3)

            # Test LONG
            sl_v=entry*(1-sl_p); tp_v=entry*(1+tp_p)
            ht=hs=False
            for j in range(bi+1,min(bi+fwd+1,n)):
                if hi[j]>=tp_v: ht=True; break
                if lo[j]<=sl_v: hs=True; break
            if ht: long_wins+=1; long_trades.append((bi,1))
            elif hs: long_losses+=1; long_trades.append((bi,0))

            # Test SHORT
            sl_v=entry*(1+sl_p); tp_v=entry*(1-tp_p)
            ht=hs=False
            for j in range(bi+1,min(bi+fwd+1,n)):
                if lo[j]<=tp_v: ht=True; break
                if hi[j]>=sl_v: hs=True; break
            if ht: short_wins+=1; short_trades.append((bi,1))
            elif hs: short_losses+=1; short_trades.append((bi,0))

        l_total=long_wins+long_losses; s_total=short_wins+short_losses
        l_wr=round(long_wins/l_total*100,1) if l_total else 0
        s_wr=round(short_wins/s_total*100,1) if s_total else 0

        long_ok=(l_wr>=BIDIR_MIN_WR and l_total>=BIDIR_MIN_TRADES)
        short_ok=(s_wr>=BIDIR_MIN_WR and s_total>=BIDIR_MIN_TRADES)

        # Engine-level direction lock (Bug #30 — long-only MT5 mismatch fix).
        # When set, override empirical classification so the GA refines
        # purely directional rules and .set files emit DirectionMode=0/1
        # with no discriminator — guaranteeing parity with a long-only or
        # short-only MT5 EA configuration.
        _force = str(globals().get("FORCE_DIRECTION", "auto")).lower()
        if _force == "long_only":
            mode = "LONG_ONLY"; discrim = None
        elif _force == "short_only":
            mode = "SHORT_ONLY"; discrim = None
        elif _force == "bidirectional":
            # Forced bidirectional: trade BOTH sides, decided per-bar by the
            # discriminator, even if neither side cleared the natural BIDIR
            # thresholds. Pair with USE_BETA_NEUTRAL_LABELS, else this just adds
            # losing trades on the weaker side.
            mode = "BIDIRECTIONAL"
            discrim = find_direction_discriminator(
                df, member_bi, col_arrays, long_trades, short_trades)
        elif long_ok and short_ok:
            mode="BIDIRECTIONAL"
            # Find direction discriminator
            discrim=find_direction_discriminator(
                df,member_bi,col_arrays,long_trades,short_trades)
        elif long_ok:
            mode="LONG_ONLY"; discrim=None
        elif short_ok:
            mode="SHORT_ONLY"; discrim=None
        else:
            # Neither passes strict threshold — use the better direction
            mode="LONG_ONLY" if l_wr>=s_wr else "SHORT_ONLY"
            discrim=None

        results[cid]={
            "mode":       mode,
            "long_wr":    l_wr,
            "short_wr":   s_wr,
            "long_trades":l_total,
            "short_trades":s_total,
            "discrim":    discrim,
        }
    return results

def find_direction_discriminator(df,member_bi,col_arrays,
                                  long_trades,short_trades):
    """
    For bidirectional patterns, find what indicator condition at signal time
    best predicts whether price will go long or short.
    Returns best discriminator column, threshold, and accuracy.
    """
    # Build arrays: feature value at each signal bar + direction outcome
    # long_trades: list of (bi, won_as_long)
    # short_trades: list of (bi, won_as_short)
    # We want to find: when both could work, what predicted which went?
    # Label: 1=long won and short lost, 0=short won and long lost
    long_dict=dict(long_trades)
    short_dict=dict(short_trades)
    bars=[]; labels_d=[]
    for bi in member_bi:
        lw=long_dict.get(bi,-1); sw=short_dict.get(bi,-1)
        if lw==1 and sw==0: bars.append(bi); labels_d.append(1)  # long won
        elif lw==0 and sw==1: bars.append(bi); labels_d.append(0)  # short won
    if len(bars)<20: return None

    labels_arr=np.array(labels_d)
    best_col=None; best_acc=DISCRIM_MIN_ACCURACY; best_thresh=None; best_dir=None

    for col in DISCRIM_COLS:
        if col not in col_arrays: continue
        vals=np.array([float(col_arrays[col][bi]) for bi in bars])
        # try thresholds at percentiles
        for pct in range(20,85,5):
            thresh=float(np.percentile(vals,pct))
            # above threshold -> long
            pred_above=(vals>=thresh).astype(int)
            acc_above=(pred_above==labels_arr).mean()
            if acc_above>best_acc:
                best_acc=acc_above; best_col=col
                best_thresh=thresh; best_dir="above"
            # below threshold -> long
            pred_below=(vals<thresh).astype(int)
            acc_below=(pred_below==labels_arr).mean()
            if acc_below>best_acc:
                best_acc=acc_below; best_col=col
                best_thresh=thresh; best_dir="below"

    # ── Stage 1 result ──────────────────────────────────────────────────────
    if best_col is not None:
        return {"col":best_col,"thresh":round(best_thresh,4),
                "dir":best_dir,"accuracy":round(best_acc*100,1),
                "n_bars":len(bars),"method":"threshold"}

    # ── Stage 2: Decision-Tree fallback (depth=2, multi-condition) ───────────
    # Finds rules like "RSI>55 AND stoch_k<40 -> LONG" when no single
    # threshold clears DISCRIM_MIN_ACCURACY.
    avail_cols=[c for c in DISCRIM_COLS if c in col_arrays]
    if len(avail_cols)<2 or len(bars)<30: return None
    X_dt=np.column_stack([
        np.array([float(col_arrays[c][bi]) for bi in bars])
        for c in avail_cols
    ])
    try:
        dt=DecisionTreeClassifier(max_depth=2,min_samples_leaf=5,random_state=42)
        dt.fit(X_dt,labels_arr)
        dt_acc=float(dt.score(X_dt,labels_arr))
        if dt_acc>=DISCRIM_MIN_ACCURACY:
            tree_text=export_text(dt,feature_names=avail_cols,max_depth=2)
            return {"col":"TREE","thresh":0.0,"dir":"tree",
                    "accuracy":round(dt_acc*100,1),"n_bars":len(bars),
                    "method":"decision_tree","tree_text":tree_text,
                    "dt_features":avail_cols}
    except Exception:
        pass
    return None

def mirror_rule_for_short(rule):
    """Flip directional conditions for a SHORT rule."""
    mirrored={}
    for col,(lo_v,hi_v) in rule.items():
        if col=="trend":
            mirrored[col]=(-hi_v,-lo_v)
        elif col=="bull":
            mirrored[col]=(1-hi_v,1-lo_v)
        elif col=="macd_norm":
            mirrored[col]=(-hi_v,-lo_v)
        elif col=="rsi14":
            mirrored[col]=(100-hi_v,100-lo_v)
        else:
            mirrored[col]=(lo_v,hi_v)
    return mirrored


# =============================================================================
# MULTI-SEED OUTPUT HELPERS
# =============================================================================

def _jaccard_rule_overlap(rule_a, rule_b):
    """Jaccard similarity on condition-column sets."""
    cols_a = set(rule_a.keys())
    cols_b = set(rule_b.keys())
    if not cols_a and not cols_b:
        return 1.0
    return len(cols_a & cols_b) / len(cols_a | cols_b)


def _oos_rank_key(r):
    """Rank patterns by what MT5 will actually reproduce.

    Preference order:
      1. EA-faithful box-only OOS (ea_test_pf, ea_test_wr) — the EA fires on
         the exported feature box with no cluster/shape gate, so these are the
         only OOS numbers MT5 can match.
      2. Cluster-gated OOS (test_pf, test_wr) — diagnostic, overstates fidelity.
      3. TRAIN composite_score — last resort when no test split exists.
    Infinite PF (no losses, tiny sample) is capped so it can't dominate.
    """
    def _cap(x):
        try:
            x = float(x)
        except (TypeError, ValueError):
            return 0.0
        if not np.isfinite(x):
            return 99.0
        return x

    ea_pf = _cap(r.get("ea_test_pf", 0))
    if ea_pf > 0 and r.get("ea_test_trades", 0):
        return (2, ea_pf, _cap(r.get("ea_test_wr", 0)))
    if r.get("test_pf", 0):
        return (1, _cap(r.get("test_pf", 0)), _cap(r.get("test_wr", 0)))
    return (0, _cap(r.get("composite_score", 0)), 0.0)


def write_combined_report(all_results, out_dir):
    """
    Deduplicate patterns from multiple seeds by Jaccard overlap on rule columns,
    re-rank globally by EA-faithful OOS quality, then write combined CSV,
    report, chart.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # OOS-bias fix (D) + EA-coherence: rank by the EA-faithful box-only OOS
    # metrics (what MT5 reproduces), falling back to cluster-gated test and
    # finally TRAIN composite_score. See _oos_rank_key.
    ranked = sorted(all_results, key=_oos_rank_key, reverse=True)
    kept = []
    for candidate in ranked:
        rule_c = candidate.get("genetic_rule", {})
        dir_c  = candidate.get("direction", "LONG")
        is_dup = False
        for existing in kept:
            if existing.get("direction", "LONG") == dir_c:
                if _jaccard_rule_overlap(rule_c, existing.get("genetic_rule", {})) \
                        >= ENSEMBLE_OVERLAP_THRESHOLD:
                    is_dup = True
                    break
        if not is_dup:
            kept.append(candidate)

    print(f"\n  [combined] {len(all_results)} total patterns across all seeds")
    print(f"  [combined] {len(kept)} unique after dedup "
          f"(Jaccard threshold={ENSEMBLE_OVERLAP_THRESHOLD})")

    trade_dfs = []
    for r in kept:
        tdf = r.get("trades", pd.DataFrame())
        if not tdf.empty:
            tdf = tdf.copy()
            tdf["seed"]      = r.get("seed", 0)
            tdf["cluster"]   = r["cluster"]
            tdf["direction"] = r["direction"]
            trade_dfs.append(tdf)
    csv_path = out_dir / "combined_all_seeds.csv"
    if trade_dfs:
        pd.concat(trade_dfs).sort_values("time").to_csv(csv_path, index=False)
        print(f"  [combined] CSV    -> {csv_path}")
    else:
        print(f"  [combined] No trades to write.")

    lines = [
        "=" * 65,
        f"  COMBINED MULTI-SEED REPORT  v6",
        f"  Seeds: {MULTI_SEED_BASE} .. {MULTI_SEED_BASE + MULTI_SEED_COUNT - 1}",
        f"  {len(all_results)} total patterns  ->  {len(kept)} unique after dedup",
        f"  Jaccard overlap threshold: {ENSEMBLE_OVERLAP_THRESHOLD}",
        f"  Ranked by EA-faithful OOS (ea_test_pf, ea_test_wr; what MT5 reproduces)",
        "=" * 65,
    ]
    for rank, r in enumerate(kept, 1):
        rule = r.get("genetic_rule", {})
        cid  = r["cluster"]
        dirn = r["direction"]
        SEP  = "-" * 65
        lines += [
            "",
            SEP,
            f"  RANK {rank} | Seed={r.get('seed', '?')} | "
            f"C{cid} [{dirn}] [{r.get('bidir_mode', '?')}]"
            + ("  MARGINAL" if r.get("marginal") else ""),
            f"  Train: WR={r.get('win_rate_', 0)}%  "
            f"Wilson={r.get('wilson_wr', 0)}%  "
            f"PF={r.get('profit_factor', 0)}  "
            f"Score={r.get('composite_score', 0)}  "
            f"{r.get('per_day', 0)}/day",
            f"  Test:  WR={r.get('test_wr', 0)}%  "
            f"PF={r.get('test_pf', 0)}  "
            f"Trades={r.get('test_trades', 0)}  "
            f"Score={r.get('test_score', 0)}",
            f"  EA-OOS (box-only, what MT5 fires): "
            f"WR={r.get('ea_test_wr', 0)}%  "
            f"PF={r.get('ea_test_pf', 0)}  "
            f"Trades={r.get('ea_test_trades', 0)}  "
            f"(inflation ×{r.get('box_inflation', 0)} vs gated test)",
            f"  SL={r.get('sl_pct_label', '?')}  "
            f"TP={r.get('tp_pct_label', '?')}  "
            f"Implied RR={r.get('implied_rr', 0)}  "
            f"Consistency={r.get('consistency', 0):.0%}",
            "",
            "  GENETIC CONDITIONS:",
            rule_to_text(rule, dirn),
        ]
        if r.get("bidir_mode") == "BIDIRECTIONAL" and r.get("discriminator"):
            dc = r["discriminator"]
            lines.append(
                f"  DISCRIMINATOR ({dc['accuracy']}%): "
                f"{dc['col']} {dc['dir']} {dc['thresh']}"
            )
    rpt_path = out_dir / "combined_report.txt"
    rpt_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [combined] Report -> {rpt_path}")

    chart_path = str(out_dir / "combined_performance.png")
    plot_performance(kept, chart_path)
    print(f"  [combined] Chart  -> {chart_path}")

    return kept


# ─────────────────────────────────────────────────────────────────────────────
# SCORING — Wilson lower bound + additive weighted
# ─────────────────────────────────────────────────────────────────────────────
def wilson_lower(wins,total,conf=SCORE_WILSON_CONFIDENCE):
    """Lower bound of Wilson score interval. Penalises small samples."""
    if total==0: return 0.0
    z=_scipy_norm.ppf((1+conf)/2)
    p=wins/total
    denom=1+z**2/total
    centre=p+z**2/(2*total)
    spread=z*(p*(1-p)/total+z**2/(4*total**2))**0.5
    return max(0.0,(centre-spread)/denom)

def time_consistency_score(trades_df):
    """Returns fraction of years where WR > 50%, clamped 0-1."""
    if trades_df is None or trades_df.empty: return 0.0
    if "time" not in trades_df.columns: return 0.0
    trades_df = trades_df.copy()
    trades_df["year"] = pd.to_datetime(trades_df["time"]).dt.year
    years = trades_df["year"].unique()
    if len(years) < 2: return 0.6  # insufficient data — partial credit
    ok = 0
    for yr in years:
        sub = trades_df[trades_df["year"] == yr]
        if len(sub) >= 5 and sub["result"].eq("WIN").mean() >= 0.50:
            ok += 1
    return ok / len(years)

def trade_distribution_score(trades_df,train_days):
    """
    Returns 1.0 if trades spread evenly, lower if clustered in one period.
    Penalises patterns that only worked in a specific 3-month window.
    """
    if trades_df is None or trades_df.empty: return 0.5
    if "time" not in trades_df.columns: return 0.5
    trades_df=trades_df.copy()
    trades_df["quarter"]=pd.to_datetime(trades_df["time"]).dt.to_period("Q")
    counts=trades_df.groupby("quarter").size()
    if len(counts)<2: return 0.5
    # coefficient of variation — lower is more evenly distributed
    cv=counts.std()/max(counts.mean(),1)
    return max(0.0,min(1.0,1.0-cv/3.0))

def _complexity_bonus(n_cols: int) -> float:
    """Bug #32: bonus for rules using more indicator columns.

    Linear ramp: 0.0 at n_cols == GENE_N_COLS_MIN, full weight at GENE_N_COLS_MAX.
    Returns 0.0 when SCORE_W_RULE_COMPLEXITY is 0 (default).
    """
    w = float(globals().get("SCORE_W_RULE_COMPLEXITY", 0.0))
    if w <= 0.0 or n_cols <= 0:
        return 0.0
    lo = int(globals().get("GENE_N_COLS_MIN", 3))
    hi = int(globals().get("GENE_N_COLS_MAX", 6))
    if hi <= lo:
        return 0.0
    frac = max(0.0, min(1.0, (n_cols - lo) / (hi - lo)))
    return w * frac


def _target_score(actual: float, target: float, weight: float,
                  excess_w: float = None) -> float:
    """v0.6.0: asymmetric target-distance score for one objective.

    * actual < target: quadratic penalty pulls the GA up hard.
      score = weight * (actual / target)^2
    * actual >= target: full reward + tiny log-scaled excess bonus so the GA
      doesn't waste effort chasing a metric beyond its target unless it can
      do so without sacrificing other objectives.
      score = weight * (1.0 + excess_w * log(1 + (actual - target)))

    ``excess_w`` defaults to the module-level ``EXCESS_BONUS_WEIGHT``. Set
    excess_w=0 for strict targets, or 1.0 to recover legacy "max everything"
    behaviour.
    """
    import math
    if excess_w is None:
        excess_w = globals().get("EXCESS_BONUS_WEIGHT", 0.1)
    if target <= 0:
        # No target → fall back to capped maximisation (legacy behaviour)
        return weight * min(actual / max(target, 0.01), 2.0)
    if actual >= target:
        return weight * (1.0 + excess_w * math.log1p(actual - target))
    return weight * ((actual / target) ** 2)


def score_rule(wins, losses, avg_rr, trades_df, train_days):
    """
    Scoring is one of two modes (controlled by ``ENABLE_TARGET_SCORING``):

    TARGET MODE (default, v0.6.0+):
      Each objective is scored relative to its user-set target. Below target
      → quadratic penalty; at target → full credit; above target → tiny
      log-scaled bonus (capped by EXCESS_BONUS_WEIGHT). The GA actively
      evolves toward the target values rather than blindly maximising.

    MAXIMISE MODE (legacy, set ENABLE_TARGET_SCORING=False):
      The original additive weighted scoring — Wilson WR, normalised PF,
      RR as multiple of break-even, time stability — combined additively.
      Behaviour identical to v0.5.0 and earlier.
    """
    total = wins + losses
    if total < 5:
        return 0.0

    wr = wins / total
    # Dynamic break-even check is a HARD constraint in both modes — if avg_rr
    # doesn't cover the win-rate's break-even cost, the pattern is unprofitable
    # by definition and gets killed.
    breakeven = ((1 - wr) / wr) if wr > 0 else 999.0
    if avg_rr < breakeven:
        return 0.0

    # Pre-compute the four objective values (same in both modes).
    gl = losses
    gw = wins * avg_rr if avg_rr > 0 else 0
    pf = gw / gl if gl > 0 else 2.0
    q_stab = time_consistency_score(trades_df)
    q_dist = trade_distribution_score(trades_df, train_days)
    stability = q_stab * q_dist

    use_targets = bool(globals().get("ENABLE_TARGET_SCORING", True))
    if use_targets:
        # v0.6.0 target-driven mode. Convert each objective to its raw scale
        # (WR as %, PF as ratio, RR as ratio, stability as 0..1) and score
        # against the target.
        wr_pct  = wr * 100.0
        rr_val  = avg_rr / max(breakeven, 0.01)  # multiple of break-even
        tgt_wr  = float(globals().get("TARGET_WR_PCT",         55.0))
        tgt_pf  = float(globals().get("TARGET_PF",              1.5))
        tgt_rr  = float(globals().get("TARGET_RR",              1.3))
        tgt_st  = float(globals().get("TARGET_STABILITY",       0.65))
        tgt_tpd = float(globals().get("TARGET_TRADES_PER_DAY",  1.0))
        tpd     = total / max(train_days, 1)
        return (
            _target_score(wr_pct,    tgt_wr,  SCORE_W_WR) +
            _target_score(pf,        tgt_pf,  SCORE_W_PF) +
            _target_score(rr_val,    tgt_rr,  SCORE_W_RR) +
            _target_score(stability, tgt_st,  SCORE_W_STAB) +
            _target_score(tpd,       tgt_tpd, SCORE_W_TRADES_PER_DAY)
        )

    # Legacy maximise mode.
    q_wr = wilson_lower(wins, total)
    q_pf = min(pf, 4.0) / 4.0
    q_rr = min(avg_rr / max(breakeven, 0.01), 2.0) / 2.0
    return (SCORE_W_WR * q_wr +
            SCORE_W_PF * q_pf +
            SCORE_W_RR * q_rr +
            SCORE_W_STAB * stability)

# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST WORKER
# ─────────────────────────────────────────────────────────────────────────────
_BT={}

def _init_bt(hi, lo, cl, op, sess, fwd, n, spread, realistic, max_hold,
             allowed, cooldown,
             snap_regime, snap_trend, snap_rsi14,
             snap_stoch_k, snap_atr_pct, snap_mtf):
    global _BT
    _BT = dict(
        hi=hi, lo=lo, cl=cl, op=op, sess=sess, fwd=fwd, n=n,
        spread=spread, realistic=realistic,
        max_hold=max_hold, allowed=allowed, cooldown=cooldown,
        snap_regime=snap_regime, snap_trend=snap_trend,
        snap_rsi14=snap_rsi14, snap_stoch_k=snap_stoch_k,
        snap_atr_pct=snap_atr_pct, snap_mtf=snap_mtf,
    )

def _bt_worker_dir(args):
    """Backtest one cluster in one specific direction."""
    cid, member_bi, sl_pct, tp_pct, direction = args
    e = _BT
    hi = e["hi"]; lo = e["lo"]; cl = e["cl"]; op = e["op"]
    sess = e["sess"]; fwd = e["fwd"]; n = e["n"]; spread = e["spread"]
    realistic = e["realistic"]; max_hold = e["max_hold"]
    allowed = e["allowed"]; cooldown = e["cooldown"]
    long = (direction == "LONG")
    # Serialize like the live EA: no new entry while a position is open, and
    # cooldown is anchored exactly like the EA's g_cooldownBar:
    #   - SL/TP hit intrabar during bar j -> anchor = j   (bar was forming at close)
    #   - MaxHold timeout                 -> anchor = j+1 (closed at next bar's open)
    # The EA skips a signal when (formingBar - anchor) < CooldownBars; the forming
    # bar for Python signal bar bi is bi+1 (the entry bar). last_exit is the last
    # bar the position was alive in (serialization guard, exact for any cooldown).
    trades = []; last_exit = -cooldown - 2; cd_anchor = -cooldown - 2

    for bi in member_bi:
        if bi + 1 >= n: continue
        if allowed and int(sess[bi]) not in allowed: continue
        if bi + 1 <= last_exit or (bi + 1) - cd_anchor < cooldown: continue
        entry = op[bi + 1] if realistic and bi + 1 < n else cl[bi]
        if entry == 0: continue
        entry_ws = entry + spread if long else entry - spread
        exit_spread = spread
        sl_v = entry_ws * (1 - sl_pct) if long else entry_ws * (1 + sl_pct)
        tp_v = entry_ws * (1 + tp_pct) if long else entry_ws * (1 - tp_pct)
        tp_v_eff = tp_v - exit_spread if long else tp_v + exit_spread
        sl_v_eff = sl_v - exit_spread if long else sl_v + exit_spread
        risk = abs(entry_ws - sl_v); reward = abs(tp_v - entry_ws)
        if risk == 0: continue
        # Book every outcome as an R-multiple (price move ÷ risk) so wins and
        # losses share the SAME unit. A clean TP hit = +reward/risk R; a clean
        # SL hit = -1.0 R; a max-hold timeout = realised (close-entry) move ÷
        # risk, signed. Storing the raw price distance for wins while charging
        # losses a flat -1.0 R silently inflated profit factor by ~(price/risk)×
        # (e.g. PF 19 on gold instead of the true ~1.2).
        win_r  = reward / risk     # R booked on a clean TP hit
        loss_r = -1.0              # R booked on a clean SL hit
        ht = hs = False; bars_held = 0
        for j in range(bi + 1, min(bi + max_hold + 1, n)):
            h_ = hi[j]; lo_ = lo[j]; bars_held += 1
            # Pessimistic intrabar resolution: when one bar's range spans BOTH
            # the stop and the target, assume the STOP filled first. Tick order
            # is unknowable from OHLC, and assuming the favourable fill biases
            # win-rate high vs a tick-accurate MT5 run.
            if long:
                if lo_ <= sl_v_eff: hs = True; break
                if h_ >= tp_v_eff: ht = True; break
            else:
                if h_ >= sl_v_eff: hs = True; break
                if lo_ <= tp_v_eff: ht = True; break
        timeout = not ht and not hs
        if timeout:
            exit_p = cl[min(bi + max_hold, n - 1)]
            exit_p_eff = exit_p - exit_spread if long else exit_p + exit_spread
            pnl = exit_p_eff - entry_ws if long else entry_ws - exit_p_eff
            if pnl > 0:
                ht = True
                win_r = pnl / risk
            else:
                hs = True
                loss_r = pnl / risk   # partial loss at timeout, not a full -1R stop

        # ── snapshot signal-bar context ──────────────────────────────────────
        snap = (
            int(e["snap_regime"][bi]),
            int(e["sess"][bi]),               # session already in _BT
            int(e["snap_trend"][bi]),
            round(float(e["snap_rsi14"][bi]),  2),
            round(float(e["snap_stoch_k"][bi]), 2),
            round(float(e["snap_atr_pct"][bi]), 5),
            int(e["snap_mtf"][bi]),
        )

        # Charge trading costs in R: one round-turn commission per trade plus
        # per-bar swap over the holding period. Classify WIN/LOSS on the NET
        # booked R (after costs) so a tiny TP win eaten by commission counts as
        # a loss — otherwise _calc_metrics adds its |R| to gross WINS and PF
        # silently inflates whenever COMMISSION_R/SWAP_R_PER_BAR are non-zero.
        cost_r = COMMISSION_R + SWAP_R_PER_BAR * bars_held
        booked_r = (win_r if ht else loss_r) - cost_r
        res = "WIN" if booked_r > 0 else "LOSS"
        last_exit = bi + bars_held
        cd_anchor = last_exit + (1 if timeout else 0)
        trades.append((bi, res, round(booked_r, 2), round(entry_ws, 2),
                       round(sl_v, 2), round(tp_v, 2),
                       direction, bars_held) + snap)
    return cid, direction, trades

def _calc_metrics(trades,member_count,trading_days,trades_df=None):
    wins=losses=0; gw=gl=0.0; rr_list=[]; equity=[0.0]
    max_dd=0.0; peak=0.0; consec=0; max_consec=0
    for _,res,rr,*_ in trades:
        if res=="WIN":
            wins+=1; gw+=abs(rr); rr_list.append(rr)
            equity.append(equity[-1]+rr); consec=0
        elif res=="LOSS":
            # rr is the stored R-multiple (negative): -1.0 for a clean stop,
            # a smaller magnitude for a timeout exit. Use it for both gross
            # loss and equity so PF/drawdown stay in true R units.
            losses+=1; gl+=abs(rr); rr_list.append(rr)
            equity.append(equity[-1]+rr)
            consec+=1; max_consec=max(max_consec,consec)
        curr=equity[-1]
        if curr>peak: peak=curr
        dd=peak-curr
        if dd>max_dd: max_dd=dd
    total=wins+losses
    pf=round(gw/gl,2) if gl else float("inf")
    avg_rr=round(float(np.mean([r for r in rr_list if r>0])),2) if any(r>0 for r in rr_list) else 0
    wilson_wr=round(wilson_lower(wins,total)*100,1)
    composite=score_rule(wins,losses,avg_rr,trades_df,trading_days)
    # per_day = actual trades (wins+losses) / trading days.
    # Do NOT use member_count here — that is the raw shape-cluster size before
    # genetic filtering.  After pass-2 refines the rule, member_count >> total,
    # so member_count/days gives an inflated number pre-genetic and a collapsed
    # number post-genetic.  Storing member_count separately preserves the signal-
    # retention stat without corrupting per_day.
    return dict(total_trades=total,wins=wins,losses=losses,
                win_rate_=round(wins/total*100,1) if total else 0,
                wilson_wr=wilson_wr,
                avg_rr=avg_rr,profit_factor=pf,
                max_drawdown_r=round(max_dd,2),
                max_consec_losses=max_consec,
                equity=equity,
                member_count=member_count,
                # REPORT-ONLY: post-rule trades / raw shape-cluster size. <1 means
                # the genetic rule fired on fewer bars than the cluster; the EA's
                # exported feature box is a superset, so live count inflates above
                # this. TODO(follow-up): true box-vs-cluster recall = evaluate the
                # exported box over the cluster regime mask (documented, not done).
                signal_retention=round(total/member_count,3) if member_count else 0,
                per_day=round(total/max(trading_days,1),2),
                composite_score=round(composite,4))

def backtest_all_directions(df, indices, labels, n_cl, bidir_info,
                             price_dists, fwd, n_workers, trading_days):
    """Backtest each cluster in its specific direction(s)."""
    hi = df["high"].values.copy(); lo = df["low"].values.copy()
    cl = df["close"].values.copy(); op = df["open"].values.copy()
    n = len(df)
    sess = (df["session"].values.copy() if "session" in df.columns
            else np.zeros(n, dtype=np.int8))
    allowed = [{"ASIAN": 0, "LONDON": 1, "NY": 2, "OVERLAP": 3, "OFF": 4}[s]
               for s in ALLOWED_SESSIONS
               if s in {"ASIAN", "LONDON", "NY", "OVERLAP", "OFF"}]
    cm = {cid: [] for cid in range(n_cl)}
    for pos, bi in enumerate(indices): cm[int(labels[pos])].append(bi)

    args = []
    for cid in range(n_cl):
        d = price_dists.get(cid); info = bidir_info.get(cid, {})
        sl_p = d["sl_pct"] if d else 0.002
        tp_p = d["tp_pct"] if d else 0.003
        mode = info.get("mode", "LONG_ONLY")
        if mode == "BIDIRECTIONAL":
            args.append((cid, cm[cid], sl_p, tp_p, "LONG"))
            args.append((cid, cm[cid], sl_p, tp_p, "SHORT"))
        elif mode == "SHORT_ONLY":
            args.append((cid, cm[cid], sl_p, tp_p, "SHORT"))
        else:
            args.append((cid, cm[cid], sl_p, tp_p, "LONG"))

    # ── snapshot arrays added to init_args ───────────────────────────────────
    _z = np.zeros(n)
    init_args = (
        hi, lo, cl, op, sess, fwd, n, SPREAD_PTS, REALISTIC_ENTRY,
        MAX_HOLD_BARS, allowed, COOLDOWN_BARS,
        df["regime"].values.copy()       if "regime"        in df.columns else _z.copy(),
        df["trend"].values.copy()        if "trend"         in df.columns else _z.copy(),
        df["rsi14"].values.copy()        if "rsi14"         in df.columns else _z.copy(),
        df["stoch_k"].values.copy()      if "stoch_k"       in df.columns else _z.copy(),
        df["atr_pct"].values.copy()      if "atr_pct"       in df.columns else _z.copy(),
        df["mtf_bull_score"].values.copy() if "mtf_bull_score" in df.columns else _z.copy(),
    )

    with mp.Pool(n_workers, initializer=_init_bt, initargs=init_args) as pool:
        raw = pool.map(_bt_worker_dir, args)

    results = {}; idx_arr = df.index
    for cid, direction, trades in raw:
        key = (cid, direction)
        tdf = pd.DataFrame([
            {"time": idx_arr[bi], "result": res, "rr": rr,
             "entry": entry, "sl": sl_v, "tp": tp_v,
             "direction": dirn, "bars_held": bh,
             "regime": reg, "session": sess_v, "trend": trn,
             "rsi14": rsi14, "stoch_k": stk,
             "atr_pct": atrp, "mtf_bull_score": mtf}
            for bi, res, rr, entry, sl_v, tp_v, dirn, bh,
                reg, sess_v, trn, rsi14, stk, atrp, mtf in trades
        ])
        m = _calc_metrics(trades, len(cm[cid]), trading_days, tdf)
        m["cluster"] = cid; m["direction"] = direction
        d = price_dists.get(cid) or {}
        m["sl_pct"] = d.get("sl_pct", 0.002)
        m["tp_pct"] = d.get("tp_pct", 0.003)
        m["sl_pct_label"] = d.get("sl_pct_label", "?")
        m["tp_pct_label"] = d.get("tp_pct_label", "?")
        m["implied_rr"] = d.get("implied_rr", 0.0)
        m["trades"] = tdf
        results[key] = m
    return results


# ─────────────────────────────────────────────────────────────────────────────
# GENETIC EVOLUTION — full rework
# ─────────────────────────────────────────────────────────────────────────────
_GEN={}

def _init_genetic(arrays,hi,lo,cl,op,fwd,n,spread):
    global _GEN
    _GEN=dict(arrays=arrays,hi=hi,lo=lo,cl=cl,op=op,fwd=fwd,n=n,spread=spread)

def _time_consistency_np(result_wins: np.ndarray, bar_indices: np.ndarray,
                          n_bars: int, train_days: int) -> float:
    """Pure-NumPy replacement for time_consistency_score used in GA hot loop.

    Approximates each trade's year from its bar index (same mapping as the
    fake pd.Timestamp construction it replaces).  Returns fraction of years
    where win rate >= 50%, clamped 0-1; identical semantics to the pandas version.
    """
    if len(bar_indices) == 0:
        return 0.0
    years = (bar_indices * train_days // max(n_bars, 1) // 365).astype(np.int32)
    unique_years = np.unique(years)
    if len(unique_years) < 2:
        return 0.6
    ok = 0
    for yr in unique_years:
        mask = years == yr
        if mask.sum() >= 5 and result_wins[mask].mean() >= 0.50:
            ok += 1
    return ok / len(unique_years)


def _trade_distribution_np(bar_indices: np.ndarray, n_bars: int,
                            train_days: int) -> float:
    """Pure-NumPy replacement for trade_distribution_score used in GA hot loop."""
    if len(bar_indices) == 0:
        return 0.5
    # quarter index: bar_idx → approximate quarter number (0, 1, 2, …)
    quarters = (bar_indices * train_days // max(n_bars, 1) // 91).astype(np.int32)
    unique_q, counts = np.unique(quarters, return_counts=True)
    if len(unique_q) < 2:
        return 0.5
    cv = counts.std() / max(counts.mean(), 1)
    return float(max(0.0, min(1.0, 1.0 - cv / 3.0)))


def _rule_match_mask(member_bi_arr, rule, arrays, cache=None):
    """v5: Vectorised rule evaluation with optional per-column mask caching.

    cache: dict keyed by (col, lb, hb) → boolean array over the same
    member_bi_arr.  Pass the same dict across calls within one GA worker to
    avoid recomputing unchanged column conditions (speedup item #20).
    """
    mask = np.ones(len(member_bi_arr), dtype=bool)
    for col, (lb, hb) in rule.items():
        if col not in arrays:
            continue
        key = (col, lb, hb)
        if cache is not None and key in cache:
            col_mask = cache[key]
        else:
            arr = arrays[col]
            vals = arr[member_bi_arr]
            col_mask = (vals >= lb) & (vals <= hb)
            if cache is not None:
                # Bound cache so box-only (eval over the full 108k-bar set) can't
                # balloon memory and kill the worker (the hang cause).
                if len(cache) >= int(globals().get("GENE_CACHE_MAX", 4000)):
                    cache.clear()
                cache[key] = col_mask
        mask &= col_mask
    return mask


def _subfeasible(stage_base: float, frac: float) -> float:
    """Graded credit for a rule that fails a feasibility gate (graded fitness).

    Returns a small positive score inside the [0, _GENE_FEASIBLE_OFFSET) band so
    the GA gets a gradient toward feasibility instead of a flat 0.0. ``stage_base``
    orders the gates (later pipeline stage → higher base, so progress always
    scores higher); ``frac`` in [0,1] is the within-stage closeness. Returns 0.0
    when grading is disabled or the rule made zero progress (so the repair path,
    which triggers on exactly 0.0, still fires for truly degenerate rules).
    """
    if not globals().get("GENE_GRADED_FITNESS", True):
        return 0.0
    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
    return stage_base + 0.095 * frac


def _score_genetic(member_bi, rule, sl_pct, tp_pct, direction,
                   full_cluster_size, train_days, _cache=None,
                   _return_trades=False):
    """Score a rule using additive Wilson-based fitness.

    v9.0 speedups applied:
      #20 — column mask cache passed in from the GA worker.
      #21 — pure-NumPy consistency scoring (no pandas DataFrame construction).
      #22 — vectorised SL/TP detection via 2-D numpy slice; sequential pass
             is only the lightweight cooldown filter, not the heavy price scan.
    """
    e = _GEN; arrays = e["arrays"]
    hi = e["hi"]; lo = e["lo"]; op = e["op"]
    fwd = e["fwd"]; n = e["n"]; spread = e["spread"]
    long = (direction == "LONG")

    member_arr = np.asarray(member_bi, dtype=np.int32)
    if len(member_arr) == 0:
        return 0.0

    # ── vectorised rule match (#20 cache) ─────────────────────────────────
    match_mask = _rule_match_mask(member_arr, rule, arrays, _cache)
    matched = int(match_mask.sum())

    retention = matched / max(full_cluster_size, 1)
    min_retention = min((train_days * 1.0) / max(full_cluster_size, 1), 0.70)
    if retention < min_retention:
        # Graded: closer to min_retention scores higher (matched==0 → 0.0 → repair).
        return _subfeasible(0.0, retention / max(min_retention, 1e-9))

    # Box-only cost guard: a box firing on too large a fraction of bars is too
    # loose to be tradeable — reject it cheaply BEFORE the per-trade cooldown sim
    # (which would otherwise loop over tens of thousands of bars and stall the GA).
    # Only active in box-only mode (cluster-gated WANTS high retention). Graded so
    # tighter boxes (closer to the cap) score higher → steers toward selectivity.
    if globals().get("GENE_SCORE_BOX_ONLY", False):
        _maxfrac = float(globals().get("MAX_MATCH_FRAC", 0.25))
        matchfrac = matched / max(full_cluster_size, 1)
        if matchfrac > _maxfrac:
            return _subfeasible(0.0, _maxfrac / matchfrac)

    matched_arr = member_arr[match_mask]

    # ── filter: skip bars too close to the end ────────────────────────────
    valid = matched_arr[matched_arr + 1 < n]
    if len(valid) == 0:
        return 0.0

    # ── vectorised SL/TP detection (#22) ──────────────────────────────────
    # EA-faithful horizon: scan the window the gate sim actually holds a trade
    # (MAX_HOLD_BARS), not FORWARD_BARS — otherwise the GA optimizes a 24-bar
    # objective while the gate/EA book 32-bar holds with timeout exits.
    hold = int(globals().get("MAX_HOLD_BARS", 0) or 0)
    if hold <= 0:
        hold = fwd
    # Build forward-window index matrix: shape (n_trades, hold).
    # Clip to stay in bounds; pad with last valid index (price won't cross).
    fwd_idx = valid[:, None] + np.arange(1, hold + 1, dtype=np.int32)[None, :]
    fwd_idx = np.clip(fwd_idx, 0, n - 1)

    entries = op[valid + 1]
    valid_entry = entries != 0
    if not valid_entry.any():
        return 0.0
    valid = valid[valid_entry]
    entries = entries[valid_entry]
    fwd_idx = fwd_idx[valid_entry]

    adj_entries = entries + (spread if long else -spread)
    sl_v = adj_entries * (1 - sl_pct if long else 1 + sl_pct)
    tp_v = adj_entries * (1 + tp_pct if long else 1 - tp_pct)
    risk = np.abs(adj_entries - sl_v)
    reward = np.abs(tp_v - adj_entries)
    valid_risk = risk > 0
    if not valid_risk.any():
        return 0.0
    valid = valid[valid_risk]
    adj_entries = adj_entries[valid_risk]
    sl_v = sl_v[valid_risk]; tp_v = tp_v[valid_risk]
    risk = risk[valid_risk]; reward = reward[valid_risk]
    fwd_idx = fwd_idx[valid_risk]

    hi_mat = hi[fwd_idx]   # (n_trades, hold)
    lo_mat = lo[fwd_idx]

    # Spread-adjusted effective trigger levels, matching _bt_worker_dir: the
    # exit side pays the spread too (tp_v_eff / sl_v_eff in the gate sim).
    if long:
        tp_eff = tp_v - spread; sl_eff = sl_v - spread
        cum_hi = np.maximum.accumulate(hi_mat, axis=1)
        cum_lo = np.minimum.accumulate(lo_mat, axis=1)
        tp_any = cum_hi >= tp_eff[:, None]
        sl_any = cum_lo <= sl_eff[:, None]
    else:
        tp_eff = tp_v + spread; sl_eff = sl_v + spread
        cum_lo = np.minimum.accumulate(lo_mat, axis=1)
        cum_hi = np.maximum.accumulate(hi_mat, axis=1)
        tp_any = cum_lo <= tp_eff[:, None]
        sl_any = cum_hi >= sl_eff[:, None]

    tp_hit = tp_any.any(axis=1)
    sl_hit = sl_any.any(axis=1)
    tp_idx_v = np.where(tp_hit, np.argmax(tp_any, axis=1), hold + 1)
    sl_idx_v = np.where(sl_hit, np.argmax(sl_any, axis=1), hold + 1)

    # Pessimistic intrabar tie-break (matches _bt_worker_dir / the EA's
    # unknowable tick order): both first trigger on the SAME bar → book the
    # stop. The old `tp_idx < sl_idx → WIN, sl_idx < tp_idx → LOSS, else
    # timeout` silently DROPPED same-bar ties from fitness.
    is_win  = tp_hit & (tp_idx_v < sl_idx_v)
    is_loss = sl_hit & (sl_idx_v <= tp_idx_v)
    timeout = ~(is_win | is_loss)

    # Timeout booking at the hold-window close, like the gate sim: realised
    # move ÷ risk (signed), spread charged on exit. The old path skipped
    # timeouts entirely, so the GA scored a different trade population than
    # the gate measures and the EA trades.
    cl_arr = e["cl"]
    exit_bi = np.minimum(valid + hold, n - 1)
    exit_eff = cl_arr[exit_bi] - spread if long else cl_arr[exit_bi] + spread
    to_pnl = (exit_eff - adj_entries) if long else (adj_entries - exit_eff)

    bars_held = np.where(is_win, tp_idx_v + 1,
                np.where(is_loss, sl_idx_v + 1,
                         np.minimum(hold, (n - 1) - valid))).astype(np.int32)
    booked = np.where(is_win, reward / risk,
             np.where(is_loss, -1.0, to_pnl / risk))
    _comm = float(globals().get("COMMISSION_R", 0.0))
    _swap = float(globals().get("SWAP_R_PER_BAR", 0.0))
    if _comm or _swap:
        booked = booked - (_comm + _swap * bars_held)

    # ── EA-faithful sequential filter: serialized + exit-anchored cooldown ─
    # Mirrors _bt_worker_dir exactly (incl. the timeout +1 anchor) so the GA
    # climbs the same landscape the gate measures and MT5 reproduces. The old
    # filter anchored on the signal bar and let trades overlap.
    cooldown = int(globals().get("COOLDOWN_BARS", 0))
    sel: list[int] = []
    last_exit = -cooldown - 2; cd_anchor = -cooldown - 2
    bh_list = bars_held.tolist(); to_list = timeout.tolist()
    for i, bi in enumerate(valid.tolist()):
        if bi + 1 <= last_exit or (bi + 1) - cd_anchor < cooldown:
            continue
        sel.append(i)
        last_exit = bi + bh_list[i]
        cd_anchor = last_exit + (1 if to_list[i] else 0)

    if _return_trades:
        # Test/debug hook: expose the exact trade stream this fitness eval
        # booked, so parity with _bt_worker_dir can be asserted directly.
        return [(int(valid[i]), float(booked[i]), int(bh_list[i]),
                 bool(to_list[i])) for i in sel]

    if not sel:
        return 0.0
    sel_idx = np.asarray(sel, dtype=np.int32)
    res_sel = booked[sel_idx]
    wins   = int((res_sel > 0).sum())
    losses = int(len(res_sel) - wins)
    total  = wins + losses
    if total < 10:
        # Graded: more trades (toward the min of 10) scores higher.
        return _subfeasible(0.10, total / 10.0)

    avg_rr = float(res_sel[res_sel > 0].mean()) if wins > 0 else 0.0

    # ── pure-NumPy consistency scoring (#21) ─────────────────────────────
    traded_bars  = valid[sel_idx]           # bar indices of all trades
    is_win_arr   = (res_sel > 0)
    q_stab = _time_consistency_np(is_win_arr, traded_bars, n, train_days)
    q_dist = _trade_distribution_np(traded_bars, n, train_days)
    stability = q_stab * q_dist

    # Re-use existing score_rule logic but bypass trades_df construction.
    wr = wins / total
    breakeven = ((1 - wr) / wr) if wr > 0 else 999.0
    if avg_rr < breakeven:
        # Graded: closer to covering break-even RR scores higher.
        return _subfeasible(0.20, avg_rr / max(breakeven, 1e-9))
    # True-R profit factor from booked outcomes (timeout losses < 1R),
    # matching _calc_metrics — not the wins*avg_rr/losses approximation
    # that priced every loss at a flat 1R.
    gw = float(res_sel[res_sel > 0].sum())
    gl = float(-res_sel[res_sel <= 0].sum())
    pf = gw / gl if gl > 0 else 2.0

    use_targets = bool(globals().get("ENABLE_TARGET_SCORING", True))
    # Graded fitness: lift every FEASIBLE score above the [0,0.30) sub-feasible
    # band so a qualifying rule always beats a near-miss. A constant offset
    # preserves the relative ordering of feasible rules (no change to selection
    # among passers); it only separates the two bands.
    _off = float(globals().get("_GENE_FEASIBLE_OFFSET", 0.30)) \
        if globals().get("GENE_GRADED_FITNESS", True) else 0.0
    # Bug #32: additive bonus that grows with rule complexity (number of cols).
    cmplx = _complexity_bonus(len(rule))
    if use_targets:
        wr_pct  = wr * 100.0
        rr_val  = avg_rr / max(breakeven, 0.01)
        tgt_wr  = float(globals().get("TARGET_WR_PCT",         55.0))
        tgt_pf  = float(globals().get("TARGET_PF",              1.5))
        tgt_rr  = float(globals().get("TARGET_RR",              1.3))
        tgt_st  = float(globals().get("TARGET_STABILITY",       0.65))
        tgt_tpd = float(globals().get("TARGET_TRADES_PER_DAY",  1.0))
        tpd     = total / max(train_days, 1)
        return _off + (
            _target_score(wr_pct,    tgt_wr,  SCORE_W_WR) +
            _target_score(pf,        tgt_pf,  SCORE_W_PF) +
            _target_score(rr_val,    tgt_rr,  SCORE_W_RR) +
            _target_score(stability, tgt_st,  SCORE_W_STAB) +
            _target_score(tpd,       tgt_tpd, SCORE_W_TRADES_PER_DAY) +
            cmplx
        )
    q_wr = wilson_lower(wins, total)
    q_pf = min(pf, 4.0) / 4.0
    q_rr = min(avg_rr / max(breakeven, 0.01), 2.0) / 2.0
    return _off + (SCORE_W_WR * q_wr + SCORE_W_PF * q_pf +
            SCORE_W_RR * q_rr + SCORE_W_STAB * stability + cmplx)

def _diagnose_and_repair(rule,col_stats,member_bi,sl_pct,tp_pct,
                          direction,full_cluster_size,train_days,rng_):
    """
    When a rule scores 0, diagnose why and try to repair it.
    Returns repaired rule or None if unrecoverable.
    v5: match counting vectorised.
    """
    e=_GEN; arrays=e["arrays"]

    # Count how many bars match — vectorised (no cache; repair is infrequent)
    member_arr = np.asarray(member_bi, dtype=np.int32)
    matched = int(_rule_match_mask(member_arr, rule, arrays, cache=None).sum())
    retention=matched/max(full_cluster_size,1)

    if retention<0.05:
        # Too few matches — widen the tightest condition
        if not rule: return None
        # find the tightest condition (smallest relative range)
        tightest_col=min(rule.items(),
                         key=lambda x:(x[1][1]-x[1][0])/
                         max(col_stats.get(x[0],(1,2))[1]-
                             col_stats.get(x[0],(1,2))[0],0.001))[0]
        lb,hb=rule[tightest_col]
        vmin,vmax=col_stats.get(tightest_col,(lb-1,hb+1))
        spread_=(hb-lb)*0.30
        new_rule=dict(rule)
        new_rule[tightest_col]=(max(lb-spread_,vmin),min(hb+spread_,vmax))
        return new_rule

    elif matched>=5 and matched<full_cluster_size*0.05:
        # Some matches but too few trades — drop most restrictive condition
        if len(rule)<=1: return None
        # find condition that excludes the most bars — vectorised
        sub_arr = member_arr[:500]
        most_restrictive=None; best_gain=0
        for drop_col in rule:
            test_rule={k:v for k,v in rule.items() if k!=drop_col}
            test_matched = int(_rule_match_mask(sub_arr, test_rule, arrays).sum())
            gain=test_matched-matched
            if gain>best_gain: best_gain=gain; most_restrictive=drop_col
        if most_restrictive:
            new_rule=dict(rule); del new_rule[most_restrictive]
            return new_rule

    return None  # can't repair

def _rand_rule(col_stats,n_cols_min,n_cols_max,rng_):
    """Generate a sparse random rule with 3-5 randomly selected columns.
    Fix 2.5: allow tail-only conditions (not forced to straddle median).
    lo_f and hi_f are sampled freely across the full range.
    """
    all_cols=list(col_stats.keys())
    if not all_cols: return {}
    n_cols=int(rng_.integers(n_cols_min,min(n_cols_max+1,len(all_cols)+1)))
    chosen=rng_.choice(all_cols,size=n_cols,replace=False).tolist()
    r={}
    for col in chosen:
        vmin,vmax=col_stats[col]
        span=vmax-vmin
        if span<1e-9: continue
        # sample two points freely — allows tail-only conditions
        gap=span*0.05  # minimum window width = 5% of range
        lo_f=float(rng_.uniform(vmin,vmax-gap))
        hi_f=float(rng_.uniform(lo_f+gap,vmax))
        if lo_f<hi_f: r[col]=(lo_f,hi_f)
    return r

def _tournament_select(pop,scores,k=3,rng_=None):
    """Pick k random rules, return the best scoring one."""
    if rng_ is None: rng_=np.random.default_rng()
    idxs=rng_.choice(len(pop),size=min(k,len(pop)),replace=False)
    best=max(idxs,key=lambda i:scores[i])
    return pop[best]

def _mutate_rule(r,col_stats,mutate_rate,rng_,can_add=True,can_drop=True):
    new={}
    for col,(lb,hb) in r.items():
        if rng_.random()<mutate_rate:
            sp=(hb-lb)*0.20
            lo2=lb+float(rng_.normal(0,sp))
            hi2=hb+float(rng_.normal(0,sp))
            new[col]=(lo2,hi2) if lo2<hi2 else (lb,hb)
        else: new[col]=(lb,hb)
    # add a new random condition occasionally
    if can_add and rng_.random()<0.15 and len(new)<GENE_N_COLS_MAX:
        avail=[c for c in col_stats if c not in new]
        if avail:
            col=rng_.choice(avail)
            vmin,vmax=col_stats[col]; mid=(vmin+vmax)/2
            lo_f=float(rng_.uniform(vmin,mid))
            hi_f=float(rng_.uniform(mid,vmax))
            if lo_f<hi_f: new[col]=(lo_f,hi_f)
    # drop a condition occasionally
    if can_drop and rng_.random()<0.10 and len(new)>GENE_N_COLS_MIN:
        drop=rng_.choice(list(new.keys()))
        del new[drop]
    return new

def _cross_rules(r1,r2,rng_):
    child={}
    for col in set(list(r1)+list(r2)):
        if col in r1 and col in r2:
            child[col]=r1[col] if rng_.random()<0.5 else r2[col]
        elif col in r1: child[col]=r1[col]
        else:           child[col]=r2[col]
    # trim if too many columns
    if len(child)>GENE_N_COLS_MAX:
        keep=list(rng_.choice(list(child.keys()),
                              size=GENE_N_COLS_MAX,replace=False))
        child={k:child[k] for k in keep}
    return child

def _rule_distance(rule_a: dict, rule_b: dict) -> float:
    """Jaccard distance on feature-column key sets (0 = identical, 1 = disjoint)."""
    keys_a = set(rule_a.keys())
    keys_b = set(rule_b.keys())
    union = keys_a | keys_b
    if not union:
        return 0.0
    return 1.0 - len(keys_a & keys_b) / len(union)


def _genetic_worker(args):
    """v9.0: adds per-worker column-mask cache (#20) and two-pass coarse/full
    evaluation (#23).  Pass 1 runs on every-3rd bar for the first 75% of
    generations (3× cheaper per eval); pass 2 polishes on the full bar set
    for the final 25% of generations.
    """
    # v1.4: optional seed_rule appended to args (backward-compat unpack)
    if len(args) >= 13:
        (cid,member_bi,sl_pct,tp_pct,direction,full_cluster_size,
         island_id,generations,pop_size,mutate_rate,train_days,seed,seed_rule)=args
    else:
        (cid,member_bi,sl_pct,tp_pct,direction,full_cluster_size,
         island_id,generations,pop_size,mutate_rate,train_days,seed)=args
        seed_rule = None
    rng_=np.random.default_rng(seed); arrays=_GEN["arrays"]
    if len(member_bi)<10: return cid,direction,island_id,{},0.0,1.0,1.0

    member_arr_full = np.asarray(member_bi, dtype=np.int32)
    col_stats={}
    for col in GENE_COLS:
        if col not in arrays: continue
        vals=arrays[col][member_arr_full]
        col_stats[col]=(float(np.percentile(vals,5)),
                        float(np.percentile(vals,95)))

    # Box-only (EA-faithful) eval set: score on ALL train bars matching the box —
    # what the EA fires — not just cluster members. col_stats above (cluster) still
    # seeds the search; only the SCORING population changes. Toggle GENE_SCORE_BOX_ONLY.
    if globals().get("GENE_SCORE_BOX_ONLY", False):
        eval_full = np.arange(int(_GEN["n"]), dtype=np.int32)
        eval_full_size = int(_GEN["n"])
    else:
        eval_full = member_arr_full
        eval_full_size = full_cluster_size

    # #23 — coarse subset (every 3rd bar) used for pass 1
    member_arr_coarse = member_arr_full[::3]
    coarse_cluster_size = max(len(member_arr_coarse), 1)
    eval_coarse = eval_full[::3]
    eval_coarse_size = max(len(eval_coarse), 1)
    # cutoff generation where we switch from coarse → full
    pass1_gens = max(1, int(generations * 0.75))
    pass2_gens = generations - pass1_gens

    # v2.1: SL/TP evolution.  An individual is (rule, sl_mult, tp_mult).  The
    # multipliers scale the cluster baseline sl_pct/tp_pct.  When EVOLVE_SLTP is
    # off they stay pinned at 1.0, so scoring is identical to fixed-SL/TP runs.
    evolve_sltp = bool(globals().get("EVOLVE_SLTP", False))
    _m_lo = float(globals().get("SLTP_EVOLVE_MIN", 0.5))
    _m_hi = float(globals().get("SLTP_EVOLVE_MAX", 2.0))

    def _clip_m(m):
        return float(min(max(m, _m_lo), _m_hi))

    def _jit_m(m):
        # log-normal jitter keeps the multiplier positive and symmetric in ratio
        if not evolve_sltp:
            return 1.0
        return _clip_m(m * float(np.exp(rng_.normal(0.0, 0.15))))

    def _clone(ind):
        return (dict(ind[0]), ind[1], ind[2])

    def _score_ind(ind, use_full=False, cache=None):
        # mask cache is keyed on (col, lo, hi) only, so it stays valid across
        # different sl/tp multipliers — sl/tp affect the trade sim, not matches.
        rule, slm, tpm = ind
        bi = eval_full if use_full else eval_coarse
        cs = eval_full_size if use_full else eval_coarse_size
        return _score_genetic(bi, rule, sl_pct * slm, tp_pct * tpm, direction,
                              cs, train_days, _cache=cache)

    def _mut_ind(ind, mrate):
        return (_mutate_rule(ind[0], col_stats, mrate, rng_),
                _jit_m(ind[1]), _jit_m(ind[2]))

    def _cx_ind(a, b):
        # rule via column crossover; each multiplier inherited from a random parent
        slm = a[1] if rng_.random() < 0.5 else b[1]
        tpm = a[2] if rng_.random() < 0.5 else b[2]
        return (_cross_rules(a[0], b[0], rng_), slm, tpm)

    def _rand_ind():
        return (_rand_rule(col_stats, GENE_N_COLS_MIN, GENE_N_COLS_MAX, rng_),
                1.0, 1.0)

    # #20 — per-worker column mask cache (cleared between full/coarse switch)
    cache: dict = {}

    # v1.4: warm-start from leaf rule if provided.  Clamps each bound to the
    # cluster's col_stats range; drops cols outside col_stats entirely.
    # Initial pop = [seed_rule, then pop_size-1 mutations of it].
    seed_clamped: dict = {}
    if seed_rule:
        for col, (lo, hi) in seed_rule.items():
            if col not in col_stats:
                continue
            vmin, vmax = col_stats[col]
            new_lo = max(lo, vmin) if lo != float("-inf") else vmin
            new_hi = min(hi, vmax) if hi != float("inf") else vmax
            if new_lo < new_hi:
                seed_clamped[col] = (new_lo, new_hi)
    if seed_clamped and len(seed_clamped) >= GENE_N_COLS_MIN:
        base_ind = (seed_clamped, 1.0, 1.0)
        pop = [base_ind]
        for _ in range(pop_size - 1):
            pop.append(_mut_ind(base_ind, mutate_rate))
    else:
        pop=[_rand_ind() for _ in range(pop_size)]
    scores=[_score_ind(r, cache=cache) for r in pop]

    best_ind=_clone(pop[int(np.argmax(scores))]); best_s=max(scores)
    no_improve=0; cur_mutate=mutate_rate
    hof = [(best_s, _clone(best_ind))]

    def _run_generations(n_gens, use_full):
        nonlocal pop, scores, best_ind, best_s, no_improve, cur_mutate, hof, cache
        if use_full:
            cache = {}  # stale coarse masks are invalid on full member set
        for gen in range(n_gens):
            if no_improve>=5:
                cur_mutate=min(mutate_rate*(1.0+(no_improve-4)*0.2),mutate_rate*2.0,0.60)
            elif cur_mutate>mutate_rate:
                cur_mutate=max(cur_mutate*0.80,mutate_rate)

            if len(set(round(s,4) for s in scores))/len(scores)<(1-GENE_DIVERSITY_THRESHOLD):
                n_refresh=max(1,pop_size//5)
                worst_idx=sorted(range(len(scores)),key=lambda i:scores[i])[:n_refresh]
                for idx in worst_idx:
                    pop[idx]=_rand_ind()
                    scores[idx]=_score_ind(pop[idx], use_full, cache)

            new_pop=[]
            top2=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)[:2]
            for idx in top2: new_pop.append(_clone(pop[idx]))
            if gen % 5 == 0 and hof:
                _, hof_ind = max(hof, key=lambda x: x[0])
                new_pop.append(_clone(hof_ind))

            while len(new_pop)<pop_size:
                p1=_tournament_select(pop,scores,k=3,rng_=rng_)
                p2=_tournament_select(pop,scores,k=3,rng_=rng_)
                child=_mut_ind(_cx_ind(p1,p2),cur_mutate)
                if not child[0]: continue
                child_score=_score_ind(child, use_full, cache)
                if child_score==0.0:
                    for _ in range(GENE_REPAIR_ATTEMPTS):
                        repaired=_diagnose_and_repair(
                            child[0],col_stats,
                            eval_full if use_full else eval_coarse,
                            sl_pct*child[1],tp_pct*child[2],direction,
                            eval_full_size if use_full else eval_coarse_size,
                            train_days,rng_)
                        if repaired:
                            cand=(repaired, child[1], child[2])
                            rs=_score_ind(cand, use_full, cache)
                            if rs>0:
                                child=cand; child_score=rs; break
                new_pop.append(child)
                if child_score > 0:
                    hof.append((child_score, _clone(child)))
                    hof.sort(key=lambda x: x[0], reverse=True)
                    hof = hof[:10]
                if child_score>best_s:
                    best_s=child_score; best_ind=_clone(child)
                    no_improve=max(0,no_improve-2)

            pop=new_pop[:pop_size]
            scores=[_score_ind(r, use_full, cache) for r in pop]
            gen_best=max(scores)
            if gen_best>best_s:
                best_s=gen_best; best_ind=_clone(pop[int(np.argmax(scores))])
                hof.append((best_s, _clone(best_ind)))
                hof.sort(key=lambda x: x[0], reverse=True)
                hof = hof[:10]
                no_improve=max(0,no_improve-2)
            else:
                no_improve+=1

    def _run_generations_crowding(n_gens, use_full):
        """Deterministic Crowding replacement: children compete with most-similar parent."""
        nonlocal pop, scores, best_ind, best_s, no_improve, cur_mutate, hof, cache
        if use_full:
            cache = {}
        for gen in range(n_gens):
            if no_improve >= 5:
                cur_mutate = min(mutate_rate * (1.0 + (no_improve - 4) * 0.2), mutate_rate * 2.0, 0.60)
            elif cur_mutate > mutate_rate:
                cur_mutate = max(cur_mutate * 0.80, mutate_rate)

            for _ in range(pop_size // 2):
                i1 = int(rng_.integers(0, pop_size))
                i2 = int(rng_.integers(0, pop_size))
                while i2 == i1:
                    i2 = int(rng_.integers(0, pop_size))
                p1_i, p2_i = pop[i1], pop[i2]
                c1 = _mut_ind(_cx_ind(p1_i, p2_i), cur_mutate)
                c2 = _mut_ind(_cx_ind(p2_i, p1_i), cur_mutate)
                if not c1[0] or not c2[0]:
                    continue
                s1 = _score_ind(c1, use_full, cache)
                s2 = _score_ind(c2, use_full, cache)
                d11 = _rule_distance(c1[0], p1_i[0]); d12 = _rule_distance(c1[0], p2_i[0])
                d21 = _rule_distance(c2[0], p1_i[0]); d22 = _rule_distance(c2[0], p2_i[0])
                if d11 + d22 <= d12 + d21:
                    if s1 > scores[i1]: pop[i1] = c1; scores[i1] = s1
                    if s2 > scores[i2]: pop[i2] = c2; scores[i2] = s2
                else:
                    if s1 > scores[i2]: pop[i2] = c1; scores[i2] = s1
                    if s2 > scores[i1]: pop[i1] = c2; scores[i1] = s2

                for cs, ss in ((s1, c1), (s2, c2)):
                    if cs > 0:
                        hof.append((cs, _clone(ss)))
                    if cs > best_s:
                        best_s = cs; best_ind = _clone(ss)

            hof.sort(key=lambda x: x[0], reverse=True)
            hof = hof[:10]
            gen_best = max(scores)
            if gen_best > best_s:
                best_s = gen_best; best_ind = _clone(pop[int(np.argmax(scores))])
                hof.append((best_s, _clone(best_ind)))
                hof.sort(key=lambda x: x[0], reverse=True)
                hof = hof[:10]
                no_improve = max(0, no_improve - 2)
            else:
                no_improve += 1

    use_crowding = bool(globals().get("GENE_USE_CROWDING", False))
    if use_crowding:
        _run_generations_crowding(pass1_gens, use_full=False)
        _run_generations_crowding(pass2_gens, use_full=True)
    else:
        _run_generations(pass1_gens, use_full=False)   # coarse pass
        _run_generations(pass2_gens, use_full=True)    # full-data polish

    if hof:
        hof_best_s, hof_best_ind = max(hof, key=lambda x: x[0])
        if hof_best_s > best_s:
            best_s, best_ind = hof_best_s, hof_best_ind

    return cid,direction,island_id,best_ind[0],best_s,best_ind[1],best_ind[2]

def genetic_refine_parallel(df,indices,labels,candidate_keys,
                             price_dists,bidir_info,fwd,
                             generations,pop_size,mutate_rate,
                             train_days,seed,n_workers,
                             leaf_rules=None):
    """
    Run genetic refinement using island model.
    candidate_keys: list of (cid, direction) tuples.
    leaf_rules: optional dict {cid: {col: (lo, hi)}} of LightGBM leaf-path
        rules used as warm-start seeds.  When provided, each candidate's
        initial population is seeded with the leaf rule (clamped to that
        cluster's col_stats) + mutations of it, instead of random rules.
    """
    t0=time.time()
    use_crowding = bool(globals().get("GENE_USE_CROWDING", False))
    mode_label = "crowding" if use_crowding else f"{GENE_ISLAND_COUNT} islands"
    print(f"  Genetic pass 1: {len(candidate_keys)} rules × {mode_label} on {n_workers} cores ...")
    arrays={col:df[col].values.copy() for col in GENE_COLS if col in df.columns}
    hi=df["high"].values.copy(); lo=df["low"].values.copy()
    cl=df["close"].values.copy(); op=df["open"].values.copy(); n=len(df)
    cm={}
    for pos,bi in enumerate(indices):
        cid=int(labels[pos])
        if cid not in cm: cm[cid]=[]
        cm[cid].append(bi)

    use_crowding = bool(globals().get("GENE_USE_CROWDING", False))
    args=[]
    for cid,direction in candidate_keys:
        d=price_dists.get(cid)
        sl_p=d["sl_pct"] if d else 0.002
        tp_p=d["tp_pct"] if d else 0.003
        full_size=len(cm.get(cid,[]))
        # v1.4: warm-start rule from LightGBM leaf path (if available)
        seed_rule = (leaf_rules or {}).get(cid)
        if use_crowding:
            # Single large-population worker per (cid, direction) — DC maintains diversity
            args.append((cid,cm.get(cid,[]),sl_p,tp_p,direction,
                         full_size,0,generations,pop_size*GENE_ISLAND_COUNT,
                         mutate_rate,train_days,
                         seed+cid*100+{"LONG":0,"SHORT":1}.get(direction,0),
                         seed_rule))
        else:
            for island in range(GENE_ISLAND_COUNT):
                args.append((cid,cm.get(cid,[]),sl_p,tp_p,direction,
                             full_size,island,generations,pop_size,
                             mutate_rate,train_days,
                             seed+cid*100+island*7+{"LONG":0,"SHORT":1}.get(direction,0),
                             seed_rule))

    with mp.Pool(n_workers,initializer=_init_genetic,
                 initargs=(arrays,hi,lo,cl,op,fwd,n,SPREAD_PTS)) as pool:
        island_results=[]; t0g=time.time()
        for i,r in enumerate(pool.imap_unordered(_genetic_worker,args)):
            island_results.append(r)
            _pbar(i+1,len(args),"evolving",t0=t0g)

    # Migration: for each (cid,direction) keep the best rule across islands
    # but also allow the best from other islands to influence
    evolve_sltp = bool(globals().get("EVOLVE_SLTP", False))
    best={}; evolved_sltp={}
    for cid,direction,island_id,rule,score,slm,tpm in island_results:
        key=(cid,direction)
        if key not in best or score>best[key][1]:
            best[key]=(rule,score)
            if evolve_sltp:
                d=price_dists.get(cid)
                base_sl=d["sl_pct"] if d else 0.002
                base_tp=d["tp_pct"] if d else 0.003
                evolved_sltp[key]=(base_sl*slm, base_tp*tpm)

    print(f"  Genetic pass 1 done  [{_elapsed(t0)}]")
    # evolved_sltp is {(cid,direction): (sl_pct, tp_pct)} — empty unless
    # EVOLVE_SLTP is on; downstream falls back to the cluster baseline.
    return best, evolved_sltp  # {(cid,direction): (rule, score)}, sl/tp overrides

def genetic_pass2_parallel(df,indices,labels,top_keys,genetic_p1,
                            price_dists,fwd,generations,pop_size,
                            mutate_rate,train_days,seed,n_workers,
                            sltp_overrides=None):
    """Pass 2: tighter search starting warm from pass 1 best rules.

    sltp_overrides: optional {(cid,direction): (sl_pct, tp_pct)} from SL/TP
    evolution.  Pass 2 refines the entry rule against these fixed evolved
    stops rather than re-evolving them, so the pass-1 choice carries through.
    """
    sltp_overrides = sltp_overrides or {}
    t0=time.time()
    print(f"  Genetic pass 2: {len(top_keys)} rules on {n_workers} cores ...")
    arrays={col:df[col].values.copy() for col in GENE_COLS if col in df.columns}
    hi=df["high"].values.copy(); lo=df["low"].values.copy()
    cl=df["close"].values.copy(); op=df["open"].values.copy(); n=len(df)
    cm={}
    for pos,bi in enumerate(indices):
        cid=int(labels[pos])
        if cid not in cm: cm[cid]=[]
        cm[cid].append(bi)

    args=[]
    for cid,direction in top_keys:
        ov=sltp_overrides.get((cid,direction))
        if ov:
            sl_p,tp_p=ov
        else:
            d=price_dists.get(cid)
            sl_p=d["sl_pct"] if d else 0.002
            tp_p=d["tp_pct"] if d else 0.003
        p1_rule=genetic_p1.get((cid,direction),({},0.0))[0]
        full_size=len(cm.get(cid,[]))
        args.append((cid,cm.get(cid,[]),sl_p,tp_p,direction,full_size,
                     p1_rule,generations,pop_size,mutate_rate,train_days,
                     seed+cid*100+1000))

    with mp.Pool(n_workers,initializer=_init_genetic,
                 initargs=(arrays,hi,lo,cl,op,fwd,n,SPREAD_PTS)) as pool:
        results=[]; t0g=time.time()
        for i,r in enumerate(pool.imap_unordered(_genetic_p2_worker,args)):
            results.append(r)
            _pbar(i+1,len(args),"pass 2",t0=t0g)

    print(f"  Pass 2 done  [{_elapsed(t0)}]")
    return {key:(rule,score) for key,rule,score in results}


def _genetic_p2_worker(args):
    """Module-level pass-2 genetic worker (required for Windows spawn pickling)."""
    (cid,member_bi,sl_pct,tp_pct,direction,full_size,
     pass1_rule,generations,pop_size,mutate_rate,train_days,seed)=args
    rng_=np.random.default_rng(seed); arrays_=_GEN["arrays"]
    if len(member_bi)<10: return (cid,direction),pass1_rule,0.0
    col_stats={}
    for col in GENE_COLS:
        if col not in arrays_: continue
        vals=np.array([float(arrays_[col][bi]) for bi in member_bi])
        col_stats[col]=(float(np.percentile(vals,PASS2_QUANTILE_LO*100)),
                        float(np.percentile(vals,PASS2_QUANTILE_HI*100)))
    # Box-only (EA-faithful) eval set — score on all train bars matching the box,
    # not just cluster members (see GENE_SCORE_BOX_ONLY). col_stats above (cluster
    # IQR) still seeds the search; only the scoring population changes.
    if globals().get("GENE_SCORE_BOX_ONLY", False):
        eval_bi = np.arange(int(_GEN["n"]), dtype=np.int32); eval_size = int(_GEN["n"])
    else:
        eval_bi = member_bi; eval_size = full_size
    # Do NOT mirror col_stats for SHORT (fix 2.4)
    def _narrow(r):
        new={}
        for col,(lb,hb) in r.items():
            if col in col_stats:
                vmin,vmax=col_stats[col]
                new[col]=(max(lb,vmin),min(hb,vmax)) if max(lb,vmin)<min(hb,vmax) \
                          else (vmin,vmax)
            else: new[col]=(lb,hb)
        return new

    seed_r=_narrow(pass1_rule) if pass1_rule else _rand_rule(
        col_stats,GENE_N_COLS_MIN,GENE_N_COLS_MAX,rng_)
    pop=[seed_r]+[_mutate_rule(seed_r,col_stats,mutate_rate,rng_,False,True)  # can_drop=True (fix 2.1a)
                  for _ in range(pop_size-1)]
    scores=[_score_genetic(eval_bi,r,sl_pct,tp_pct,direction,
                           eval_size,train_days) for r in pop]
    best_r=pop[int(np.argmax(scores))]; best_s=max(scores)
    for _ in range(generations):
        p1=_tournament_select(pop,scores,k=3,rng_=rng_)
        p2=_tournament_select(pop,scores,k=3,rng_=rng_)
        child=_mutate_rule(_cross_rules(p1,p2,rng_),col_stats,
                           mutate_rate,rng_,False,False)
        if not child: continue
        cs=_score_genetic(eval_bi,child,sl_pct,tp_pct,
                          direction,eval_size,train_days)
        if cs==0:
            rep=_diagnose_and_repair(child,col_stats,eval_bi,sl_pct,tp_pct,
                                     direction,eval_size,train_days,rng_)
            if rep:
                cs2=_score_genetic(eval_bi,rep,sl_pct,tp_pct,
                                   direction,eval_size,train_days)
                if cs2>0: child=rep; cs=cs2
        idx_worst=int(np.argmin(scores))
        if cs>=scores[idx_worst]: pop[idx_worst]=child; scores[idx_worst]=cs
        if cs>best_s: best_s=cs; best_r=dict(child)
    return (cid,direction),best_r,best_s


def _bt_refined_worker(args):
    """Module-level backtest worker for backtest_refined (Windows pickling)."""
    key,fbi,sl_p,tp_p,dirn=args
    cid=key[0] if isinstance(key,tuple) else key
    _,_,trades=_bt_worker_dir((cid,fbi,sl_p,tp_p,dirn))
    return key,trades,fbi

# ─────────────────────────────────────────────────────────────────────────────
# POST-GENETIC BACKTEST
# ─────────────────────────────────────────────────────────────────────────────
def backtest_refined(df, indices, labels, genetic_rules, price_dists,
                     fwd, n_workers, trading_days, sltp_overrides=None,
                     box_only=False):
    """Backtest only bars matching each genetic rule.

    sltp_overrides: optional {(cid,direction): (sl_pct, tp_pct)} from SL/TP
    evolution.  When a key is present its evolved stop/target override the
    cluster's quantile baseline (for the sim and the reported metrics).

    box_only: when True, each rule fires on EVERY bar matching its feature box,
    ignoring cluster membership — exactly how the exported EA behaves in MT5
    (it has no cluster/shape gate). The default (False) keeps the cluster gate
    (cluster members ∩ box), the historical behaviour. Use box_only=True to get
    an EA-faithful estimate and to quantify box-inflation vs the gated count.
    """
    sltp_overrides = sltp_overrides or {}
    hi = df["high"].values.copy(); lo = df["low"].values.copy()
    cl = df["close"].values.copy(); op = df["open"].values.copy(); n = len(df)
    sess = (df["session"].values.copy() if "session" in df.columns
            else np.zeros(n, dtype=np.int8))
    allowed = [{"ASIAN": 0, "LONDON": 1, "NY": 2, "OVERLAP": 3, "OFF": 4}[s]
               for s in ALLOWED_SESSIONS
               if s in {"ASIAN", "LONDON", "NY", "OVERLAP", "OFF"}]
    col_arrays = {col: df[col].values.copy()
                  for col in GENE_COLS if col in df.columns}
    cm = {}
    for pos, bi in enumerate(indices):
        cid = int(labels[pos])
        if cid not in cm: cm[cid] = []
        cm[cid].append(bi)

    # box_only: candidate pool is EVERY bar (the EA has no cluster gate), so each
    # rule matches against all indices rather than just its cluster's members.
    all_bi = list(indices) if box_only else None

    filtered_args = []
    for (cid, direction), (rule, score) in genetic_rules.items():
        ov = sltp_overrides.get((cid, direction))
        if ov:
            sl_p, tp_p = ov
        else:
            d = price_dists.get(cid)
            sl_p = d["sl_pct"] if d else 0.002
            tp_p = d["tp_pct"] if d else 0.003
        source_bi = all_bi if box_only else cm.get(cid, [])
        filtered_bi = [bi for bi in source_bi
                       if all(not (c in col_arrays) or
                              (lb <= float(col_arrays[c][bi]) <= hb)
                              for c, (lb, hb) in rule.items())]
        filtered_args.append(((cid, direction), filtered_bi, sl_p, tp_p, direction))

    # ── snapshot arrays added to init_args ───────────────────────────────────
    _z = np.zeros(n)
    init_args = (
        hi, lo, cl, op, sess, fwd, n, SPREAD_PTS, REALISTIC_ENTRY,
        MAX_HOLD_BARS, allowed, COOLDOWN_BARS,
        df["regime"].values.copy()       if "regime"        in df.columns else _z.copy(),
        df["trend"].values.copy()        if "trend"         in df.columns else _z.copy(),
        df["rsi14"].values.copy()        if "rsi14"         in df.columns else _z.copy(),
        df["stoch_k"].values.copy()      if "stoch_k"       in df.columns else _z.copy(),
        df["atr_pct"].values.copy()      if "atr_pct"       in df.columns else _z.copy(),
        df["mtf_bull_score"].values.copy() if "mtf_bull_score" in df.columns else _z.copy(),
    )

    with mp.Pool(n_workers, initializer=_init_bt, initargs=init_args) as pool:
        raw = pool.map(_bt_refined_worker, filtered_args)

    results = {}; idx_arr = df.index; _n_idx = len(idx_arr)
    for key, trades, fbi in raw:
        orig_size = len(cm.get(key[0] if isinstance(key, tuple) else key, []))
        filt_size = len(fbi)
        tdf = pd.DataFrame([
            {"time": idx_arr[bi],
             "entry_time": idx_arr[min(bi + 1, _n_idx - 1)],
             "exit_time":  idx_arr[min(bi + bh, _n_idx - 1)],
             "pnl_pts":    round(rr * abs(entry - sl_v), 5),
             "result": res, "rr": rr,
             "entry": entry, "sl": sl_v, "tp": tp_v,
             "direction": dirn, "bars_held": bh,
             "regime": reg, "session": sess_v, "trend": trn,
             "rsi14": rsi14, "stoch_k": stk,
             "atr_pct": atrp, "mtf_bull_score": mtf}
            for bi, res, rr, entry, sl_v, tp_v, dirn, bh,
                reg, sess_v, trn, rsi14, stk, atrp, mtf in trades
        ])
        # Full EA feature snapshot at each signal bar (feat_* columns) — the
        # training data for the optional ONNX trade filter (onnx_filter.py).
        if not tdf.empty:
            _bis = np.asarray([t[0] for t in trades], dtype=np.int64)
            for _fc in EA_FEATURE_COLS:
                if _fc in df.columns:
                    tdf["feat_" + _fc] = df[_fc].values[_bis]
        cid = key[0] if isinstance(key, tuple) else key
        m = _calc_metrics(trades, filt_size, trading_days, tdf)
        m["cluster"] = cid; m["key"] = key
        m["original_signals"] = orig_size
        m["filtered_signals"] = filt_size
        m["signal_retention"] = round(filt_size / max(orig_size, 1) * 100, 1)
        d = price_dists.get(cid) or {}
        ov = sltp_overrides.get(key) if isinstance(key, tuple) else None
        if ov:
            m["sl_pct"], m["tp_pct"] = float(ov[0]), float(ov[1])
            m["sl_pct_label"] = "evolved"
            m["tp_pct_label"] = "evolved"
            m["implied_rr"] = round(ov[1] / ov[0], 3) if ov[0] > 0 else 0.0
        else:
            m["sl_pct"] = d.get("sl_pct", 0.002)
            m["tp_pct"] = d.get("tp_pct", 0.003)
            m["sl_pct_label"] = d.get("sl_pct_label", "?")
            m["tp_pct_label"] = d.get("tp_pct_label", "?")
            m["implied_rr"] = d.get("implied_rr", 0.0)
        m["direction"] = key[1] if isinstance(key, tuple) else "LONG"
        m["trades"] = tdf
        results[key] = m
    return results

# ─────────────────────────────────────────────────────────────────────────────
# QUALITY ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
def pattern_degradation(trades_df):
    if trades_df is None or trades_df.empty: return 0.0,0.0
    res=trades_df[trades_df["result"].isin(["WIN","LOSS"])]
    if len(res)<10: return 0.0,0.0
    overall=res["result"].eq("WIN").mean()*100
    recent=res.tail(min(RECENT_BARS,len(res)//3))
    if recent.empty: return round(overall,1),round(overall,1)
    return round(overall,1),round(recent["result"].eq("WIN").mean()*100,1)

def cluster_correlation_matrix(labels,indices,n_cl):
    bar_to_cl={}
    for pos,bi in enumerate(indices):
        cid=int(labels[pos])
        if bi not in bar_to_cl: bar_to_cl[bi]=set()
        bar_to_cl[bi].add(cid)
    corr=np.zeros((n_cl,n_cl)); counts=np.zeros(n_cl)
    for bi,cids in bar_to_cl.items():
        for c in cids: counts[c]+=1
        for c1 in cids:
            for c2 in cids:
                if c1!=c2: corr[c1,c2]+=1
    for i in range(n_cl):
        for j in range(n_cl):
            if i!=j and counts[i]>0: corr[i,j]/=counts[i]
    return corr

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def describe_cluster(df,indices,labels,cid,w):
    mb=[indices[i] for i in range(len(indices)) if labels[i]==cid]
    if not mb: return "empty"
    sub=df.iloc[mb]
    def _med(col): return sub[col].median() if col in sub else 0
    def _mode(col):
        m=sub[col].mode() if col in sub else pd.Series([0])
        return m.iloc[0] if not m.empty else 0
    rsi_med=_med("rsi14"); trend_m=_mode("trend")
    bull_pct=_med("bull")*100; bb_med=_med("bb_width")
    vol_med=_med("vol_ratio"); regime_m=int(_mode("regime"))
    rsi_lbl="oversold" if rsi_med<40 else ("overbought" if rsi_med>60 else "neutral")
    trnd_lbl="uptrend" if trend_m==1 else ("downtrend" if trend_m==-1 else "ranging")
    bb_lbl="squeeze" if bb_med<0.008 else ("wide" if bb_med>0.02 else "normal")
    return (f"RSI {rsi_med:.0f} ({rsi_lbl}) | {trnd_lbl} | BB {bb_lbl} | "
            f"{bull_pct:.0f}% bull | vol×{vol_med:.1f} | "
            f"regime:{REGIME_NAMES.get(regime_m,'?')}")

def rule_to_text(rule,direction=None):
    if not rule: return "    (no conditions)"
    parts=[]
    if direction: parts.append(f"    {'DIRECTION':<22} {direction}")
    for col,(lo_v,hi_v) in rule.items():
        lbl=COND_LABELS.get(col,col)
        parts.append(f"    {col:<22} {lo_v:>8.4f}  to  {hi_v:>8.4f}   # {lbl}")
    return "\n".join(parts)

# ─────────────────────────────────────────────────────────────────────────────
# CHARTS
# ─────────────────────────────────────────────────────────────────────────────
def plot_performance(results_list,out):
    valid=[r for r in results_list if r.get("total_trades",0)>=5]
    if not valid: return
    nc=len(valid); clrs=[PALETTE[r["cluster"]%len(PALETTE)] for r in valid]
    names=[f"C{r['cluster']}\n{r.get('direction','L')[0]}" for r in valid]
    x=np.arange(nc)
    fig=plt.figure(figsize=(max(14,nc*1.5),14),facecolor=BG)
    gs=gridspec.GridSpec(3,3,figure=fig,hspace=0.50,wspace=0.35)
    def _ax(pos,title):
        ax=fig.add_subplot(pos); ax.set_facecolor(PANEL)
        for s in ax.spines.values(): s.set_color(GRID)
        ax.tick_params(colors=MUTED,labelsize=7); ax.grid(color=GRID,lw=0.4,alpha=0.5,axis="y")
        ax.set_title(title,color=TEXT,fontsize=9,fontweight="bold",pad=6); return ax
    ax=_ax(gs[0,0],"Win Rate % (Wilson)")
    bars=ax.bar(x,[r.get("wilson_wr",0) for r in valid],color=clrs,alpha=0.85,width=0.6)
    ax.axhline(50,color=MUTED,lw=0.7,ls="--",alpha=0.5); ax.set_ylim(0,100)
    ax.set_xticks(x); ax.set_xticklabels(names,fontsize=7)
    for bar,r in zip(bars,valid):
        ax.text(bar.get_x()+bar.get_width()/2,r.get("wilson_wr",0)+1,
                f"{r.get('wilson_wr',0)}%",ha="center",color=TEXT,fontsize=7,fontweight="bold")
    ax=_ax(gs[0,1],"Profit Factor")
    pfs=[min(r.get("profit_factor",0),8) for r in valid]
    bars=ax.bar(x,pfs,color=clrs,alpha=0.85,width=0.6)
    ax.axhline(1,color=MUTED,lw=0.7,ls="--",alpha=0.5)
    ax.set_xticks(x); ax.set_xticklabels(names,fontsize=7)
    for bar,r,v in zip(bars,valid,pfs):
        ax.text(bar.get_x()+bar.get_width()/2,v+0.05,
                str(r.get("profit_factor",0)),ha="center",color=TEXT,fontsize=7,fontweight="bold")
    ax=_ax(gs[0,2],"Composite Score")
    bars=ax.bar(x,[r.get("composite_score",0) for r in valid],color=clrs,alpha=0.85,width=0.6)
    ax.set_xticks(x); ax.set_xticklabels(names,fontsize=7)
    ax=_ax(gs[1,0],"Max Drawdown (R)")
    bars=ax.bar(x,[r.get("max_drawdown_r",0) for r in valid],color=DOWN,alpha=0.75,width=0.6)
    ax.axhline(MAX_DRAWDOWN_R,color=MUTED,lw=0.7,ls="--",alpha=0.5)
    ax.set_xticks(x); ax.set_xticklabels(names,fontsize=7)
    ax=_ax(gs[1,1],"Signals/Day")
    bars=ax.bar(x,[r.get("per_day",0) for r in valid],color=clrs,alpha=0.85,width=0.6)
    ax.axhline(1,color=MUTED,lw=0.7,ls="--",alpha=0.5)
    ax.set_xticks(x); ax.set_xticklabels(names,fontsize=7)
    ax=_ax(gs[1,2],"Trade Count")
    ax.bar(x,[r.get("wins",0) for r in valid],color=UP,alpha=0.85,width=0.6,label="Wins")
    ax.bar(x,[r.get("losses",0) for r in valid],color=DOWN,alpha=0.85,width=0.6,
           label="Losses",bottom=[r.get("wins",0) for r in valid])
    ax.set_xticks(x); ax.set_xticklabels(names,fontsize=7)
    ax.legend(fontsize=7,facecolor=PANEL,labelcolor=MUTED)
    ax=fig.add_subplot(gs[2,:]); ax.set_facecolor(PANEL)
    for s in ax.spines.values(): s.set_color(GRID)
    ax.tick_params(colors=MUTED,labelsize=7); ax.grid(color=GRID,lw=0.4,alpha=0.5)
    ax.set_title("Equity Curves (R)",color=TEXT,fontsize=9,fontweight="bold")
    for r,col in zip(valid,clrs):
        eq=r.get("equity",[0])
        if len(eq)>1:
            ax.plot(eq,color=col,lw=1.2,
                    label=f"C{r['cluster']} {r.get('direction','L')[0]} "
                          f"WR={r.get('wilson_wr',0)}% PF={r.get('profit_factor',0)}")
    ax.axhline(0,color=MUTED,lw=0.6,ls="--",alpha=0.5)
    ax.legend(fontsize=6,facecolor=PANEL,labelcolor=TEXT,loc="upper left",ncol=3)
    ax.set_xlabel("Trade #",color=MUTED,fontsize=7)
    fig.suptitle(f"Pattern Performance v4 | seed={RANDOM_SEED} | "
                 f"spread={SPREAD_PTS}pts | Wilson WR | Bidirectional",
                 color=TEXT,fontsize=10,fontweight="bold",y=1.002)
    plt.savefig(out,dpi=150,bbox_inches="tight",facecolor=BG); plt.close(fig)
    print(f"  Performance -> {out}")

def plot_regime_distribution(df,out):
    if "regime" not in df.columns: return
    fig,axes=plt.subplots(1,2,figsize=(14,5),facecolor=BG)
    counts=df["regime"].value_counts().sort_index()
    cols_r=[PALETTE[i] for i in range(5)]
    labels_r=[REGIME_NAMES[i] for i in range(5)]
    counts_v=[counts.get(i,0) for i in range(5)]
    ax=axes[0]; ax.set_facecolor(PANEL)
    for s in ax.spines.values(): s.set_color(GRID)
    ax.tick_params(colors=MUTED,labelsize=7.5)
    bars=ax.bar(range(5),counts_v,color=cols_r,alpha=0.85,width=0.6)
    ax.set_xticks(range(5)); ax.set_xticklabels(labels_r,rotation=15,ha="right",fontsize=8)
    ax.set_title("Regime Distribution",color=TEXT,fontsize=9,fontweight="bold")
    for bar,v in zip(bars,counts_v):
        ax.text(bar.get_x()+bar.get_width()/2,v+50,f"{v:,}",
                ha="center",color=TEXT,fontsize=7.5)
    ax=axes[1]; ax.set_facecolor(PANEL)
    for s in ax.spines.values(): s.set_color(GRID)
    ax.pie(counts_v,labels=labels_r,colors=cols_r,autopct="%1.1f%%",startangle=140,
           textprops={"color":TEXT,"fontsize":8})
    ax.set_title("Regime %",color=TEXT,fontsize=9,fontweight="bold")
    fig.suptitle("Market Regime Analysis",color=TEXT,fontsize=11,fontweight="bold")
    plt.tight_layout()
    plt.savefig(out,dpi=150,bbox_inches="tight",facecolor=BG); plt.close(fig)
    print(f"  Regime chart -> {out}")


# ─────────────────────────────────────────────────────────────────────────────
# .SET FILE GENERATOR — MetaTrader 5 parameter file for universal template EA
# ─────────────────────────────────────────────────────────────────────────────

# MT5 .set format requires the raw integer value for ENUM_TIMEFRAMES inputs.
# Writing the Python string name (e.g. "PERIOD_M15") causes MT5 to silently
# fall back to 0 = PERIOD_CURRENT for all SignalTFn slots.
_TF_NAME_TO_INT: dict[str, int] = {
    "PERIOD_CURRENT": 0,
    "PERIOD_M1": 1,   "PERIOD_M2": 2,   "PERIOD_M3": 3,   "PERIOD_M4": 4,
    "PERIOD_M5": 5,   "PERIOD_M6": 6,   "PERIOD_M10": 10, "PERIOD_M12": 12,
    "PERIOD_M15": 15, "PERIOD_M20": 20, "PERIOD_M30": 30,
    "PERIOD_H1": 16385, "PERIOD_H2": 16386, "PERIOD_H3": 16387,
    "PERIOD_H4": 16388, "PERIOD_H6": 16390, "PERIOD_H8": 16392,
    "PERIOD_H12": 16396, "PERIOD_D1": 16408, "PERIOD_W1": 32769,
    "PERIOD_MN1": 49153,
}


def generate_set_file(pattern_no, cid, direction, rule, sl_pct, tp_pct,
                      bidir_mode, discriminator, r, out_path):
    """
    Write a MetaTrader 5 .set file for one qualifying pattern.
    Matches the input variable names of the universal template EA.

    pattern_no  : sequential number (1, 2, 3 ...) for easy reference
    cid         : cluster id
    direction   : LONG / SHORT
    rule        : genetic conditions dict {col: (lo, hi)}
    sl_pct      : SL as fraction of price (e.g. 0.00522 = 0.522%)
    tp_pct      : TP as fraction of price (e.g. 0.00363 = 0.363%)
    bidir_mode  : LONG_ONLY / SHORT_ONLY / BIDIRECTIONAL
    discriminator: dict with col/thresh/dir/accuracy or None
    r           : full result dict for stats header
    """
    import math

    # Features whose EA implementation returns only integer values.
    # Genetic bounds are computed on continuous training-data arrays which can
    # produce fractional limits (e.g. mtf_bull_score_hi=1.8839 when the true
    # max is 2.0).  We snap lo DOWN and hi UP to the nearest integer so the EA
    # never silently blocks bars that lie at a valid integer boundary.
    INTEGER_BOUND_FEATURES = {
        "mtf_bull_score",   # {0, 1, 2}
        "mtf_bear_score",   # {0, 1, 2}
        "regime",           # {0, 1, 2, 3, 4}
        "trend",            # {-1, 0, 1}
        "bull",             # {0, 1}
        "bb_expanding",     # {0, 1}
        "inside_bar",       # {0, 1}
        "outside_bar",      # {0, 1}
        "htf_div",          # {-1, 0, 1}
        "session",          # {0, 1, 2, 3, 4}
        "vol_price_div",    # {-1, 0, 1}
        "prev_sess_bias",   # {-1, 0, 1}
        "sd_zone",          # {-1, 0, 1}  FIX: was missing — fractional bounds
                            # (e.g. lo=0.3) would exclude valid integer 0 in EA
    }

    def _snap_bounds(col, lo_v, hi_v):
        """Snap lo down and hi up to nearest integer for discrete features."""
        if col in INTEGER_BOUND_FEATURES:
            lo_snapped = float(math.floor(lo_v))
            hi_snapped = float(math.ceil(hi_v))
            if lo_snapped != lo_v or hi_snapped != hi_v:
                print(f"  [set_file] {col}: snapped bounds "
                      f"[{lo_v:.6f}, {hi_v:.6f}] → "
                      f"[{lo_snapped:.6f}, {hi_snapped:.6f}]")
            return lo_snapped, hi_snapped
        return lo_v, hi_v

    # Direction mode: 0=long, 1=short, 2=auto
    dir_mode = 0 if direction=="LONG" else 1
    # For bidirectional patterns with a discriminator, use auto mode (2)
    if bidir_mode=="BIDIRECTIONAL" and discriminator:
        dir_mode = 2

    # Map GENE_COLS to the EA input variable names
    # These must match exactly what is in the universal template EA
    COL_TO_EA = {
        "rsi14":          ("rsi14_lo",        "rsi14_hi"),
        "macd_norm":      ("macd_norm_lo",     "macd_norm_hi"),
        "atr_pct":        ("atr_pct_lo",       "atr_pct_hi"),
        "bb_width":       ("bb_width_lo",      "bb_width_hi"),
        "trend":          ("trend_lo",         "trend_hi"),
        "mtf_bull_score": ("mtf_bull_score_lo","mtf_bull_score_hi"),
        "body_pct":       ("body_pct_lo",      "body_pct_hi"),
        "rng_atr":        ("rng_atr_lo",       "rng_atr_hi"),
        "session":        ("session_lo",       "session_hi"),
        "vol_ratio":      ("vol_ratio_lo",     "vol_ratio_hi"),
        "vol_body_conf":  ("vol_body_conf_lo", "vol_body_conf_hi"),
        "regime":         ("regime_lo",        "regime_hi"),
        "vol_price_div":  ("vol_price_div_lo", "vol_price_div_hi"),
        "bb_expanding":   ("bb_expanding_lo",  "bb_expanding_hi"),
        "prev_sess_bias": ("prev_sess_bias_lo","prev_sess_bias_hi"),
        "poc_dist":       ("poc_dist_lo",      "poc_dist_hi"),
        "bull":           ("bull_lo",          "bull_hi"),
        "uwk_pct":        ("uwk_pct_lo",       "uwk_pct_hi"),
        "lwk_pct":        ("lwk_pct_lo",       "lwk_pct_hi"),
        # v5 features
        "stoch_k":        ("stoch_k_lo",        "stoch_k_hi"),
        "stoch_d":        ("stoch_d_lo",        "stoch_d_hi"),
        "pin_bar":        ("pin_bar_lo",         "pin_bar_hi"),
        "inside_bar":     ("inside_bar_lo",      "inside_bar_hi"),
        "outside_bar":    ("outside_bar_lo",     "outside_bar_hi"),
        "htf_div":        ("htf_div_lo",         "htf_div_hi"),
        "rolling_sharpe": ("rolling_sharpe_lo",  "rolling_sharpe_hi"),
        "sd_zone":        ("sd_zone_lo",         "sd_zone_hi"),
        "vwap_dist":      ("vwap_dist_lo",       "vwap_dist_hi"),
    }

    # Maps column name → numeric index in the EA's feat[27] array.
    # Must stay in sync with PatternDiscoveryEA.mq5 COLUMN INDEX TABLE.
    COL_INDEX = {
        "rsi14":          0,
        "macd_norm":      1,
        "atr_pct":        2,
        "bb_width":       3,
        "trend":          4,
        "mtf_bull_score": 5,
        "body_pct":       6,
        "rng_atr":        7,
        "vol_ratio":      8,
        "vol_body_conf":  9,
        "regime":         10,
        "vol_price_div":  11,
        "bb_expanding":   12,
        "prev_sess_bias": 13,
        "poc_dist":       14,
        "bull":           15,
        "uwk_pct":        16,
        "lwk_pct":        17,
        "stoch_k":        18,
        "stoch_d":        19,
        "pin_bar":        20,
        "inside_bar":     21,
        "outside_bar":    22,
        "htf_div":        23,
        "rolling_sharpe": 24,
        "sd_zone":        25,
        "vwap_dist":      26,
    }

    # Conditions absent from `rule` are simply omitted from the .set file

    lines = []

    # Header comment. The "Test:" line carries the EA-faithful (box-only) OOS
    # numbers — the only figures an MT5 backtest of this .set can reproduce,
    # since the EA fires on the exported feature box with no cluster/shape
    # gate. The cluster-gated test numbers stay as a separate diagnostic line.
    _ea_wr = r.get("ea_test_wr", r.get("test_wr", 0))
    _ea_pf = r.get("ea_test_pf", r.get("test_pf", 0))
    _ea_n  = r.get("ea_test_trades", r.get("test_trades", 0))
    lines += [
        f"; Pattern {pattern_no} — Cluster {cid} [{direction}] [{bidir_mode}]",
        f"; Train: WR={r.get('win_rate_',0)}%  Wilson={r.get('wilson_wr',0)}%"
        f"  PF={r.get('profit_factor',0)}  Score={r.get('composite_score',0)}",
        f"; SignalRetention={r.get('signal_retention',0)} (report-only:"
        f" post-rule trades / shape-cluster size; EA box is a superset → live inflates)",
        f"; Test:  WR={_ea_wr}%  PF={_ea_pf}  Trades={_ea_n}"
        f"  (EA-faithful box-only OOS — compare THIS to your MT5 backtest)",
        f"; TestGated: WR={r.get('test_wr',0)}%  PF={r.get('test_pf',0)}"
        f"  Trades={r.get('test_trades',0)}"
        f"  (cluster∩box diagnostic; MT5 cannot reproduce this)",
        f"; SL={sl_pct*100:.3f}%  TP={tp_pct*100:.3f}%"
        f"  Implied RR={r.get('implied_rr',0)}",
        f"; Generated by Pattern Discovery v6 | seed={RANDOM_SEED}",
        "",
    ]

    # Magic number: base 10000 + pattern number
    lines.append(f"MagicNumber={10000+pattern_no}")

    # Direction
    lines.append(f"DirectionMode={dir_mode}")
    lines.append(f"; 0=LongOnly 1=ShortOnly 2=Auto(discriminator)")
    if dir_mode == 2:
        lines += [
            "; ┌──────────────────────────────────────────────────────────────┐",
            "; │  WARNING: BIDIRECTIONAL pattern — DO NOT override            │",
            "; │  DirectionMode to 0/1 in MT5.  Reported metrics assume the   │",
            "; │  discriminator picks LONG/SHORT per bar.  Forcing a single   │",
            "; │  side silently drops ~half the intended trades.              │",
            "; │  For a true single-direction strategy, re-run Discovery      │",
            "; │  with FORCE_DIRECTION=long_only (or short_only).             │",
            "; └──────────────────────────────────────────────────────────────┘",
        ]

    # SignalTF1..4 — feed the EA's multi-TF inputs with the timeframes that
    # were actually used in this discovery run. Map CSV filename → MQL5
    # ENUM_TIMEFRAMES identifier so the converter (set_to_mql.py) can
    # substitute these directly into the input block.
    _TF_RE = re.compile(r"_(m1|m5|m15|m30|h1|h4|d1|w1|mn1)\.csv$", re.IGNORECASE)
    _TF_MAP = {
        "m1": "PERIOD_M1", "m5": "PERIOD_M5", "m15": "PERIOD_M15",
        "m30": "PERIOD_M30", "h1": "PERIOD_H1", "h4": "PERIOD_H4",
        "d1": "PERIOD_D1", "w1": "PERIOD_W1", "mn1": "PERIOD_MN1",
    }
    def _csv_to_period(fn: str) -> str:
        if not fn:
            return "PERIOD_CURRENT"
        m = _TF_RE.search(fn)
        return _TF_MAP.get(m.group(1).lower(), "PERIOD_CURRENT") if m else "PERIOD_CURRENT"

    _tf_files_for_set = [TF1_FILE, TF2_FILE, TF3_FILE, TF4_FILE, TF5_FILE]
    _primary_idx_for_set = max(1, min(5, int(PRIMARY_TF))) - 1
    # Collect the SIGNAL slots only (everything except primary), in slot order,
    # mapped to ENUM_TIMEFRAMES. Pad/truncate to exactly 4 SignalTF inputs.
    _signal_periods = [
        _csv_to_period(fn)
        for i, fn in enumerate(_tf_files_for_set)
        if i != _primary_idx_for_set and fn
    ]
    while len(_signal_periods) < 4:
        _signal_periods.append("PERIOD_CURRENT")
    _signal_periods = _signal_periods[:4]
    lines += [
        "",
        "; Multi-TF signal slots (forwarded to PatternDiscoveryEA's SignalTFn inputs).",
        "; PERIOD_CURRENT = slot disabled.",
    ]
    for i, period in enumerate(_signal_periods, start=1):
        lines.append(f"SignalTF{i}={_TF_NAME_TO_INT.get(period, 0)}")

    # Discriminator for bidirectional
    if bidir_mode=="BIDIRECTIONAL" and discriminator:
        dc=discriminator
        # Discrim_Dir encoding:
        #   +1 → col ABOVE thresh → LONG  (dc['dir']=='above')
        #   -1 → col ABOVE thresh → SHORT (dc['dir']=='below', i.e. below→LONG)
        # The EA's ResolveDirection() uses exactly these values; 0 is NOT valid.
        discrim_dir_val = 1 if dc['dir']=='above' else -1
        discrim_col_idx = COL_INDEX.get(dc['col'], 0)
        if dc['col'] not in COL_INDEX:
            print(f"  [set_file] WARNING: discriminator col '{dc['col']}' not in COL_INDEX, "
                  f"defaulting to 0 (rsi14)")
        lines += [
            "",
            f"; DIRECTION DISCRIMINATOR (accuracy={dc['accuracy']}%)",
            f"; LONG  when {dc['col']} (col {discrim_col_idx}) "
            f"{'above' if dc['dir']=='above' else 'below'} {dc['thresh']}",
            f"; SHORT when {dc['col']} (col {discrim_col_idx}) "
            f"{'below' if dc['dir']=='above' else 'above'} {dc['thresh']}",
            f"Discrim_Col={discrim_col_idx}",
            f"Discrim_Thresh={dc['thresh']:.6f}",
            f"; Discrim_Dir: +1=above->LONG  -1=above->SHORT(=below->LONG)",
            f"Discrim_Dir={discrim_dir_val}",
        ]

    # SL and TP as % of price
    lines += [
        "",
        "; SL/TP as FRACTION of entry price (multiply by entry to get price distance)",
        "; Example: SL_Pct=0.005 on a $2000 gold entry = $10 stop distance",
        "; Your EA must implement: sl_price = entry * (1 - SL_Pct) for LONG",
        ";                         sl_price = entry * (1 + SL_Pct) for SHORT",
        f"SL_Pct={sl_pct:.6f}",
        f"TP_Pct={tp_pct:.6f}",
    ]

    # Position sizing and risk
    # CooldownBars and MaxHoldBars are emitted from the SAME constants the
    # backtest simulator used (COOLDOWN_BARS / MAX_HOLD_BARS) so the live EA
    # spaces and times-out trades exactly like the discovery run that produced
    # these metrics. Hardcoding different values here silently de-syncs the EA
    # from the .set's reported results.
    lines += [
        "",
        "; Position sizing",
        "Lots=0.10",
        f"CooldownBars={int(COOLDOWN_BARS)}",
        "BreakevenAtR=0.0",
        "UseTrailing=false",
        "TrailingStart=1.0",
        "TrailingStep=0.5",
        "; Force-close a position after this many bars if neither SL nor TP hit",
        "; (matches the simulator's MAX_HOLD_BARS). 0 = hold until SL/TP only.",
        f"MaxHoldBars={int(MAX_HOLD_BARS)}",
        "; Trading costs in R (risk multiples), matching the simulator's",
        "; COMMISSION_R / SWAP_R_PER_BAR. Commission = round-turn per trade;",
        "; Swap = per bar held. 0.0 = no cost charged.",
        f"Commission_R={float(COMMISSION_R):.6f}",
        f"Swap_R_PerBar={float(SWAP_R_PER_BAR):.6f}",
    ]

    # Session filter — derive from session condition in rule if present
    lines += ["", "; Session filter (true=trade that session)"]
    sess_lo, sess_hi = rule.get("session", (-1, 99))
    for sess_id, sess_name in [(0,"Asian"),(1,"London"),(2,"NY"),(3,"Overlap"),(4,"Off")]:
        active = "true" if sess_lo <= sess_id <= sess_hi else "false"
        lines.append(f"Trade{sess_name}={active}")

    # Condition parameters — only write conditions that are actually active in the rule.
    # Bounds for integer-valued features are snapped outward to the nearest integer
    # so the EA's range check never silently excludes a valid discrete value.
    active_cond_lines = []
    for col, (ea_lo, ea_hi) in COL_TO_EA.items():
        if col == "session": continue  # handled above via TradeXxx booleans
        if col in rule:
            lo_v, hi_v = rule[col]
            lo_v, hi_v = _snap_bounds(col, lo_v, hi_v)
            lbl = COND_LABELS.get(col, col)
            active_cond_lines.append(f"; {lbl}")
            active_cond_lines.append(f"{ea_lo}={lo_v:.6f}")
            active_cond_lines.append(f"{ea_hi}={hi_v:.6f}")

    # MQL5 implementation warnings for features that require custom EA code
    # (no MT5 built-in or non-trivial logic).  Emit per active rule condition.
    MQL5_WARNINGS = {
        "poc_dist": (
            "; !! MQL5 WARNING: poc_dist has NO MT5 built-in equivalent.\n"
            ";    Your EA must implement a custom 100-bar, 20-bin volume-profile\n"
            ";    histogram to compute the Point-of-Control distance.  Without\n"
            ";    that exact logic this condition will silently misfire.\n"
            ";    Recommended: exclude poc_dist from GENE_COLS, or implement\n"
            ";    GetPocDist() in your EA matching Python's compute_price_distributions."
        ),
        "prev_sess_bias": (
            "; !! MQL5 WARNING: prev_sess_bias requires tracking the open and close\n"
            ";    of the PREVIOUS session boundary.  MT5 has no built-in for this.\n"
            ";    Your EA must record session start/end prices and compute\n"
            ";    bias = sign(prev_sess_close - prev_sess_open) each new session.\n"
            ";    The session boundaries must match MT5_SERVER_UTC_OFFSET in discovery."
        ),
        "rolling_sharpe": (
            "; !! MQL5 WARNING: rolling_sharpe requires a 20-bar rolling mean and\n"
            ";    std-dev of bar returns.  Your EA must maintain a circular buffer\n"
            ";    of 20 bar returns and compute mean/std on each new bar.\n"
            ";    Alternatively, iStdDev on close prices is a reasonable proxy."
        ),
    }
    warn_lines = []
    for col in rule:
        if col in MQL5_WARNINGS:
            warn_lines.append("")
            warn_lines.append(MQL5_WARNINGS[col])
    if warn_lines:
        lines += ["", "; === MQL5 IMPLEMENTATION WARNINGS ==="] + warn_lines

    if active_cond_lines:
        lines += ["", "; Entry conditions (only active conditions are listed)"]
        lines += active_cond_lines
    else:
        lines += ["", "; Entry conditions: none (raw cluster, no filter)"]

    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────# ── Module-level cache populated once per process by _prepare_shared_data() ──
_SHARED_DATA: dict = {}   # keys: df, df_train, df_test, X_tr, idx_tr, X_te, idx_te
                           #       trading_days, train_days, test_days


def _prepare_shared_data(nw: int) -> None:
    """
    Load raw data, compute all features, apply warmup trim, and encode windows.
    Called once before the multi-seed loop so every seed reuses identical arrays.
    Results stored in module-level _SHARED_DATA dict.
    """
    global _SHARED_DATA
    if _SHARED_DATA:
        print("  [cache] Reusing shared data from first seed — skipping Load/Encode.")
        return

    print(f"\n[cache] Load & Encode (once for all seeds)")

    # ── Load ──────────────────────────────────────────────────────────────────
    df = load_raw_data()
    trading_days = max(1, (df.index[-1] - df.index[0]).days)
    split_bar = int(len(df) * TRAIN_RATIO)
    df_train = df.iloc[:split_bar]; df_test = df.iloc[split_bar:]
    train_days = max(1, (df_train.index[-1] - df_train.index[0]).days)
    test_days  = max(1, (df_test.index[-1]  - df_test.index[0]).days)
    print(f"  {len(df):,} bars | {trading_days} days")
    print(f"  Train: {len(df_train):,} bars ({train_days}d) "
          f"{df_train.index[0].date()} -> {df_train.index[-1].date()}")
    print(f"  Test:  {len(df_test):,} bars ({test_days}d) "
          f"{df_test.index[0].date()} -> {df_test.index[-1].date()}")

    # ── Extended + v5 features (per split — no look-ahead) ───────────────────
    print("  Computing extended features on train set ...")
    df_train = add_extended_features(detect_regimes(df_train.copy()))
    print("  Computing v5 features on train set ...")
    df_train = add_v5_features(df_train)
    print("  Computing extended features on test set ...")
    df_test = add_extended_features(detect_regimes(df_test.copy()))
    print("  Computing v5 features on test set ...")
    df_test = add_v5_features(df_test)
    df_train = df_train.fillna(0); df_test = df_test.fillna(0)

    # ── Warmup trim ───────────────────────────────────────────────────────────
    if INDICATOR_WARMUP_BARS > 0:
        df_train = df_train.iloc[INDICATOR_WARMUP_BARS:].copy()
        df_test  = df_test.iloc[INDICATOR_WARMUP_BARS:].copy()
        print(f"  Warmup trim: discarded first {INDICATOR_WARMUP_BARS} bars from each split")

    # ── Encode ────────────────────────────────────────────────────────────────
    X_tr, idx_tr = build_features_parallel(df_train, WINDOW_SIZE, nw)
    X_te, idx_te = build_features_parallel(df_test,  WINDOW_SIZE, nw)

    _SHARED_DATA.update(dict(
        df=df, df_train=df_train, df_test=df_test,
        X_tr=X_tr, idx_tr=idx_tr,
        X_te=X_te, idx_te=idx_te,
        trading_days=trading_days,
        train_days=train_days,
        test_days=test_days,
    ))
    print("[cache] Load & Encode complete — cached for remaining seeds.\n")


def _run_config_snapshot():
    """JSON-safe snapshot of the knobs that shape a run's results. Consumed by
    the results_seed{seed}.json artifact and the optional AI reviewer."""
    names = [
        "TRAIN_RATIO", "FORWARD_BARS", "MAX_HOLD_BARS", "COOLDOWN_BARS",
        "SPREAD_PTS", "COMMISSION_R", "SWAP_R_PER_BAR",
        "SL_PCT_QUANTILE", "TP_PCT_QUANTILE", "MIN_DIST_RR",
        "GENETIC_GENERATIONS", "GENETIC_POPULATION",
        "GENE_SCORE_BOX_ONLY", "GATE_BOX_ONLY", "MAX_MATCH_FRAC",
        "ENABLE_TARGET_SCORING", "TARGET_WR_PCT", "TARGET_PF", "TARGET_RR",
        "TARGET_STABILITY", "TARGET_TRADES_PER_DAY", "FILTER_EDGE_K",
        "MIN_FREQ_PER_DAY", "MAX_DRAWDOWN_R", "MAX_CONSEC_LOSSES",
        "MIN_TIME_CONSISTENCY", "MIN_TEST_TRADES_PER_DAY", "MAX_SOFT_FAILS",
        "CLUSTERING_METHOD", "MULTI_SEED_COUNT", "FORCE_DIRECTION",
        "USE_TRIPLE_BARRIER", "USE_BETA_NEUTRAL_LABELS",
    ]
    g = globals()
    snap = {}
    for nm in names:
        v = g.get(nm)
        if isinstance(v, (set, frozenset)):
            v = sorted(v)
        snap[nm] = v
    snap["HARD_FILTERS"] = sorted(g.get("HARD_FILTERS", set()))
    snap["wr_floor"] = round(_wr_floor(), 3)
    snap["pf_floor"] = round(_pf_floor(), 3)
    return snap


def _results_summary_entry(r):
    """JSON-safe scalar subset of one final_results pattern dict (no DataFrames)."""
    keep = {}
    for k in ("pattern_id", "cluster", "direction", "bidir_mode", "marginal",
              "soft_fail", "win_rate_", "wilson_wr", "profit_factor",
              "composite_score", "total_trades", "per_day", "max_drawdown_r",
              "max_consec_losses", "signal_retention",
              "test_wr", "test_pf", "test_trades", "test_score",
              "ea_test_wr", "ea_test_pf", "ea_test_trades", "box_inflation",
              "sl_pct", "tp_pct", "implied_rr", "consistency",
              "overall_wr", "recent_wr", "degrading", "genetic_score",
              "seed", "csv_path"):
        if k in r:
            keep[k] = r[k]
    rule = r.get("genetic_rule") or {}
    keep["genetic_rule"] = {c: [float(lo), float(hi)] for c, (lo, hi) in rule.items()}
    d = r.get("discriminator")
    if d:
        keep["discriminator"] = {k2: v for k2, v in d.items() if k2 != "tree_text"}
    return keep


def run_mc_on_top_patterns(results, out_dir, n=None):
    """Run FTMO MC on top-N non-marginal passers using TEST-split trades only."""
    import json as _json
    if n is None:
        n = RUN_MC_ON_TOP_N
    try:
        from discovery_to_mc import load_pattern_csv
        from mc_funded_test import (run_mc_phase1, run_mc_phase2, run_mc_funded,
                                     failure_mode_breakdown, time_to_pass_distribution)
    except ImportError as e:
        print(f"[MC] skipped — import failed: {e}")
        return []

    passers = [r for r in results if r.get("passed") and not r.get("marginal")]
    # Pick MC candidates by EA-faithful OOS quality (what MT5 reproduces),
    # not the TRAIN composite score.
    passers.sort(key=_oos_rank_key, reverse=True)
    top = passers[:n]
    if not top:
        print("[MC] no passing non-marginal patterns to evaluate")
        return []

    summary_rows = []
    out_dir = Path(out_dir)
    for r in top:
        csv_path = r.get("csv_path") or r.get("trades_csv")
        if not csv_path or not Path(csv_path).exists():
            print(f"[MC] skip {r.get('pattern_id','?')}: no trades CSV")
            continue
        try:
            # Prefer the EA-faithful (box-only) OOS trades — the stream MT5
            # will actually produce — falling back to the cluster-gated test
            # split for CSVs written before the test_ea split existed.
            daily = load_pattern_csv(csv_path, split_filter="test_ea")
            if len(daily) < 5:
                daily = load_pattern_csv(csv_path, split_filter="test")
        except Exception as e:
            print(f"[MC] skip {r.get('pattern_id','?')}: load failed: {e}")
            continue
        if len(daily) < 5:
            print(f"[MC] skip {r.get('pattern_id','?')}: only {len(daily)} test days")
            continue

        try:
            p1  = run_mc_phase1(daily, balance=MC_BALANCE, n_sims=MC_N_SIMS,
                                max_days=MC_MAX_DAYS, seed=RANDOM_SEED)
            p2  = run_mc_phase2(daily, balance=MC_BALANCE, n_sims=MC_N_SIMS,
                                max_days=MC_MAX_DAYS, seed=RANDOM_SEED)
            fd  = run_mc_funded(daily, balance=MC_BALANCE, n_sims=MC_N_SIMS,
                                seed=RANDOM_SEED)
            fm  = failure_mode_breakdown(daily, balance=MC_BALANCE,
                                         n_sims=MC_N_SIMS, seed=RANDOM_SEED)
            ttp = time_to_pass_distribution(daily, balance=MC_BALANCE,
                                            n_sims=MC_N_SIMS, seed=RANDOM_SEED)
        except Exception as e:
            print(f"[MC] failed for {r.get('pattern_id','?')}: {e}")
            continue

        pat_id = r.get("pattern_id") or r.get("name") or f"pat_{len(summary_rows)}"
        out_json = out_dir / f"mc_{pat_id}.json"
        out_json.write_text(_json.dumps({
            "pattern_id": pat_id, "score": r.get("score"),
            "phase1": p1, "phase2": p2, "funded": fd,
            "failure_mode": fm, "time_to_pass": ttp,
        }, default=str, indent=2), encoding="utf-8")

        r["mc"] = {"phase1": p1, "phase2": p2, "funded": fd,
                   "failure_mode": fm, "time_to_pass": ttp,
                   "json_path": str(out_json)}
        summary_rows.append({
            "pattern_id":         pat_id,
            "score":              r.get("score"),
            "p1_pass_rate":       p1.get("pass_rate"),
            "p1_p50_days":        (p1.get("days_p50") or p1.get("avg_days_to_pass")),
            "p2_pass_rate":       p2.get("pass_rate"),
            "combined_pass_rate": (p1.get("pass_rate", 0) or 0) * (p2.get("pass_rate", 0) or 0),
            "funded_blowup_rate": fd.get("blowup_rate"),
        })

    if summary_rows:
        csv_out = out_dir / "mc_summary_top_patterns.csv"
        pd.DataFrame(summary_rows).to_csv(csv_out, index=False)
        print(f"[MC] wrote MC summary for {len(summary_rows)} patterns -> {csv_out}")
    return top


def main():
    global RANDOM_SEED
    # RESEARCH: the FORWARD_BARS_SWEEP block (below) may rebind FORWARD_BARS to
    # the sweep winner. Declared here so the assignment is valid even though
    # FORWARD_BARS is also READ earlier in this function (e.g. the LightGBM path).
    global FORWARD_BARS
    t_total=time.time()
    OUT=Path(OUTPUT_FOLDER)/f"seed_{RANDOM_SEED}"
    OUT.mkdir(parents=True,exist_ok=True)
    np.random.seed(RANDOM_SEED); random.seed(RANDOM_SEED)
    nw=_n_workers(); total_cpu=os.cpu_count() or 4

    _STAGES=["Cluster","Shape Match",
             "Price Distributions","Bidirectional Analysis",
             "Initial Backtest","Genetic Pass 1",
             "Post-P1 Backtest","Select Top 25%",
             "Genetic Pass 2","Post-P2 Backtest",
             "Validate (test)","Quality Analysis","Export"]
    _sn=[0]
    def _stage(name):
        _sn[0]+=1; print(f"\n[{_sn[0]}/{len(_STAGES)}] {name}", flush=True)

    print(f"\n{'='*65}")
    print(f"  PATTERN DISCOVERY ENGINE  v6")
    print(f"  seed={RANDOM_SEED} | CPU: {nw}/{total_cpu} ({CORES_RESERVED} reserved)")
    print(f"  Train/Test: {int(TRAIN_RATIO*100)}%/{int((1-TRAIN_RATIO)*100)}%")
    print(f"  Spread={SPREAD_PTS}pts | Bidirectional | Multi-algo clustering")
    print(f"  Scoring: Wilson WR + additive weighted")
    print(f"{'='*65}\n")

    # ── Unpack shared (pre-computed) data ─────────────────────────────────────
    sd          = _SHARED_DATA
    df          = sd["df"]
    df_train    = sd["df_train"];  df_test     = sd["df_test"]
    X_tr        = sd["X_tr"];      idx_tr      = sd["idx_tr"]
    X_te        = sd["X_te"];      idx_te      = sd["idx_te"]
    trading_days= sd["trading_days"]
    train_days  = sd["train_days"]; test_days  = sd["test_days"]

    print(f"  Train: {len(df_train):,} bars ({train_days}d) "
          f"{df_train.index[0].date()} -> {df_train.index[-1].date()}")
    print(f"  Test:  {len(df_test):,} bars ({test_days}d) "
          f"{df_test.index[0].date()} -> {df_test.index[-1].date()}")

    # ── Cluster ───────────────────────────────────────────────────────────────
    _stage("Cluster")
    # v1.3: optional LightGBM tree-leaf clustering.  Replaces statistical
    # KMeans with profit-aware partitioning (each cluster = a leaf rule).
    _clustering_method = str(globals().get("CLUSTERING_METHOD", "kmeans")).lower()
    _lgbm_model = None
    _lgbm_train_paths: dict = {}
    _leaf_rules: dict = {}  # v1.4: per-cluster warm-start rule from LightGBM leaf path
    if _clustering_method == "lightgbm":
        try:
            from tree_candidates import cluster_by_lightgbm_leaves
            _lgbm_n_est = int(globals().get("LIGHTGBM_N_ESTIMATORS", 3))
            _lgbm_n_leaves = int(globals().get("LIGHTGBM_NUM_LEAVES", 32))
            labels_tr, n_cl, _lgbm_model = cluster_by_lightgbm_leaves(
                X_tr, idx_tr, df_train,
                forward_bars=FORWARD_BARS,
                num_leaves=_lgbm_n_leaves,
                n_estimators=_lgbm_n_est,
                min_samples_leaf=int(globals().get("LIGHTGBM_MIN_SAMPLES_LEAF", 30)),
                random_seed=RANDOM_SEED,
                learning_rate=float(globals().get("LIGHTGBM_LEARNING_RATE", 0.05)),
            )
            if _lgbm_model is None:
                print("  LightGBM: not enough labelled bars for a tree model "
                      "— falling back to KMeans.")
                _clustering_method = "kmeans"
            else:
                # Build path->id map for test-set assignment below.
                leaf_idx_tr = _lgbm_model.predict(X_tr, pred_leaf=True)
                if leaf_idx_tr.ndim == 1:
                    leaf_idx_tr = leaf_idx_tr.reshape(-1, 1)
                for j, row in enumerate(leaf_idx_tr):
                    p = tuple(int(x) for x in row)
                    if p not in _lgbm_train_paths:
                        _lgbm_train_paths[p] = int(labels_tr[j])

                # v1.4: extract leaf-path rules for warm-start.  Only meaningful
                # when n_estimators=1 (single tree → leaf_index == cluster id).
                if _lgbm_n_est == 1:
                    try:
                        from tree_candidates import extract_leaf_rules
                        enc_names = [f"enc_{i}" for i in range(X_tr.shape[1])]
                        per_leaf = extract_leaf_rules(_lgbm_model, enc_names)
                        for path_tuple, cluster_id in _lgbm_train_paths.items():
                            leaf_idx = path_tuple[0] if path_tuple else None
                            if leaf_idx is not None and leaf_idx in per_leaf:
                                _leaf_rules[cluster_id] = per_leaf[leaf_idx]
                        print(f"  Extracted {len(_leaf_rules)} leaf-path rules "
                              f"for optimizer warm-start.")
                    except Exception as ex:
                        print(f"  LightGBM leaf-rule extract skipped ({ex})")

                print(f"  Clustering method: lightgbm "
                      f"({_lgbm_n_est} trees × {_lgbm_n_leaves} leaves, "
                      f"{n_cl} leaf-clusters)")
        except ImportError as ex:
            import sys as _sys
            print(f"  LightGBM path import FAILED: {ex!r}")
            print(f"    python = {_sys.executable}")
            print(f"    (If lightgbm IS installed, you're running a different Python "
                  f"than the embedded one, or tree_candidates isn't importable here.)")
            print("  Falling back to KMeans.")
            _clustering_method = "kmeans"
        except Exception as ex:
            import traceback
            print(f"  LightGBM clustering failed ({ex}) — falling back to KMeans")
            traceback.print_exc()
            _clustering_method = "kmeans"

    if _clustering_method != "lightgbm":
        if REGIME_MODE:
            try:
                labels_tr,n_cl=cluster_multi_algo(
                    X_tr,idx_tr,df_train,N_CLUSTERS//5,RANDOM_SEED)
            except Exception as ex:
                print(f"  Multi-algo clustering failed ({ex}), falling back to K-Means")
                labels_tr,n_cl=cluster_per_regime_kmeans(
                    X_tr,idx_tr,df_train,N_CLUSTERS//5,RANDOM_SEED)
        else:
            sc=StandardScaler(); Xs=sc.fit_transform(X_tr)
            km=MiniBatchKMeans(n_clusters=N_CLUSTERS,random_state=RANDOM_SEED,
                               n_init=10,max_iter=300,batch_size=min(10000,len(X_tr)))
            labels_tr=km.fit_predict(Xs); n_cl=N_CLUSTERS

    print(f"  Total clusters: {n_cl}")
    for cid in range(n_cl):
        cnt=int((labels_tr==cid).sum())
        if cnt>0: print(f"  Cluster {cid:>2d}: {cnt:>6,} ({cnt/train_days:.1f}/day)")

    # ── Shape matching ────────────────────────────────────────────────────────
    _stage("Shape Match")
    if USE_SHAPE_MATCHING:
        labels_tr=refine_shape_matching(
            df_train,idx_tr,labels_tr,WINDOW_SIZE,SHAPE_MATCH_THRESHOLD,nw)
    else:
        print("  Skipped.")

    # Assign test bars to clusters.  LightGBM path uses the trained model's
    # leaf-path prediction; KMeans path uses centroid-nearest-neighbour.
    print("  Assigning test bars ...")
    if _clustering_method == "lightgbm" and _lgbm_model is not None:
        from tree_candidates import assign_test_bars_to_leaves
        labels_te = assign_test_bars_to_leaves(_lgbm_model, X_te, _lgbm_train_paths)
    else:
        sc_tr=StandardScaler(); X_tr_scaled=sc_tr.fit_transform(X_tr)
        # Compute centroid per cluster in scaled feature space
        feat_centroids_tr={}
        for cid in range(n_cl):
            mask=labels_tr==cid
            if mask.sum()>0:
                feat_centroids_tr[cid]=X_tr_scaled[mask].mean(axis=0)
        # Scale test features with TRAINING scaler (not re-fitted)
        X_te_scaled=sc_tr.transform(X_te)
        # Assign each test bar to nearest centroid — fully vectorised (v5 speedup)
        labels_te=np.zeros(len(idx_te),dtype=np.int32)
        centroid_matrix=np.vstack([feat_centroids_tr[c]
                                    for c in range(n_cl)
                                    if c in feat_centroids_tr])
        valid_cids=[c for c in range(n_cl) if c in feat_centroids_tr]
        # cdist is much faster than a Python loop for large test sets
        dists_all = cdist(X_te_scaled, centroid_matrix, metric="euclidean")
        labels_te = np.array(valid_cids, dtype=np.int32)[np.argmin(dists_all, axis=1)]

    # ── RESEARCH: forward-horizon sweep (gated on FORWARD_BARS_SWEEP) ─────────
    # When the sweep list is non-empty, evaluate each candidate horizon on the
    # TRAIN clusters and keep the one with the strongest sustained-move edge.
    # The winner is rebound onto the FORWARD_BARS module global so EVERY
    # downstream stage (price-dist, bidirectional, backtests, validation) uses
    # the chosen horizon with no further plumbing. Empty list (default) skips
    # this entirely and FORWARD_BARS is left exactly as configured.
    # (FORWARD_BARS is declared global at the top of main().)
    _sweep=list(globals().get("FORWARD_BARS_SWEEP", []) or [])
    if _sweep:
        _best_fwd,_sw_table=select_forward_horizon(
            df_train, idx_tr, labels_tr, n_cl, _sweep)
        if _sw_table is not None:
            print("  [research] forward-horizon sweep "
                  "(fwd: weighted sustained%):")
            for _fb,_sc in _sw_table:
                _mark=" <= chosen" if _fb==_best_fwd else ""
                print(f"      fwd={_fb:>4}: {_sc}{_mark}")
            if _best_fwd!=FORWARD_BARS:
                print(f"  [research] FORWARD_BARS {FORWARD_BARS} -> {_best_fwd} "
                      f"(sweep winner).")
            FORWARD_BARS=_best_fwd

    # ── Price distributions ───────────────────────────────────────────────────
    _stage("Price Distributions")
    price_dists_tr=compute_price_distributions(
        df_train,idx_tr,labels_tr,n_cl,FORWARD_BARS)
    for cid in range(n_cl):
        d=price_dists_tr.get(cid)
        if d: print(f"  C{cid:>2d}: SL={d['sl_pct_label']} TP={d['tp_pct_label']} "
                    f"RR={d['implied_rr']} sustained={d['sustained_pct']}%")

    # ── Bidirectional analysis ────────────────────────────────────────────────
    _stage("Bidirectional Analysis")
    bidir_info=analyze_bidirectionality(
        df_train,idx_tr,labels_tr,n_cl,price_dists_tr,FORWARD_BARS)
    bidir_count=sum(1 for v in bidir_info.values() if v.get("mode")=="BIDIRECTIONAL")
    long_count=sum(1 for v in bidir_info.values() if v.get("mode")=="LONG_ONLY")
    short_count=sum(1 for v in bidir_info.values() if v.get("mode")=="SHORT_ONLY")
    print(f"  LONG_ONLY: {long_count}  SHORT_ONLY: {short_count}  "
          f"BIDIRECTIONAL: {bidir_count}")
    for cid,info in bidir_info.items():
        mode=info.get("mode","?")
        lw=info.get("long_wr",0); sw=info.get("short_wr",0)
        lt=info.get("long_trades",0); st=info.get("short_trades",0)
        d_str=""
        if info.get("discrim"):
            dc=info["discrim"]
            d_str=f" | discrim={dc['col']} {dc['dir']} {dc['thresh']} acc={dc['accuracy']}%"
        print(f"  C{cid:>2d}: {mode:<15} L:{lw}%({lt}t) S:{sw}%({st}t){d_str}")

    # ── Initial backtest ──────────────────────────────────────────────────────
    _stage("Initial Backtest")
    bt_initial=backtest_all_directions(
        df_train,idx_tr,labels_tr,n_cl,bidir_info,
        price_dists_tr,FORWARD_BARS,nw,train_days)
    print(f"  {len(bt_initial)} direction-specific backtests done")
    for key,r in sorted(bt_initial.items(),
                        key=lambda x:x[1].get("composite_score",0),reverse=True)[:10]:
        print(f"  C{key[0]:>2d} {key[1]:>5}: WR={r['win_rate_']}% "
              f"Wilson={r['wilson_wr']}% PF={r['profit_factor']} "
              f"score={r['composite_score']}")

    # ── Candidate selection ───────────────────────────────────────────────────
    min_trades_p1=int(train_days*MIN_TRADES_PER_DAY_PASS2*0.5)
    candidates=[(cid,dirn) for (cid,dirn),r in bt_initial.items()
                if r.get("total_trades",0)>=min_trades_p1
                and r.get("win_rate_",0)>=_wr_floor()*0.85]
    print(f"  {len(candidates)} candidates for genetic pass 1 "
          f"(min {min_trades_p1} trades)")

    # ── Genetic pass 1 ────────────────────────────────────────────────────────
    _stage("Genetic Pass 1")
    if candidates:
        genetic_p1,sltp_p1=genetic_refine_parallel(
            df_train,idx_tr,labels_tr,candidates,
            price_dists_tr,bidir_info,FORWARD_BARS,
            GENETIC_GENERATIONS,GENETIC_POPULATION,
            GENETIC_MUTATE_RATE,train_days,RANDOM_SEED,nw,
            leaf_rules=_leaf_rules)
    else:
        genetic_p1={}; sltp_p1={}; print("  No candidates.")

    # ── Post-P1 backtest ──────────────────────────────────────────────────────
    _stage("Post-P1 Backtest")
    bt_p1=backtest_refined(
        df_train,idx_tr,labels_tr,genetic_p1,
        price_dists_tr,FORWARD_BARS,nw,train_days,sltp_overrides=sltp_p1)
    print(f"  Pass 1 refined results:")
    for key,r in sorted(bt_p1.items(),
                        key=lambda x:x[1].get("composite_score",0),reverse=True):
        print(f"  C{key[0]:>2d} {key[1]:>5}: WR={r['win_rate_']}% "
              f"Wilson={r['wilson_wr']}% PF={r['profit_factor']} "
              f"signals={r['filtered_signals']}/{r['original_signals']} "
              f"score={r['composite_score']}")

    # ── Select top 25% for pass 2 ─────────────────────────────────────────────
    _stage("Select Top 25%")
    min_trades_p2=int(train_days*MIN_TRADES_PER_DAY_PASS2)
    tradeable_p1=[(k,r) for k,r in bt_p1.items()
                  if r.get("total_trades",0)>=min_trades_p2]
    n_top=max(1,int(len(tradeable_p1)*TOP_FRACTION_PASS2))
    top_keys=[k for k,r in sorted(tradeable_p1,
                                   key=lambda x:x[1].get("composite_score",0),
                                   reverse=True)[:n_top]]
    if not top_keys:
        print(f"  No rules with >={min_trades_p2} trades — selecting by WR×PF instead.")
        # fallback: rank by win_rate × profit_factor (works even when score=0)
        def _rank_key(k):
            r=bt_p1[k]
            wr=r.get("win_rate_",0)/100.0
            pf=min(r.get("profit_factor",0) if r.get("profit_factor",0)!=float("inf") else 0, 5.0)
            trades=r.get("total_trades",0)
            # require at least 20 trades to rank
            return wr*pf if trades>=20 else 0.0
        top_keys=sorted(bt_p1.keys(),key=_rank_key,reverse=True)[:max(1,len(bt_p1)//4)]
        print(f"  Selected by WR×PF: {top_keys}")
    print(f"  Top {len(top_keys)} selected: {top_keys}")

    # ── Genetic pass 2 ────────────────────────────────────────────────────────
    _stage("Genetic Pass 2")
    # In box-only (Mode A) pass 1 already optimizes the EA-faithful objective, so
    # pass 2 (re-optimizing the SAME objective from a narrowed seed) is redundant
    # AND expensive — skip it. Pass 2 only earns its keep in cluster-gated mode.
    if top_keys and not globals().get("GENE_SCORE_BOX_ONLY", False):
        genetic_p2=genetic_pass2_parallel(
            df_train,idx_tr,labels_tr,top_keys,genetic_p1,
            price_dists_tr,FORWARD_BARS,
            PASS2_GENERATIONS,PASS2_POPULATION,
            PASS2_MUTATE_RATE,train_days,RANDOM_SEED,nw,
            sltp_overrides=sltp_p1)
        # merge: pass2 replaces pass1 if better
        genetic_final=dict(genetic_p1)
        for key,(rule,score) in genetic_p2.items():
            p1s=genetic_p1.get(key,({},0.0))[1]
            if score>p1s+0.001:  # strict improvement only (fix 2.1b)
                genetic_final[key]=(rule,score)
                print(f"  Pass 2 improved {key}: {p1s:.4f} -> {score:.4f}")
            else:
                print(f"  Pass 2 no improvement {key} (kept p1: {p1s:.4f})")
    else:
        genetic_final=genetic_p1
        if globals().get("GENE_SCORE_BOX_ONLY", False):
            print("  Skipped (box-only mode — pass 1 already optimizes box-only).")
        else:
            print("  Skipped.")

    # ── Post-P2 backtest ──────────────────────────────────────────────────────
    _stage("Post-P2 Backtest")
    # box_only=True (honest discovery): the gate + reported TRAIN metrics now
    # reflect what the EA fires (box-only), matching how the GA scored. Gating on
    # in-sample box-only avoids test-set leakage; box-only TEST (bt_test_ea) is the
    # OOS check reported alongside.
    bt_final=backtest_refined(
        df_train,idx_tr,labels_tr,genetic_final,
        price_dists_tr,FORWARD_BARS,nw,train_days,sltp_overrides=sltp_p1,
        box_only=globals().get("GATE_BOX_ONLY", False))
    print(f"  Final refined results:")
    for key,r in sorted(bt_final.items(),
                        key=lambda x:x[1].get("composite_score",0),reverse=True):
        print(f"  C{key[0]:>2d} {key[1]:>5}: WR={r['win_rate_']}% "
              f"Wilson={r['wilson_wr']}% PF={r['profit_factor']} "
              f"score={r['composite_score']}")

    # ── Validate on test ──────────────────────────────────────────────────────
    _stage("Validate (test)")
    # OOS-bias fix (E): do NOT re-fit SL/TP on test data. Reuse the TRAIN-fitted
    # exit levels (price_dists_tr) as the quantile baseline so the OOS stage is a
    # true hold-out (sltp_p1 overrides are already train-derived).
    bt_test=backtest_refined(
        df_test,idx_te,labels_te,
        {k:v for k,v in genetic_final.items() if isinstance(k[0],int)},
        price_dists_tr,FORWARD_BARS,nw,test_days,sltp_overrides=sltp_p1)

    # ── EA-faithful OOS backtest (cause C): score each box on the WHOLE test
    # split with NO cluster/shape gate — exactly how the exported EA fires in
    # MT5. Report-only for now: surfaces the box-inflation gap (live vs gated
    # trade count) so you can see how much the cluster gate was flattering the
    # numbers before deciding whether to gate on it. Does NOT affect pass/fail.
    bt_test_ea=backtest_refined(
        df_test,idx_te,labels_te,
        {k:v for k,v in genetic_final.items() if isinstance(k[0],int)},
        price_dists_tr,FORWARD_BARS,nw,test_days,sltp_overrides=sltp_p1,
        box_only=True)

    # ── Quality analysis ──────────────────────────────────────────────────────
    _stage("Quality Analysis")
    corr_mat=cluster_correlation_matrix(labels_tr,idx_tr,n_cl)
    flagged_corr=set()
    for i in range(n_cl):
        for j in range(i+1,n_cl):
            if corr_mat[i,j]>CORRELATION_THRESHOLD:
                sc_i=max(bt_final.get((i,"LONG"),{}).get("composite_score",0),
                         bt_final.get((i,"SHORT"),{}).get("composite_score",0))
                sc_j=max(bt_final.get((j,"LONG"),{}).get("composite_score",0),
                         bt_final.get((j,"SHORT"),{}).get("composite_score",0))
                drop=j if sc_i>=sc_j else i
                flagged_corr.add(drop)
                print(f"  Clusters {i}&{j} correlated {corr_mat[i,j]:.0%} "
                      f"— dropping C{drop}")

    min_test_trades=int(test_days*MIN_TEST_TRADES_PER_DAY)
    final_results=[]

    for key,(rule,gscore) in genetic_final.items():
        cid,direction=key if isinstance(key,tuple) else (key,"LONG")
        if cid in flagged_corr: continue
        tr=bt_final.get(key,{}); te=bt_test.get(key,{})
        ea=bt_test_ea.get(key,{})   # EA-faithful (box-only) OOS metrics
        # Box-inflation: how many more trades the EA fires (box-only) vs the
        # cluster-gated test count. >1 means MT5 will trade more than discovery.
        _te_n=te.get("total_trades",0); _ea_n=ea.get("total_trades",0)
        box_inflation=round(_ea_n/_te_n,2) if _te_n>0 else (float("inf") if _ea_n>0 else 0.0)
        d_tr=price_dists_tr.get(cid) or {}
        info=bidir_info.get(cid,{})
        # v5 soft filter: tally all fails; marginal = exactly 1 fail
        def _chk(name,val,thresh,mode="min"):
            fails=val<thresh if mode=="min" else val>thresh
            print(f"    [{'FAIL' if fails else 'ok':>4}] {name:<28} {val}  "
                  f"({'<' if mode=='min' else '>'} {thresh})")
            return fails

        print(f"\n  C{cid:>2d} {direction} checking filters:")
        # EA-faithful OOS (report-only) — printed for EVERY pattern, pass or drop,
        # so you can see what MT5 will actually trade even when nothing passes.
        print(f"    [EA-OOS] gated test: WR={te.get('win_rate_',0)}% "
              f"PF={te.get('profit_factor',0)} n={_te_n}  |  "
              f"box-only (EA): WR={ea.get('win_rate_',0)}% "
              f"PF={ea.get('profit_factor',0)} n={_ea_n}  |  inflation×{box_inflation}")
        trd_df=tr.get("trades",pd.DataFrame())
        consistency=time_consistency_score(trd_df)
        min_wr = _wr_floor(); min_pf = _pf_floor()
        checks=[
            ("implied_rr",       d_tr.get("implied_rr",0),     MIN_DIST_RR,         "min"),
            ("win_rate_%",        tr.get("win_rate_",0),         min_wr,              "min"),
            ("wilson_wr",         tr.get("wilson_wr",0),         min_wr*0.9,          "min"),
            ("profit_factor",     tr.get("profit_factor",0),     min_pf,              "min"),
            ("composite_score",   tr.get("composite_score",0),   0.25,                 "min"),
            ("max_drawdown_r",    tr.get("max_drawdown_r",99),   MAX_DRAWDOWN_R,       "max"),
            ("max_consec_losses", tr.get("max_consec_losses",99),MAX_CONSEC_LOSSES,    "max"),
            ("per_day",           tr.get("per_day",0),           MIN_FREQ_PER_DAY,     "min"),
            # test_trades uses the BOX-ONLY (EA) count, not the cluster-gated one:
            # the cluster gate makes the test count near-zero for selective rules,
            # which is an artifact, not what the EA actually trades out-of-sample.
            ("test_trades",       _ea_n,                         min_test_trades,      "min"),
            ("time_consistency",  round(consistency,2),          MIN_TIME_CONSISTENCY, "min"),
        ]
        # Tally per-filter fails AND remember (name, value, threshold) for the
        # marginal case so the UI can show *which* filter softened.
        fail_details = []
        for name, val, thresh, mode in checks:
            if _chk(name, val, thresh, mode):
                fail_details.append({
                    "name":      name,
                    "value":     float(val) if isinstance(val, (int, float)) else val,
                    "threshold": float(thresh) if isinstance(thresh, (int, float)) else thresh,
                    "mode":      mode,  # "min" or "max"
                })
        # Hard/soft gate: any HARD-filter fail drops the pattern; otherwise it
        # passes (MARGINAL if it trips up to MAX_SOFT_FAILS soft proxies).
        hard_fails = [f for f in fail_details if f["name"] in HARD_FILTERS]
        soft_fails = [f for f in fail_details if f["name"] not in HARD_FILTERS]
        soft_fail = soft_fails[0] if soft_fails else None  # back-compat (first softened)
        max_soft = int(globals().get("MAX_SOFT_FAILS", 0))

        def _fmt_fails(fs):
            return ", ".join(
                f"{f['name']}({f['value']}{'<' if f['mode']=='min' else '>'}{f['threshold']})"
                for f in fs)

        if hard_fails:
            print(f"    [DROP] hard filter(s) failed: {_fmt_fails(hard_fails)}")
            continue
        if len(soft_fails) > max_soft:
            print(f"    [DROP] {len(soft_fails)} soft filters failed (max {max_soft}): "
                  f"{_fmt_fails(soft_fails)}")
            continue
        is_marginal = len(soft_fails) > 0
        if is_marginal:
            print(f"    [MARGINAL] {len(soft_fails)} soft filter(s) failed — kept ⚠: "
                  f"{_fmt_fails(soft_fails)}")
        else:
            print(f"    [PASS] All filters passed ✓")

        overall_wr,recent_wr=pattern_degradation(trd_df)
        _pat_id = f"C{cid}_{direction}_seed{RANDOM_SEED}"
        final_results.append({
            **tr,
            "cluster":       cid,
            "direction":     direction,
            "bidir_mode":    info.get("mode","LONG_ONLY"),
            "long_wr":       info.get("long_wr",0),
            "short_wr":      info.get("short_wr",0),
            "discriminator": info.get("discrim"),
            "genetic_rule":  rule,
            "genetic_score": gscore,
            "sl_pct_label":  d_tr.get("sl_pct_label","?"),
            "tp_pct_label":  d_tr.get("tp_pct_label","?"),
            "implied_rr":    d_tr.get("implied_rr",0),
            "sl_pct":        d_tr.get("sl_pct",0.002),
            "tp_pct":        d_tr.get("tp_pct",0.003),
            "consistency":   round(consistency,2),
            "overall_wr":    overall_wr,
            "recent_wr":     recent_wr,
            "degrading":     recent_wr<overall_wr-5.0,
            "test_wr":       te.get("win_rate_",0),
            "test_pf":       te.get("profit_factor",0),
            "test_trades":   te.get("total_trades",0),
            "test_score":    te.get("composite_score",0),
            # EA-faithful (box-only) OOS — what MT5 should actually reproduce.
            "ea_test_wr":     ea.get("win_rate_",0),
            "ea_test_pf":     ea.get("profit_factor",0),
            "ea_test_trades": ea.get("total_trades",0),
            "box_inflation":  box_inflation,
            "marginal":      is_marginal,   # v5: True = passed via soft filter
            "soft_fail":     soft_fail,     # {name,value,threshold,mode} or None
            # MC-chain fields
            "passed":        True,
            "score":         tr.get("composite_score", 0.0),
            "pattern_id":    _pat_id,
            "test_trades_df": te.get("trades", pd.DataFrame()),
            # EA-faithful OOS trade list (box-only) — what MT5 will produce;
            # written to the per-pattern CSV as split="test_ea" and preferred
            # by the MC chain.
            "ea_test_trades_df": ea.get("trades", pd.DataFrame()),
        })

    # OOS-bias fix (D) + EA-coherence: rank by the EA-faithful box-only OOS
    # metrics (what MT5 reproduces), not the cluster-gated test numbers.
    final_results.sort(key=_oos_rank_key, reverse=True)
    print(f"\n  {len(final_results)} patterns passed all filters")

    # ── Export ────────────────────────────────────────────────────────────────
    _stage("Export")
    OUT.mkdir(parents=True, exist_ok=True)
    # Build a human-readable timeframe summary for the report header.
    # Lists every non-empty slot, marks which is primary.
    tf_files_all = [TF1_FILE, TF2_FILE, TF3_FILE, TF4_FILE, TF5_FILE]
    primary_idx = max(1, min(5, int(PRIMARY_TF))) - 1
    tf_lines = []
    for i, fn in enumerate(tf_files_all):
        if not fn:
            continue
        role = "primary, run EA on this" if i == primary_idx else "signal"
        tf_lines.append(f"    TF{i + 1} ({role}): {fn}")

    report_lines=[
        "="*65,
        f"  PATTERN DISCOVERY REPORT v4 | seed={RANDOM_SEED}",
        f"  {df.index[0].date()} -> {df.index[-1].date()}",
        f"  Train: {df_train.index[0].date()} -> {df_train.index[-1].date()} ({train_days}d)",
        f"  Test:  {df_test.index[0].date()} -> {df_test.index[-1].date()} ({test_days}d)",
        f"  Timeframes used:",
        *tf_lines,
        f"  {len(final_results)} qualifying patterns",
        f"  Spread={SPREAD_PTS}pts | Wilson WR | Bidirectional | Multi-algo",
        "="*65,
    ]
    all_trade_dfs=[]

    for r in final_results:
        cid=r["cluster"]; direction=r["direction"]
        desc=describe_cluster(df_train,idx_tr,labels_tr,cid,WINDOW_SIZE)
        rule=r["genetic_rule"]; SEP="-"*65
        degrade=""
        if r["degrading"]:
            degrade=f"  ⚠️  DEGRADING: {r['overall_wr']}% -> recent {r['recent_wr']}%"

        print(f"\n{SEP}")
        print(f"  CLUSTER {cid} [{direction}] [{r['bidir_mode']}]"
              + ("  ⚠ MARGINAL" if r.get("marginal") else ""))
        print(f"  Train: WR={r['win_rate_']}% Wilson={r['wilson_wr']}% "
              f"PF={r['profit_factor']} Score={r['composite_score']} "
              f"{r['per_day']}/day retain={r.get('signal_retention',0)} (report-only)")
        print(f"  Test:  WR={r['test_wr']}% PF={r['test_pf']} "
              f"({r['test_trades']} trades) Score={r['test_score']}")
        print(f"  {desc}")
        if degrade: print(degrade)
        print(f"  Consistency: {r['consistency']:.0%}  "
              f"SL={r['sl_pct_label']} TP={r['tp_pct_label']} "
              f"Implied RR={r['implied_rr']}")

        if r["bidir_mode"]=="BIDIRECTIONAL" and r["discriminator"]:
            dc=r["discriminator"]
            print(f"\n  DIRECTION DISCRIMINATOR (accuracy={dc['accuracy']}%)"
                  f"  [{dc.get('method','threshold')}]:")
            if dc.get("method")=="decision_tree":
                print(f"    (2-condition tree — see tree_text below)")
                print(textwrap.indent(dc.get("tree_text",""), "    "))
            else:
                print(f"    LONG  when: {dc['col']} {'above' if dc['dir']=='above' else 'below'} {dc['thresh']}")
                print(f"    SHORT when: {dc['col']} {'below' if dc['dir']=='above' else 'above'} {dc['thresh']}")

        print(f"\n  GENETIC CONDITIONS [{direction}]:")
        print(rule_to_text(rule,direction))

        tdf=r.get("trades",pd.DataFrame())
        tdf_te=r.get("test_trades_df",pd.DataFrame())
        tdf_ea=r.get("ea_test_trades_df",pd.DataFrame())
        if not tdf.empty or not tdf_te.empty or not tdf_ea.empty:
            fp=str(OUT/f"cluster_{cid}_{direction}_seed{RANDOM_SEED}.csv")
            # Tag each split and ensure required MC columns exist.
            # "test_ea" = EA-faithful box-only OOS trades (preferred by MC).
            parts=[]
            for _sdf, _split in [(tdf,"train"),(tdf_te,"test"),(tdf_ea,"test_ea")]:
                if _sdf.empty:
                    continue
                _sdf=_sdf.copy()
                _sdf["split"]=_split
                # back-fill entry_time/exit_time/pnl_pts if missing (older path)
                if "entry_time" not in _sdf.columns:
                    _sdf["entry_time"]=_sdf.get("time",pd.Series(dtype="object"))
                if "exit_time" not in _sdf.columns:
                    _sdf["exit_time"]=_sdf.get("time",pd.Series(dtype="object"))
                if "pnl_pts" not in _sdf.columns:
                    _risk=(_sdf.get("entry",0)-_sdf.get("sl",0)).abs()
                    _sdf["pnl_pts"]=(_sdf.get("rr",0)*_risk).round(5)
                # ensure direction column
                if "direction" not in _sdf.columns:
                    _sdf["direction"]=direction
                parts.append(_sdf)
            if parts:
                merged=pd.concat(parts,ignore_index=True)
                merged.to_csv(fp,index=False)
                all_trade_dfs.append(merged)
                r["csv_path"]=fp

        # Generate .set file for MetaTrader 5 universal template EA
        pattern_no=final_results.index(r)+1

        # Sanity check: count how many test bars actually satisfy this rule.
        # If 0, the rule is overfit and will never fire in production.
        rule=r["genetic_rule"]
        rule_arrays={col:df_test[col].values for col in rule if col in df_test.columns}
        match_mask=np.ones(len(df_test),dtype=bool)
        for col,(lo_v,hi_v) in rule.items():
            if col in rule_arrays:
                arr=rule_arrays[col]
                match_mask&=(arr>=lo_v)&(arr<=hi_v)
        test_matches=int(match_mask.sum())
        test_match_pct=test_matches/max(len(df_test),1)*100
        print(f"  Rule fires on {test_matches}/{len(df_test)} test bars "
              f"({test_match_pct:.2f}%)")
        if test_matches==0:
            print(f"  ⚠ WARNING: Rule fires on 0 test bars — pattern may be overfit. "
                  f"Consider adjusting GENE_N_COLS_MIN/MAX or FORWARD_BARS.")

        set_path=str(OUT/f"pattern_{pattern_no:02d}_C{cid}_{direction}_seed{RANDOM_SEED}.set")
        generate_set_file(
            pattern_no=pattern_no,
            cid=cid,
            direction=direction,
            rule=r["genetic_rule"],
            sl_pct=r.get("sl_pct",0.002),
            tp_pct=r.get("tp_pct",0.003),
            bidir_mode=r["bidir_mode"],
            discriminator=r["discriminator"],
            r=r,
            out_path=set_path,
        )
        print(f"  .set  -> {set_path}")

        report_lines+=[
            "",SEP,
            f"  CLUSTER {cid} [{direction}] [{r['bidir_mode']}]"
            + ("  ⚠ MARGINAL" if r.get("marginal") else ""),
            f"  Train: WR={r['win_rate_']}% Wilson={r['wilson_wr']}% "
            f"PF={r['profit_factor']} Score={r['composite_score']} {r['per_day']}/day "
            f"retain={r.get('signal_retention',0)} (report-only)",
            f"  Test:  WR={r['test_wr']}% PF={r['test_pf']} "
            f"trades={r['test_trades']} "
            f"({round(r['test_trades']/max(test_days,1),2)}/day) "
            f"Score={r['test_score']}",
            f"  EA-OOS (box-only, what MT5 fires): WR={r.get('ea_test_wr',0)}% "
            f"PF={r.get('ea_test_pf',0)} trades={r.get('ea_test_trades',0)} "
            f"(inflation ×{r.get('box_inflation',0)} vs gated test)",
            f"  {desc}",
        ]
        if degrade: report_lines.append(degrade)
        if r["bidir_mode"]=="BIDIRECTIONAL" and r["discriminator"]:
            dc=r["discriminator"]
            report_lines+=[
                f"  DISCRIMINATOR ({dc['accuracy']}%): "
                f"{dc['col']} {dc['dir']} {dc['thresh']}"
            ]
        report_lines+=["","  GENETIC CONDITIONS:",rule_to_text(rule,direction)]

    if all_trade_dfs:
        mp_=str(OUT/f"all_patterns_seed{RANDOM_SEED}.csv")
        pd.concat(all_trade_dfs).sort_values("time").to_csv(mp_,index=False)
        print(f"\n  Combined CSV -> {mp_}")

    rpt=str(OUT/f"report_seed{RANDOM_SEED}.txt")
    Path(rpt).write_text("\n".join(report_lines),encoding="utf-8")
    print(f"  Report      -> {rpt}")

    # Machine-readable run summary — consumed by the optional AI reviewer and
    # external tooling. Always written (cheap, deterministic, no DataFrames).
    import json as _json_s
    _summary_doc = None
    try:
        _summary_doc = {
            "seed": RANDOM_SEED,
            "run_config": _run_config_snapshot(),
            "patterns": [_results_summary_entry(r) for r in final_results],
        }
        _sj = str(OUT / f"results_seed{RANDOM_SEED}.json")
        Path(_sj).write_text(_json_s.dumps(_summary_doc, indent=1, default=str),
                             encoding="utf-8")
        print(f"  Summary JSON-> {_sj}")
    except Exception as _se:
        print(f"  [summary] JSON skipped: {_se}")

    # Optional AI-in-the-loop review (ADVISORY only — never gates or re-ranks;
    # OFF unless AI_REVIEW_ENABLED / BD_AI_REVIEW=1; failures are non-fatal).
    _ai_on = bool(globals().get("AI_REVIEW_ENABLED", False))
    if not _ai_on:
        try:
            from ai_review import is_enabled as _ai_env_on
            _ai_on = _ai_env_on()
        except Exception:
            _ai_on = False
    if _ai_on and _summary_doc is not None:
        try:
            from ai_review import review_run as _ai_review_run
            _ai_path = _ai_review_run(
                _summary_doc["patterns"], _summary_doc["run_config"],
                OUT, RANDOM_SEED,
                base_url=str(globals().get("AI_REVIEW_BASE_URL", "")) or None,
                model=str(globals().get("AI_REVIEW_MODEL", "")) or None,
                timeout_s=float(globals().get("AI_REVIEW_TIMEOUT_S", 120)))
            if _ai_path:
                print(f"  AI review   -> {_ai_path}")
        except Exception as _ae:
            print(f"  [ai_review] skipped: {_ae}")

    all_bt=[r for r in final_results]
    if all_bt: plot_performance(all_bt,str(OUT/f"performance_seed{RANDOM_SEED}.png"))
    plot_regime_distribution(df,str(OUT/f"regimes_seed{RANDOM_SEED}.png"))

    if RUN_MC_ON_TOP_N > 0:
        try:
            run_mc_on_top_patterns(final_results, OUT)
        except Exception as _mc_e:
            print(f"[MC] chain failed: {_mc_e}")

    print(f"\n{'='*65}")
    print(f"  TOTAL TIME: {_elapsed(t_total)}")
    print(f"  Output: {OUTPUT_FOLDER}")
    print(f"  {len(final_results)} patterns ready")
    print(f"  Change RANDOM_SEED to explore different patterns")
    print(f"{'='*65}\n")
    return final_results


if __name__ == "__main__":
    # Overrides are loaded by _load_app_overrides() at module-import time
    # (defined just below the CONFIG block) so that mp.Pool workers re-importing
    # this module under spawn-mode pick up the same overrides as the parent.
    mp.set_start_method("spawn", force=True)
    _nw = _n_workers()
    # Load, feature-compute, and encode once for all seeds.
    # For a single-seed run this is equivalent to the original flow.
    _prepare_shared_data(_nw)
    if MULTI_SEED_COUNT <= 1:
        main()
    else:
        print(f"\n{'='*65}")
        print(f"  MULTI-SEED BATCH: {MULTI_SEED_COUNT} seeds "
              f"starting from {MULTI_SEED_BASE}")
        print(f"{'='*65}")
        all_seed_results = []
        for _si in range(MULTI_SEED_COUNT):
            RANDOM_SEED = MULTI_SEED_BASE + _si
            np.random.seed(RANDOM_SEED)
            random.seed(RANDOM_SEED)
            print(f"\n\n{'#'*65}")
            print(f"  SEED {_si+1}/{MULTI_SEED_COUNT}  →  {RANDOM_SEED}")
            print(f"{'#'*65}")
            seed_results = main()
            if seed_results:
                for r in seed_results:
                    r["seed"] = RANDOM_SEED
                all_seed_results.extend(seed_results)
        write_combined_report(all_seed_results, OUTPUT_FOLDER)
        print(f"\n{'='*65}")
        print(f"  BATCH COMPLETE: {MULTI_SEED_COUNT} seeds finished.")
        print(f"  Results in subfolders of: {OUTPUT_FOLDER}")
        print(f"{'='*65}\n")