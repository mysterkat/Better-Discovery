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
TF1_FILE      = "xauusd_m5.csv"
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
MULTI_SEED_COUNT = 6
MULTI_SEED_BASE  = RANDOM_SEED   # seeds = BASE, BASE+1, BASE+2, …

WINDOW_SIZE = 5
N_CLUSTERS  = 5   # per algorithm per regime

REGIME_MODE           = False
USE_SHAPE_MATCHING    = True
SHAPE_MATCH_THRESHOLD = 0.75
USE_SOFT_FILTER       = True   # v5: marginal patterns get ⚠ tag instead of drop
USE_EXTRA_FEATURES    = True   # v5: stoch, candle patterns, SD zones, rolling Sharpe

FORWARD_BARS            = 24
MEANINGFUL_MOVE_ATR     = 0.6   # price must move >= this x ATR
MEANINGFUL_SUSTAIN_BARS = 4     # and hold for this many bars
SPREAD_PTS              = 0.30
REALISTIC_ENTRY         = True
MAX_HOLD_BARS           = 32
ALLOWED_SESSIONS        = []
COOLDOWN_BARS           = 2

SL_PCT_QUANTILE = 0.85
TP_PCT_QUANTILE = 0.60
MIN_DIST_RR     = 0.30

# Genetic
GENETIC_GENERATIONS      = 20
GENETIC_POPULATION       = 70
GENETIC_MUTATE_RATE      = 0.25
GENE_N_COLS_MIN          = 3
GENE_N_COLS_MAX          = 6
GENE_REPAIR_ATTEMPTS     = 3
GENE_DIVERSITY_THRESHOLD = 0.70
GENE_ISLAND_COUNT        = 4
GENE_MIGRATION_INTERVAL  = 10
# v0.9.x #24 — deterministic crowding replaces island model in pass 1.
# Single island of GENE_ISLAND_COUNT × pop_size with DC replacement;
# maintains diversity without spawning GENE_ISLAND_COUNT separate processes.
# Set to False to revert to island model (for A/B benchmarking).
GENE_USE_CROWDING        = True

# v1.0 optimizer selection — "ga" | "optuna"
# "optuna" uses TPE (Bayesian) search instead of the evolutionary GA.
# Requires: pip install optuna
GENE_OPTIMIZER           = "ga"

# v1.0 surrogate fitness model — wraps whichever optimizer is active.
# After SURROGATE_MIN_SAMPLES real evals the GBM predicts fitness;
# only SURROGATE_REAL_FRAC of subsequent calls hit the real scorer.
SURROGATE_ENABLED        = False
SURROGATE_REAL_FRAC      = 0.10   # fraction of evals using real fitness
SURROGATE_MIN_SAMPLES    = 40     # real evals before first surrogate fit
SURROGATE_RETRAIN_EVERY  = 20     # retrain after every N new real evals

# Pass 2
TOP_FRACTION_PASS2       = 0.25
MIN_TRADES_PER_DAY_PASS2 = 0.5
PASS2_GENERATIONS        = 20
PASS2_POPULATION         = 30
PASS2_MUTATE_RATE        = 0.15
PASS2_QUANTILE_LO        = 0.25
PASS2_QUANTILE_HI        = 0.75

ENSEMBLE_OVERLAP_THRESHOLD = 0.60

# Bidirectional
BIDIR_MIN_WR         = 52.0
BIDIR_MIN_TRADES     = 15
DISCRIM_MIN_ACCURACY = 0.62

# Scoring weights
SCORE_W_WR              = 0.30
SCORE_W_PF              = 0.30
SCORE_W_RR              = 0.25
SCORE_W_STAB            = 0.15
SCORE_WILSON_CONFIDENCE = 0.85

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
TARGET_STABILITY        = 0.65   # time-consistency × distribution goal (0..1)
TARGET_TRADES_PER_DAY   = 1.0    # trades-per-day goal
EXCESS_BONUS_WEIGHT     = 0.1

SCORE_W_TRADES_PER_DAY = 0.15   # weight for trades/day target component

# Quality filters
MIN_FREQ_PER_DAY        = 0.3
MIN_WIN_RATE            = 48.0
MIN_PROFIT_FACTOR       = 1.15
MAX_DRAWDOWN_R          = 15.0
MAX_CONSEC_LOSSES       = 8
MIN_TIME_CONSISTENCY    = 0.30
MIN_TEST_TRADES_PER_DAY = 0.3
CORRELATION_THRESHOLD   = 0.70
RECENT_BARS             = 8000

# Auto-run MC on top-N passing patterns (test split only)
RUN_MC_ON_TOP_N   = 5
MC_N_SIMS         = 10000
MC_BALANCE        = 100000
MC_LOT            = 0.10
MC_MAX_DAYS       = 60

# =============================================================================
#  INTERNALS
# =============================================================================

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
    return df.fillna(0)

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

def _init_enc(*arrs):
    global _ENC
    keys=["closes","bodies","rngs","uwks","lwks","bulls","rsi","macd","bbw",
          "trend","mtf","atr","vol_ratio","vol_body","regime",
          "vol_div","bb_exp","prev_sess"]
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
    args=[(i,w) for i in range(w,len(df))]
    with mp.Pool(n_workers,initializer=_init_enc,initargs=arrs) as pool:
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
    print(f"  Encoded {len(indices):,} windows  [{_elapsed(t0)}]")
    return X,indices

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
def compute_price_distributions(df,indices,labels,n_cl,fwd):
    """v5: inner bar loop replaced with NumPy slice operations per trade."""
    hi=df["high"].values; lo=df["low"].values
    cl=df["close"].values; op=df["open"].values
    atr=df["atr14"].values; n=len(df)
    results={}

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
    return results

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

        if long_ok and short_ok:
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


def write_combined_report(all_results, out_dir):
    """
    Deduplicate patterns from multiple seeds by Jaccard overlap on rule columns,
    re-rank globally by composite_score, then write combined CSV, report, chart.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ranked = sorted(all_results, key=lambda r: r.get("composite_score", 0), reverse=True)
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
        f"  Ranked by composite_score (descending)",
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
    trades = []; last_sig = -cooldown - 1

    for bi in member_bi:
        if bi + 1 >= n: continue
        if allowed and int(sess[bi]) not in allowed: continue
        if bi - last_sig < cooldown: continue
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
        ht = hs = False; bars_held = 0
        for j in range(bi + 1, min(bi + max_hold + 1, n)):
            h_ = hi[j]; lo_ = lo[j]; bars_held += 1
            if long:
                if h_ >= tp_v_eff: ht = True; break
                if lo_ <= sl_v_eff: hs = True; break
            else:
                if lo_ <= tp_v_eff: ht = True; break
                if h_ >= sl_v_eff: hs = True; break
        if not ht and not hs:
            exit_p = cl[min(bi + max_hold, n - 1)]
            exit_p_eff = exit_p - exit_spread if long else exit_p + exit_spread
            pnl = exit_p_eff - entry_ws if long else entry_ws - exit_p_eff
            if pnl > 0:
                ht = True
                reward = pnl / risk
            else:
                hs = True

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

        if ht:
            last_sig = bi
            trades.append((bi, "WIN", round(reward, 2), round(entry_ws, 2),
                           round(sl_v, 2), round(tp_v, 2),
                           direction, bars_held) + snap)
        elif hs:
            last_sig = bi
            trades.append((bi, "LOSS", -1.0, round(entry_ws, 2),
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
            losses+=1; gl+=1.0; rr_list.append(-1.0)
            equity.append(equity[-1]-1.0)
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
                cache[key] = col_mask
        mask &= col_mask
    return mask


def _score_genetic(member_bi, rule, sl_pct, tp_pct, direction,
                   full_cluster_size, train_days, _cache=None):
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
        return 0.0

    matched_arr = member_arr[match_mask]

    # ── filter: skip bars too close to the end ────────────────────────────
    valid = matched_arr[matched_arr + 1 < n]
    if len(valid) == 0:
        return 0.0

    # ── vectorised SL/TP detection (#22) ──────────────────────────────────
    # Build forward-window index matrix: shape (n_trades, fwd).
    # Clip to stay in bounds; pad with last valid index (price won't cross).
    fwd_idx = valid[:, None] + np.arange(1, fwd + 1, dtype=np.int32)[None, :]
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

    hi_mat = hi[fwd_idx]   # (n_trades, fwd)
    lo_mat = lo[fwd_idx]

    if long:
        cum_hi = np.maximum.accumulate(hi_mat, axis=1)
        cum_lo = np.minimum.accumulate(lo_mat, axis=1)
        tp_any = cum_hi >= tp_v[:, None]
        sl_any = cum_lo <= sl_v[:, None]
    else:
        cum_lo = np.minimum.accumulate(lo_mat, axis=1)
        cum_hi = np.maximum.accumulate(hi_mat, axis=1)
        tp_any = cum_lo <= tp_v[:, None]
        sl_any = cum_hi >= sl_v[:, None]

    tp_hit = tp_any.any(axis=1)
    sl_hit = sl_any.any(axis=1)
    tp_idx_v = np.where(tp_hit, np.argmax(tp_any, axis=1), fwd + 1)
    sl_idx_v = np.where(sl_hit, np.argmax(sl_any, axis=1), fwd + 1)

    # result per trade: 1=WIN, -1=LOSS, 0=timeout
    results = np.where(tp_idx_v < sl_idx_v, 1,
              np.where(sl_idx_v < tp_idx_v, -1, 0)).astype(np.int8)

    # ── lightweight sequential cooldown filter ────────────────────────────
    last_sig = -COOLDOWN_BARS - 1
    sel: list[int] = []
    for i, bi in enumerate(valid.tolist()):
        if bi - last_sig < COOLDOWN_BARS:
            continue
        if results[i] != 0:
            last_sig = bi
            sel.append(i)

    if not sel:
        return 0.0
    sel_idx = np.asarray(sel, dtype=np.int32)
    res_sel = results[sel_idx]
    wins   = int((res_sel == 1).sum())
    losses = int((res_sel == -1).sum())
    total  = wins + losses
    if total < 10:
        return 0.0

    rr_arr = reward[sel_idx] / risk[sel_idx]
    avg_rr = float(rr_arr[res_sel == 1].mean()) if wins > 0 else 0.0

    # ── pure-NumPy consistency scoring (#21) ─────────────────────────────
    traded_bars  = valid[sel_idx]           # bar indices of all trades
    is_win_arr   = (res_sel == 1)
    q_stab = _time_consistency_np(is_win_arr, traded_bars, n, train_days)
    q_dist = _trade_distribution_np(traded_bars, n, train_days)
    stability = q_stab * q_dist

    # Re-use existing score_rule logic but bypass trades_df construction.
    wr = wins / total
    breakeven = ((1 - wr) / wr) if wr > 0 else 999.0
    if avg_rr < breakeven:
        return 0.0
    gl = losses
    gw = wins * avg_rr if avg_rr > 0 else 0
    pf = gw / gl if gl > 0 else 2.0

    use_targets = bool(globals().get("ENABLE_TARGET_SCORING", True))
    if use_targets:
        wr_pct  = wr * 100.0
        rr_val  = avg_rr / max(breakeven, 0.01)
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
    q_wr = wilson_lower(wins, total)
    q_pf = min(pf, 4.0) / 4.0
    q_rr = min(avg_rr / max(breakeven, 0.01), 2.0) / 2.0
    return (SCORE_W_WR * q_wr + SCORE_W_PF * q_pf +
            SCORE_W_RR * q_rr + SCORE_W_STAB * stability)

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
    (cid,member_bi,sl_pct,tp_pct,direction,full_cluster_size,
     island_id,generations,pop_size,mutate_rate,train_days,seed)=args
    rng_=np.random.default_rng(seed); arrays=_GEN["arrays"]
    if len(member_bi)<10: return cid,direction,island_id,{},0.0

    member_arr_full = np.asarray(member_bi, dtype=np.int32)
    col_stats={}
    for col in GENE_COLS:
        if col not in arrays: continue
        vals=arrays[col][member_arr_full]
        col_stats[col]=(float(np.percentile(vals,5)),
                        float(np.percentile(vals,95)))

    # #23 — coarse subset (every 3rd bar) used for pass 1
    member_arr_coarse = member_arr_full[::3]
    coarse_cluster_size = max(len(member_arr_coarse), 1)
    # cutoff generation where we switch from coarse → full
    pass1_gens = max(1, int(generations * 0.75))
    pass2_gens = generations - pass1_gens

    def _score(rule, use_full=False, cache=None):
        bi = member_arr_full if use_full else member_arr_coarse
        cs = full_cluster_size if use_full else coarse_cluster_size
        return _score_genetic(bi, rule, sl_pct, tp_pct, direction, cs,
                              train_days, _cache=cache)

    # #20 — per-worker column mask cache (cleared between full/coarse switch)
    cache: dict = {}

    pop=[_rand_rule(col_stats,GENE_N_COLS_MIN,GENE_N_COLS_MAX,rng_)
         for _ in range(pop_size)]
    scores=[_score(r, cache=cache) for r in pop]

    best_r=pop[int(np.argmax(scores))]; best_s=max(scores)
    no_improve=0; cur_mutate=mutate_rate
    hof = [(best_s, dict(best_r))]

    def _run_generations(n_gens, use_full):
        nonlocal pop, scores, best_r, best_s, no_improve, cur_mutate, hof, cache
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
                    pop[idx]=_rand_rule(col_stats,GENE_N_COLS_MIN,GENE_N_COLS_MAX,rng_)
                    scores[idx]=_score(pop[idx], use_full, cache)

            new_pop=[]
            top2=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)[:2]
            for idx in top2: new_pop.append(dict(pop[idx]))
            if gen % 5 == 0 and hof:
                _, hof_rule = max(hof, key=lambda x: x[0])
                new_pop.append(dict(hof_rule))

            while len(new_pop)<pop_size:
                p1=_tournament_select(pop,scores,k=3,rng_=rng_)
                p2=_tournament_select(pop,scores,k=3,rng_=rng_)
                child=_mutate_rule(_cross_rules(p1,p2,rng_),
                                   col_stats,cur_mutate,rng_)
                if not child: continue
                child_score=_score(child, use_full, cache)
                if child_score==0.0:
                    for _ in range(GENE_REPAIR_ATTEMPTS):
                        repaired=_diagnose_and_repair(
                            child,col_stats,
                            member_arr_full if use_full else member_arr_coarse,
                            sl_pct,tp_pct,direction,
                            full_cluster_size if use_full else coarse_cluster_size,
                            train_days,rng_)
                        if repaired:
                            rs=_score(repaired, use_full, cache)
                            if rs>0:
                                child=repaired; child_score=rs; break
                new_pop.append(child)
                if child_score > 0:
                    hof.append((child_score, dict(child)))
                    hof.sort(key=lambda x: x[0], reverse=True)
                    hof = hof[:10]
                if child_score>best_s:
                    best_s=child_score; best_r=dict(child)
                    no_improve=max(0,no_improve-2)

            pop=new_pop[:pop_size]
            scores=[_score(r, use_full, cache) for r in pop]
            gen_best=max(scores)
            if gen_best>best_s:
                best_s=gen_best; best_r=pop[int(np.argmax(scores))]
                hof.append((best_s, dict(best_r)))
                hof.sort(key=lambda x: x[0], reverse=True)
                hof = hof[:10]
                no_improve=max(0,no_improve-2)
            else:
                no_improve+=1

    def _run_generations_crowding(n_gens, use_full):
        """Deterministic Crowding replacement: children compete with most-similar parent."""
        nonlocal pop, scores, best_r, best_s, no_improve, cur_mutate, hof, cache
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
                p1_r, p2_r = pop[i1], pop[i2]
                c1 = _mutate_rule(_cross_rules(p1_r, p2_r, rng_), col_stats, cur_mutate, rng_)
                c2 = _mutate_rule(_cross_rules(p2_r, p1_r, rng_), col_stats, cur_mutate, rng_)
                if not c1 or not c2:
                    continue
                s1 = _score(c1, use_full, cache)
                s2 = _score(c2, use_full, cache)
                d11 = _rule_distance(c1, p1_r); d12 = _rule_distance(c1, p2_r)
                d21 = _rule_distance(c2, p1_r); d22 = _rule_distance(c2, p2_r)
                if d11 + d22 <= d12 + d21:
                    if s1 > scores[i1]: pop[i1] = c1; scores[i1] = s1
                    if s2 > scores[i2]: pop[i2] = c2; scores[i2] = s2
                else:
                    if s1 > scores[i2]: pop[i2] = c1; scores[i2] = s1
                    if s2 > scores[i1]: pop[i1] = c2; scores[i1] = s2

                for cs, ss in ((s1, c1), (s2, c2)):
                    if cs > 0:
                        hof.append((cs, dict(ss)))
                    if cs > best_s:
                        best_s = cs; best_r = dict(ss)

            hof.sort(key=lambda x: x[0], reverse=True)
            hof = hof[:10]
            gen_best = max(scores)
            if gen_best > best_s:
                best_s = gen_best; best_r = dict(pop[int(np.argmax(scores))])
                hof.append((best_s, dict(best_r)))
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
        hof_best_s, hof_best_r = max(hof, key=lambda x: x[0])
        if hof_best_s > best_s:
            best_s, best_r = hof_best_s, hof_best_r

    return cid,direction,island_id,best_r,best_s

def genetic_refine_parallel(df,indices,labels,candidate_keys,
                             price_dists,bidir_info,fwd,
                             generations,pop_size,mutate_rate,
                             train_days,seed,n_workers):
    """
    Run genetic refinement using island model.
    candidate_keys: list of (cid, direction) tuples.
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
        if use_crowding:
            # Single large-population worker per (cid, direction) — DC maintains diversity
            args.append((cid,cm.get(cid,[]),sl_p,tp_p,direction,
                         full_size,0,generations,pop_size*GENE_ISLAND_COUNT,
                         mutate_rate,train_days,
                         seed+cid*100+{"LONG":0,"SHORT":1}.get(direction,0)))
        else:
            for island in range(GENE_ISLAND_COUNT):
                args.append((cid,cm.get(cid,[]),sl_p,tp_p,direction,
                             full_size,island,generations,pop_size,
                             mutate_rate,train_days,
                             seed+cid*100+island*7+{"LONG":0,"SHORT":1}.get(direction,0)))

    with mp.Pool(n_workers,initializer=_init_genetic,
                 initargs=(arrays,hi,lo,cl,op,fwd,n,SPREAD_PTS)) as pool:
        island_results=[]; t0g=time.time()
        for i,r in enumerate(pool.imap_unordered(_genetic_worker,args)):
            island_results.append(r)
            _pbar(i+1,len(args),"evolving",t0=t0g)

    # Migration: for each (cid,direction) keep the best rule across islands
    # but also allow the best from other islands to influence
    best={}
    for cid,direction,island_id,rule,score in island_results:
        key=(cid,direction)
        if key not in best or score>best[key][1]:
            best[key]=(rule,score)

    print(f"  Genetic pass 1 done  [{_elapsed(t0)}]")
    return best  # {(cid,direction): (rule, score)}

def genetic_pass2_parallel(df,indices,labels,top_keys,genetic_p1,
                            price_dists,fwd,generations,pop_size,
                            mutate_rate,train_days,seed,n_workers):
    """Pass 2: tighter search starting warm from pass 1 best rules."""
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
    scores=[_score_genetic(member_bi,r,sl_pct,tp_pct,direction,
                           full_size,train_days) for r in pop]
    best_r=pop[int(np.argmax(scores))]; best_s=max(scores)
    for _ in range(generations):
        p1=_tournament_select(pop,scores,k=3,rng_=rng_)
        p2=_tournament_select(pop,scores,k=3,rng_=rng_)
        child=_mutate_rule(_cross_rules(p1,p2,rng_),col_stats,
                           mutate_rate,rng_,False,False)
        if not child: continue
        cs=_score_genetic(member_bi,child,sl_pct,tp_pct,
                          direction,full_size,train_days)
        if cs==0:
            rep=_diagnose_and_repair(child,col_stats,member_bi,sl_pct,tp_pct,
                                     direction,full_size,train_days,rng_)
            if rep:
                cs2=_score_genetic(member_bi,rep,sl_pct,tp_pct,
                                   direction,full_size,train_days)
                if cs2>0: child=rep; cs=cs2
        idx_worst=int(np.argmin(scores))
        if cs>=scores[idx_worst]: pop[idx_worst]=child; scores[idx_worst]=cs
        if cs>best_s: best_s=cs; best_r=dict(child)
    return (cid,direction),best_r,best_s


def _optuna_worker(args):
    """v1.0 #26/#27: Optuna TPE optimizer (+ optional surrogate) for one (cid, direction)."""
    (cid, member_bi, sl_pct, tp_pct, direction, full_size,
     n_trials, train_days, seed, use_surrogate) = args

    import optuna as _optuna
    _optuna.logging.set_verbosity(_optuna.logging.WARNING)

    arrays_ = _GEN["arrays"]
    if len(member_bi) < 10:
        return cid, direction, 0, {}, 0.0

    member_arr = np.asarray(member_bi, dtype=np.int32)
    col_stats: dict = {}
    for col in GENE_COLS:
        if col not in arrays_: continue
        vals = arrays_[col][member_arr]
        col_stats[col] = (float(np.percentile(vals, 5)), float(np.percentile(vals, 95)))

    sorted_cols = sorted(col_stats.keys())
    rng_ = np.random.default_rng(seed)

    # ── Surrogate state ───────────────────────────────────────────────────────
    surr_X: list = []
    surr_y: list = []
    surrogate = None
    real_eval_count = [0]
    real_since_retrain = [0]

    def _rule_to_vec(rule: dict) -> list:
        vec = []
        for col in sorted_cols:
            vmin, vmax = col_stats[col]
            span = vmax - vmin if vmax > vmin else 1.0
            if col in rule:
                lo, hi = rule[col]
                vec.extend([1.0, (lo - vmin) / span, (hi - vmin) / span])
            else:
                vec.extend([0.0, 0.0, 0.0])
        return vec

    def _real_score(rule: dict) -> float:
        s = _score_genetic(member_bi, rule, sl_pct, tp_pct, direction, full_size, train_days)
        real_eval_count[0] += 1
        real_since_retrain[0] += 1
        if use_surrogate:
            surr_X.append(_rule_to_vec(rule))
            surr_y.append(s)
            n_real = len(surr_y)
            if (n_real >= SURROGATE_MIN_SAMPLES and
                    real_since_retrain[0] >= SURROGATE_RETRAIN_EVERY):
                nonlocal surrogate
                from sklearn.ensemble import GradientBoostingRegressor
                surrogate = GradientBoostingRegressor(
                    n_estimators=80, max_depth=3, learning_rate=0.1,
                    random_state=int(seed) % (2 ** 31))
                surrogate.fit(surr_X, surr_y)
                real_since_retrain[0] = 0
        return s

    def _eval(rule: dict) -> float:
        if (use_surrogate and surrogate is not None and
                rng_.random() > SURROGATE_REAL_FRAC):
            pred = float(surrogate.predict([_rule_to_vec(rule)])[0])
            return max(0.0, pred)
        return _real_score(rule)

    # ── Objective ─────────────────────────────────────────────────────────────
    def objective(trial: "_optuna.Trial") -> float:
        rule: dict = {}
        for col in sorted_cols:
            use = trial.suggest_categorical(f"use_{col}", [0, 1])
            if not use:
                continue
            vmin, vmax = col_stats[col]
            lo_pct = trial.suggest_float(f"lo_pct_{col}", 0.0, 0.95)
            w_pct  = trial.suggest_float(f"w_pct_{col}",  0.01, 1.0 - lo_pct)
            lo = vmin + lo_pct * (vmax - vmin)
            hi = vmin + (lo_pct + w_pct) * (vmax - vmin)
            if lo < hi:
                rule[col] = (lo, hi)
        if len(rule) < GENE_N_COLS_MIN:
            return 0.0
        if len(rule) > GENE_N_COLS_MAX:
            keep = list(rule.keys())[:GENE_N_COLS_MAX]
            rule = {k: rule[k] for k in keep}
        return _eval(rule)

    # ── Reconstruct best rule from trial params ───────────────────────────────
    def _params_to_rule(params: dict) -> dict:
        rule: dict = {}
        for col in sorted_cols:
            if not params.get(f"use_{col}", 0):
                continue
            vmin, vmax = col_stats[col]
            lo_pct = params.get(f"lo_pct_{col}", 0.0)
            w_pct  = params.get(f"w_pct_{col}", 0.1)
            lo = vmin + lo_pct * (vmax - vmin)
            hi = vmin + (lo_pct + w_pct) * (vmax - vmin)
            if lo < hi:
                rule[col] = (lo, hi)
        return rule

    sampler = _optuna.samplers.TPESampler(seed=int(seed) % (2 ** 31))
    study = _optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best_rule = _params_to_rule(study.best_trial.params)
    best_score = float(study.best_value)
    return cid, direction, 0, best_rule, best_score


def optuna_refine_parallel(df, indices, labels, candidate_keys,
                            price_dists, fwd,
                            n_trials, train_days, seed, n_workers):
    """
    v1.0 #26/#27: Optuna TPE optimizer as a drop-in replacement for
    genetic_refine_parallel().  Returns the same {(cid,direction):(rule,score)} dict.
    n_trials ≈ GENETIC_GENERATIONS × GENETIC_POPULATION for a fair comparison.
    """
    t0 = time.time()
    use_surrogate = bool(globals().get("SURROGATE_ENABLED", False))
    mode = "TPE + surrogate" if use_surrogate else "TPE"
    print(f"  Optuna {mode}: {len(candidate_keys)} rules × {n_trials} trials "
          f"on {n_workers} cores ...")

    arrays = {col: df[col].values.copy() for col in GENE_COLS if col in df.columns}
    hi = df["high"].values.copy(); lo_arr = df["low"].values.copy()
    cl = df["close"].values.copy(); op = df["open"].values.copy(); n = len(df)
    cm: dict = {}
    for pos, bi in enumerate(indices):
        cid = int(labels[pos])
        if cid not in cm: cm[cid] = []
        cm[cid].append(bi)

    args = []
    for cid, direction in candidate_keys:
        d = price_dists.get(cid)
        sl_p = d["sl_pct"] if d else 0.002
        tp_p = d["tp_pct"] if d else 0.003
        full_size = len(cm.get(cid, []))
        args.append((cid, cm.get(cid, []), sl_p, tp_p, direction,
                     full_size, n_trials, train_days,
                     seed + cid * 100 + {"LONG": 0, "SHORT": 1}.get(direction, 0),
                     use_surrogate))

    with mp.Pool(n_workers, initializer=_init_genetic,
                 initargs=(arrays, hi, lo_arr, cl, op, fwd, n, SPREAD_PTS)) as pool:
        results = []; t0g = time.time()
        for i, r in enumerate(pool.imap_unordered(_optuna_worker, args)):
            results.append(r)
            _pbar(i + 1, len(args), "optimizing", t0=t0g)

    best: dict = {}
    for cid, direction, _island, rule, score in results:
        key = (cid, direction)
        if key not in best or score > best[key][1]:
            best[key] = (rule, score)

    print(f"  Optuna done  [{_elapsed(t0)}]")
    return best


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
                     fwd, n_workers, trading_days):
    """Backtest only bars matching each genetic rule."""
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

    filtered_args = []
    for (cid, direction), (rule, score) in genetic_rules.items():
        d = price_dists.get(cid)
        sl_p = d["sl_pct"] if d else 0.002
        tp_p = d["tp_pct"] if d else 0.003
        filtered_bi = [bi for bi in cm.get(cid, [])
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
        cid = key[0] if isinstance(key, tuple) else key
        m = _calc_metrics(trades, filt_size, trading_days, tdf)
        m["cluster"] = cid; m["key"] = key
        m["original_signals"] = orig_size
        m["filtered_signals"] = filt_size
        m["signal_retention"] = round(filt_size / max(orig_size, 1) * 100, 1)
        d = price_dists.get(cid) or {}
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

    # Header comment
    lines += [
        f"; Pattern {pattern_no} — Cluster {cid} [{direction}] [{bidir_mode}]",
        f"; Train: WR={r.get('win_rate_',0)}%  Wilson={r.get('wilson_wr',0)}%"
        f"  PF={r.get('profit_factor',0)}  Score={r.get('composite_score',0)}",
        f"; Test:  WR={r.get('test_wr',0)}%  PF={r.get('test_pf',0)}"
        f"  Trades={r.get('test_trades',0)}",
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
    lines += [
        "",
        "; Position sizing",
        "Lots=0.10",
        "CooldownBars=3",
        "BreakevenAtR=0.0",
        "UseTrailing=false",
        "TrailingStart=1.0",
        "TrailingStep=0.5",
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
    passers.sort(key=lambda r: r.get("score", 0.0), reverse=True)
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

    # Assign test bars using full feature vector (fix 1.4)
    # Compute train cluster centroids in the full feature space
    print("  Assigning test bars via full feature vector ...")
    sc_tr=StandardScaler(); X_tr_scaled=sc_tr.fit_transform(X_tr)
    # Compute centroid per cluster in scaled feature space
    feat_centroids_tr={}
    for cid in range(n_cl):
        mask=labels_tr==cid
        if mask.sum()>0:
            feat_centroids_tr[cid]=X_tr_scaled[mask].mean(axis=0)
    # Scale test features with TRAINING scaler (not re-fitted)
    X_te_scaled=sc_tr.transform(X_te)
    # Assign each test bar to nearest centroid by Euclidean distance
    # Assign each test bar to nearest centroid — fully vectorised (v5 speedup)
    labels_te=np.zeros(len(idx_te),dtype=np.int32)
    centroid_matrix=np.vstack([feat_centroids_tr[c]
                                for c in range(n_cl)
                                if c in feat_centroids_tr])
    valid_cids=[c for c in range(n_cl) if c in feat_centroids_tr]
    # cdist is much faster than a Python loop for large test sets
    dists_all = cdist(X_te_scaled, centroid_matrix, metric="euclidean")
    labels_te = np.array(valid_cids, dtype=np.int32)[np.argmin(dists_all, axis=1)]

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
                and r.get("win_rate_",0)>=MIN_WIN_RATE*0.85]
    print(f"  {len(candidates)} candidates for genetic pass 1 "
          f"(min {min_trades_p1} trades)")

    # ── Genetic / Optuna pass 1 ───────────────────────────────────────────────
    _optimizer = str(globals().get("GENE_OPTIMIZER", "ga")).lower()
    _stage(f"{'Optuna' if _optimizer == 'optuna' else 'Genetic'} Pass 1")
    if candidates:
        if _optimizer == "optuna":
            _n_trials = GENETIC_GENERATIONS * GENETIC_POPULATION  # equiv budget
            genetic_p1 = optuna_refine_parallel(
                df_train, idx_tr, labels_tr, candidates,
                price_dists_tr, FORWARD_BARS,
                _n_trials, train_days, RANDOM_SEED, nw)
        else:
            genetic_p1=genetic_refine_parallel(
                df_train,idx_tr,labels_tr,candidates,
                price_dists_tr,bidir_info,FORWARD_BARS,
                GENETIC_GENERATIONS,GENETIC_POPULATION,
                GENETIC_MUTATE_RATE,train_days,RANDOM_SEED,nw)
    else:
        genetic_p1={}; print("  No candidates.")

    # ── Post-P1 backtest ──────────────────────────────────────────────────────
    _stage("Post-P1 Backtest")
    bt_p1=backtest_refined(
        df_train,idx_tr,labels_tr,genetic_p1,
        price_dists_tr,FORWARD_BARS,nw,train_days)
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
    if top_keys:
        genetic_p2=genetic_pass2_parallel(
            df_train,idx_tr,labels_tr,top_keys,genetic_p1,
            price_dists_tr,FORWARD_BARS,
            PASS2_GENERATIONS,PASS2_POPULATION,
            PASS2_MUTATE_RATE,train_days,RANDOM_SEED,nw)
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
        genetic_final=genetic_p1; print("  Skipped.")

    # ── Post-P2 backtest ──────────────────────────────────────────────────────
    _stage("Post-P2 Backtest")
    bt_final=backtest_refined(
        df_train,idx_tr,labels_tr,genetic_final,
        price_dists_tr,FORWARD_BARS,nw,train_days)
    print(f"  Final refined results:")
    for key,r in sorted(bt_final.items(),
                        key=lambda x:x[1].get("composite_score",0),reverse=True):
        print(f"  C{key[0]:>2d} {key[1]:>5}: WR={r['win_rate_']}% "
              f"Wilson={r['wilson_wr']}% PF={r['profit_factor']} "
              f"score={r['composite_score']}")

    # ── Validate on test ──────────────────────────────────────────────────────
    _stage("Validate (test)")
    price_dists_te=compute_price_distributions(
        df_test,idx_te,labels_te,n_cl,FORWARD_BARS)
    bt_test=backtest_refined(
        df_test,idx_te,labels_te,
        {k:v for k,v in genetic_final.items() if isinstance(k[0],int)},
        price_dists_te,FORWARD_BARS,nw,test_days)

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
        d_tr=price_dists_tr.get(cid) or {}
        info=bidir_info.get(cid,{})
        # v5 soft filter: tally all fails; marginal = exactly 1 fail
        def _chk(name,val,thresh,mode="min"):
            fails=val<thresh if mode=="min" else val>thresh
            print(f"    [{'FAIL' if fails else 'ok':>4}] {name:<28} {val}  "
                  f"({'<' if mode=='min' else '>'} {thresh})")
            return fails

        print(f"\n  C{cid:>2d} {direction} checking filters:")
        trd_df=tr.get("trades",pd.DataFrame())
        consistency=time_consistency_score(trd_df)
        checks=[
            ("implied_rr",       d_tr.get("implied_rr",0),     MIN_DIST_RR,         "min"),
            ("win_rate_%",        tr.get("win_rate_",0),         MIN_WIN_RATE,         "min"),
            ("wilson_wr",         tr.get("wilson_wr",0),         MIN_WIN_RATE*0.9,     "min"),
            ("profit_factor",     tr.get("profit_factor",0),     MIN_PROFIT_FACTOR,    "min"),
            ("composite_score",   tr.get("composite_score",0),   0.25,                 "min"),
            ("max_drawdown_r",    tr.get("max_drawdown_r",99),   MAX_DRAWDOWN_R,       "max"),
            ("max_consec_losses", tr.get("max_consec_losses",99),MAX_CONSEC_LOSSES,    "max"),
            ("per_day",           tr.get("per_day",0),           MIN_FREQ_PER_DAY,     "min"),
            ("test_trades",       te.get("total_trades",0),      min_test_trades,      "min"),
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
        n_fails = len(fail_details)
        soft_fail = None  # populated only for marginals
        if n_fails == 0:
            print(f"    [PASS] All filters passed ✓")
            is_marginal = False
        elif USE_SOFT_FILTER and n_fails == 1:
            soft_fail = fail_details[0]
            op = "<" if soft_fail["mode"] == "min" else ">"
            print(f"    [MARGINAL] 1 filter failed: {soft_fail['name']} "
                  f"({soft_fail['value']} {op} {soft_fail['threshold']}) — kept with ⚠ tag")
            is_marginal = True
        else:
            print(f"    [DROP] {n_fails} filters failed")
            continue
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
            "marginal":      is_marginal,   # v5: True = passed via soft filter
            "soft_fail":     soft_fail,     # {name,value,threshold,mode} or None
            # MC-chain fields
            "passed":        True,
            "score":         tr.get("composite_score", 0.0),
            "pattern_id":    _pat_id,
            "test_trades_df": te.get("trades", pd.DataFrame()),
        })

    final_results.sort(key=lambda r:r.get("composite_score",0),reverse=True)
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
              f"{r['per_day']}/day")
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
        if not tdf.empty or not tdf_te.empty:
            fp=str(OUT/f"cluster_{cid}_{direction}_seed{RANDOM_SEED}.csv")
            # Tag each split and ensure required MC columns exist
            parts=[]
            for _sdf, _split in [(tdf,"train"),(tdf_te,"test")]:
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
            f"PF={r['profit_factor']} Score={r['composite_score']} {r['per_day']}/day",
            f"  Test:  WR={r['test_wr']}% PF={r['test_pf']} "
            f"trades={r['test_trades']} "
            f"({round(r['test_trades']/max(test_days,1),2)}/day) "
            f"Score={r['test_score']}",
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