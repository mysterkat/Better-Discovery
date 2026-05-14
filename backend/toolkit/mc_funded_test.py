"""
FTMO 2-Step Evaluation Simulator
==================================
Compatible with Python 3.14

Simulates the full FTMO 2-Step pipeline:
    Phase 1  FTMO Challenge  — 10% profit target, 5% daily loss, 10% max loss, min 4 days
    Phase 2  Verification    —  5% profit target, same rules, min 4 days
    Funded Account           — no profit target, same DD rules, configurable payouts

Drawdown model (exact FTMO rules):
    Max Loss (total floor): FIXED — always initial_balance x 10%, never moves.
    Max Daily Loss:         FIXED dollar amount (initial_balance x 5%), but the
                            reference resets each midnight to that day's opening balance.
                            Daily floor = today's opening equity - fixed_daily_loss_$
                            A good day gives you more room tomorrow; the allowed loss
                            stays the same dollar amount regardless.

No consistency rule. No scaling plan.

SIMULATION NOTE:
    The CSV provides end-of-day aggregated P&L, not intraday tick data.
    Daily loss is approximated as each day's P&L measured from that day's
    opening balance. Intraday floating drawdown on positions held overnight
    (which FTMO checks in real-time) is NOT captured. Results will slightly
    underestimate breach probability vs live trading. Commission and swap
    impact on the daily loss limit is also not modelled from the backtest CSV.

Dashboard: single HTML file with 3 clickable tabs (Challenge / Verification / Funded)

Usage (TradingView):
    1. Export from TradingView: Strategy Tester > List of Trades > Download
    2. Set DATA_SOURCE = "tradingview"
    3. Set FILE_PATH to your CSV path
    4. Run: python mc_funded_test.py

Usage (MetaTrader 5):
    1. In MT5 Strategy Tester, run your backtest
    2. Right-click the results table > Save as Report  -> saves a .html file
    3. Set DATA_SOURCE = "mt5_html"
    4. Set FILE_PATH_MT5_HTML to that .html path
    5. Run: python mc_funded_test.py

    NOTE: The "Graph" CSV export from MT5 (balance/equity curve) is NOT
    supported — it contains no per-trade profit data. Use the HTML report only.

Dependencies:
    pip install numpy pandas plotly beautifulsoup4
"""

import sys
import warnings
import pathlib

import numpy as np
import pandas as pd
import plotly.graph_objects as go

warnings.filterwarnings("ignore")

# Cancel-check hook: when running inside the FastAPI app, the runners module
# provides ``check_cancelled()`` which raises if the user cancelled the job.
# When the toolkit is imported standalone (CLI / tests / notebooks) we fall
# back to a no-op so the module stays importable without the app context.
try:
    from app.jobs.runners import check_cancelled  # type: ignore[import-not-found]
except ImportError:
    def check_cancelled():  # type: ignore[no-redef]
        pass

# -----------------------------------------------------------------------------
#  USER CONFIGURATION  <- only edit this block
# -----------------------------------------------------------------------------

# Set to "tradingview" or "mt5_html"
DATA_SOURCE = "mt5_html"

# Defaults for standalone/script-mode runs. The Better Discovery app passes
# the actual report path via overrides at runtime, so these only matter if
# you invoke `python mc_funded_test.py` directly. Set them before running
# standalone, or leave the empty string to be reminded at startup.

# -- TradingView source -------------------------------------------------------
FILE_PATH = ""  # e.g. r"C:\path\to\TradingView-export.csv"

# -- MetaTrader 5 source ------------------------------------------------------
# MT5: Strategy Tester > right-click results > Save as Report (.html)
FILE_PATH_MT5_HTML = ""  # e.g. r"C:\path\to\ReportNEW.html"

N_SIMULATIONS     = 10_000
N_DISPLAY_CURVES  = 200
CONFIDENCE_LEVELS = (5, 25, 50, 75, 95)
RANDOM_SEED       = 64

# -- Phase 1: FTMO Challenge --------------------------------------------------
P1_BALANCE       = 10_000.0  # account size ($10k / $25k / $50k / $100k / $200k)
P1_LEVERAGE      = 1.0      # trade size multiplier vs your backtest
P1_PROFIT_TARGET = 0.10      # 10% of initial balance
P1_MAX_DAILY_DD  = 0.05      # 5%  fixed dollar amount, reference resets each day
P1_MAX_TOTAL_DD  = 0.10      # 10% fixed floor from initial balance, never moves
P1_MIN_DAYS      = 4         # minimum trading days (days with at least 1 trade)
P1_MAX_SIM_DAYS  = 365       # safety cap -- no time limit in real FTMO

# -- Phase 2: Verification ----------------------------------------------------
P2_BALANCE       = 10_000.0  # same size as Challenge by default
P2_LEVERAGE      = 1.0
P2_PROFIT_TARGET = 0.05      # 5% -- half of Phase 1
P2_MAX_DAILY_DD  = 0.05
P2_MAX_TOTAL_DD  = 0.10
P2_MIN_DAYS      = 4
P2_MAX_SIM_DAYS  = 365

# -- Funded Account -----------------------------------------------------------
FD_BALANCE       = 10_000.0  # funded account size
FD_LEVERAGE      = 1.0
FD_MAX_DAILY_DD  = 0.05      # same 5% rule applies on funded
FD_MAX_TOTAL_DD  = 0.10      # same 10% rule applies on funded
FD_PROFIT_SPLIT  = 0.80      # 80% of profit goes to trader on payout

# Payout settings
# "threshold" = pay when profit >= threshold
# "schedule"  = pay every N trading days
# "both"      = whichever triggers first
FD_PAYOUT_MODE      = "schedule"
FD_PAYOUT_THRESHOLD = 0.05   # unused when mode is "schedule", kept for reference
FD_PAYOUT_SCHEDULE  = 14     # pay out every 14 trading days
FD_MIN_DAYS_PAYOUT  = 14     # min trading days per cycle before payout allowed
# True  = balance resets to starting balance after payout (most common)
# False = profit stays in account
FD_BALANCE_RESET    = True
FD_MAX_SIM_DAYS     = 252    # ~1 trading year

# -- Long-term Monte Carlo (Tab 4) --------------------------------------------
LT_DAYS              = 252
LT_SIMS              = 10_000
LT_RUIN_PCT          = 0.20
# Yahoo Finance ticker for buy-and-hold comparison in the Long-term tab.
# Set to "" to skip (also skipped if yfinance is not installed).
LT_BENCHMARK_TICKER  = ""
# -----------------------------------------------------------------------------


# =============================================================================
#  1. DATA LOADING
# =============================================================================

def clean_numeric(series):
    return (
        series.astype(str)
        .str.replace(r"[$%,\s]", "", regex=True)
        .str.replace(r"\((.+)\)", r"-\1", regex=True)
        .pipe(pd.to_numeric, errors="coerce")
    )


def load_tradingview_csv(filepath):
    path = pathlib.Path(filepath)
    if not path.exists():
        sys.exit(
            "\n[ERROR] File not found: " + str(path.resolve()) + "\n"
            "  Export: TradingView > Strategy Tester > List of Trades > Download\n"
        )

    raw = pd.read_csv(path, header=0)
    raw.columns = (
        raw.columns
        .str.strip().str.lower()
        .str.replace(r"[^a-z0-9]+", "_", regex=True)
        .str.strip("_")
    )
    print("[INFO] Raw columns:", list(raw.columns))

    rename_map = {
        "net_p_l_usd"   : "profit",
        "net_p_l_"      : "profit_pct",
        "net_p_l"       : "profit_pct",
        "profit_usd"    : "profit",
        "date_and_time" : "date",
        "date_time"     : "date",
    }
    df = raw.rename(columns=rename_map).copy()

    if "type" in df.columns:
        mask = df["type"].str.lower().str.contains("exit|close|sell|cover", na=False)
        df   = df[mask].copy()

    if df.empty:
        sys.exit("[ERROR] No exit trades found. Check CSV format.")

    for col in ("profit", "profit_pct"):
        if col in df.columns:
            df[col] = clean_numeric(df[col])

    if "profit" not in df.columns:
        sys.exit("[ERROR] Could not find profit column. Available: " + str(list(df.columns)))

    if "date" in df.columns:
        df["date"]       = pd.to_datetime(df["date"], errors="coerce")
        df["trade_date"] = df["date"].dt.date
    else:
        df["trade_date"] = None

    df = df.dropna(subset=["profit"]).reset_index(drop=True)
    print("[INFO] Loaded", len(df), "completed trades from", path.name)
    return df


def load_mt5_html(filepath):
    """
    Parses a MetaTrader 5 Strategy Tester HTML report (Save as Report).

    The report contains two tables: Orders and Deals.
    Only the Deals table has per-trade profit data.
    Deals columns (0-indexed):
        0  Time        1  Deal      2  Symbol   3  Type
        4  Direction   5  Volume    6  Price     7  Order
        8  Commission  9  Swap      10 Profit    11 Balance
        12 Comment

    We keep only rows where Direction == "out" (trade exits with realised P&L).

    The file is UTF-16 LE encoded (standard MT5 output).
    Requires: pip install beautifulsoup4
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit(
            "\n[ERROR] beautifulsoup4 is required for MT5 HTML parsing.\n"
            "  Install with: pip install beautifulsoup4\n"
        )

    path = pathlib.Path(filepath)
    if not path.exists():
        sys.exit(
            "\n[ERROR] File not found: " + str(path.resolve()) + "\n"
            "  Export from MT5: Strategy Tester > right-click results > Save as Report\n"
        )

    # MT5 saves HTML as UTF-16 LE (with BOM)
    try:
        content = path.read_text(encoding="utf-16")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="replace")

    soup = BeautifulSoup(content, "html.parser")

    # -----------------------------------------------------------------------
    # Locate the Deals section header (<th> containing "Deals"),
    # then collect all <tr> rows that follow until the next section header.
    # -----------------------------------------------------------------------
    deals_header = None
    for th in soup.find_all("th"):
        if "Deals" in th.get_text():
            deals_header = th
            break

    if deals_header is None:
        sys.exit(
            "[ERROR] Could not find 'Deals' table section in the MT5 HTML report.\n"
            "  Make sure you exported via: Strategy Tester > right-click > Save as Report\n"
        )

    # The Deals column-header row is the next <tr> after the Deals <th> row
    rows = []
    # Walk through all <tr> siblings/descendants after the deals header
    # The table is flat — find the column header row, then harvest data rows
    all_rows = soup.find_all("tr")
    deals_section = False
    column_row_seen = False

    for tr in all_rows:
        text = tr.get_text(" ", strip=True)

        # Detect Deals section header
        if not deals_section:
            th_tags = tr.find_all("th")
            if any("Deals" in th.get_text() for th in th_tags):
                deals_section = True
            continue

        # Skip the column-label row (contains "Time", "Deal", "Commission", etc.)
        if not column_row_seen:
            tds = tr.find_all("td")
            if tds and any(
                kw in tds[0].get_text()
                for kw in ("Time", "Deal", "Open", "Order", "#")
            ):
                column_row_seen = True
                continue
            # sometimes the column row uses <th> not <td>
            ths = tr.find_all("th")
            if ths:
                column_row_seen = True
                continue

        # Stop if we hit another major section header
        ths = tr.find_all("th")
        if ths and not tr.find("td"):
            break

        tds = tr.find_all("td")
        if len(tds) < 11:
            continue
        rows.append([td.get_text(strip=True) for td in tds])

    if not rows:
        sys.exit(
            "[ERROR] No Deals rows extracted from the MT5 HTML.\n"
            "  The report may be empty or in an unexpected format.\n"
        )

    print("[INFO] Raw Deals rows found:", len(rows))

    # -----------------------------------------------------------------------
    # Build DataFrame and filter to closed trades (direction == "out")
    # -----------------------------------------------------------------------
    col_names = ["time", "deal", "symbol", "type", "direction",
                 "volume", "price", "order", "commission", "swap",
                 "profit", "balance", "comment"]

    df = pd.DataFrame(rows, columns=col_names[:len(rows[0])])

    # Extract regime from entry ("in") rows before filtering them out.
    # The EA writes "R:X" in the comment of the entry deal, not the exit deal.
    # Strategy: forward-fill the last seen regime so each "out" row inherits
    # the regime of the preceding "in" row for that trade.
    import re as _re
    if "comment" in df.columns:
        df["_regime_raw"] = df["comment"].str.extract(r"R:(\d)").astype(float)
        # Only "in" rows carry the tag; forward-fill to the next "out" row.
        df["_regime_raw"] = df["_regime_raw"].ffill()
    else:
        df["_regime_raw"] = float("nan")

    # Keep only exit deals
    df = df[df["direction"].str.lower() == "out"].copy()

    if df.empty:
        sys.exit("[ERROR] No exit deals (direction='out') found in the Deals table.")

    df["regime"] = df["_regime_raw"]
    df = df.drop(columns=["_regime_raw"])

    # Parse profit — MT5 uses spaces as thousands separators, e.g. "10 039.20"
    df["profit"] = (
        df["profit"]
        .str.replace(r"\s", "", regex=True)   # remove space thousands sep
        .str.replace(",", ".")                 # EU decimal comma safety
        .pipe(pd.to_numeric, errors="coerce")
    )

    # Parse commission and swap with the same cleanup, defaulting NaN to 0.
    # Both are usually negative (cost) or zero in MT5 reports.
    for _cost_col in ("commission", "swap"):
        if _cost_col in df.columns:
            df[_cost_col] = (
                df[_cost_col]
                .astype(str)
                .str.replace(r"\s", "", regex=True)
                .str.replace(",", ".")
                .pipe(pd.to_numeric, errors="coerce")
                .fillna(0.0)
            )
        else:
            df[_cost_col] = 0.0

    # Net P&L includes per-trade commission and swap costs.
    # Adding is correct because MT5 reports these as negative numbers when they
    # represent a cost; positive swap (credit) is also propagated correctly.
    df["net_profit"] = df["profit"] + df["commission"] + df["swap"]

    # Parse timestamp
    df["time"] = pd.to_datetime(df["time"], format="%Y.%m.%d %H:%M:%S", errors="coerce")
    df["trade_date"] = df["time"].dt.date

    df = df.dropna(subset=["profit"]).reset_index(drop=True)

    _commission_total = float(df["commission"].sum())
    _swap_total       = float(df["swap"].sum())
    _net_total        = float(df["net_profit"].sum())

    print("[INFO] Loaded", len(df), "closed trades from", path.name)
    print("[INFO] Date range:", str(df["trade_date"].min()), "->", str(df["trade_date"].max()))
    print("[INFO] Total P&L:  $" + str(round(df["profit"].sum(), 2)))
    print("[INFO] Win rate:    " + str(round((df["profit"] > 0).mean() * 100, 1)) + "%")
    print(
        "[INFO] Including commission ($" + str(round(_commission_total, 2))
        + ") and swap ($" + str(round(_swap_total, 2))
        + ") -- net P&L: $" + str(round(_net_total, 2))
    )

    return df


def get_daily_pnl(df, scale):
    """
    Aggregate trades to daily P&L and scale to the target account size.
    Each element = one trading day (days with no trades are excluded).

    Prefers the ``net_profit`` column when present (MT5 source — includes
    commission + swap). Falls back to ``profit`` for sources where the
    profit column is already net of costs (e.g. TradingView).
    """
    pnl_col = "net_profit" if "net_profit" in df.columns else "profit"
    profits_raw = df[pnl_col].to_numpy(dtype=float) * scale
    has_dates   = "trade_date" in df.columns and df["trade_date"].notna().any()
    if has_dates:
        return (
            df.assign(ps=profits_raw)
            .groupby("trade_date")["ps"]
            .sum()
            .to_numpy(dtype=float)
        )
    return profits_raw


# =============================================================================
#  2. EVALUATION PHASE ENGINE  (used for both Phase 1 and Phase 2)
# =============================================================================

def _build_regime_daily(trades_df, scale=1.0):
    """
    Build per-day dominant regime and per-regime daily P&L pools.

    Parameters
    ----------
    trades_df : DataFrame with 'trade_date', 'profit', and optionally 'regime'.
    scale     : profit scaling factor already applied to 'profit' if needed.

    Returns
    -------
    regime_by_date  : dict {date: regime_int}
    regime_pnl_pools: dict {regime_int: [daily_pnl_float, ...]}
    """
    if "regime" not in trades_df.columns or trades_df["regime"].isna().all():
        return {}, {}

    df = trades_df.copy()
    if "trade_date" not in df.columns:
        return {}, {}

    df = df.dropna(subset=["regime", "trade_date"])
    if df.empty:
        return {}, {}

    # Dominant regime per day
    dominant = (
        df.groupby("trade_date")["regime"]
        .agg(lambda x: x.mode().iloc[0])
        .astype(int)
    )
    regime_by_date = dominant.to_dict()

    # Daily P&L per day (already scaled via daily_pnl, but build from raw trades).
    # Prefer ``net_profit`` (commission + swap inclusive) when available, matching
    # ``get_daily_pnl`` so the regime pools are consistent with the global pool.
    pnl_col = "net_profit" if "net_profit" in df.columns else "profit"
    daily_profit = df.groupby("trade_date")[pnl_col].sum() * scale

    # Pool of daily P&L values per dominant regime
    regime_pnl_pools = {r: [] for r in range(5)}
    for date, regime in regime_by_date.items():
        if date in daily_profit.index and 0 <= regime < 5:
            regime_pnl_pools[regime].append(float(daily_profit[date]))

    return regime_by_date, regime_pnl_pools


def run_eval_phase(daily_pnl, balance, profit_target_pct, max_daily_dd_pct,
                   max_total_dd_pct, min_days, max_sim_days, rng, n_sims,
                   trans_matrix=None, regime_pnl_pools=None, start_regime=None,
                   phase_label="", predrawn_pnl=None, intraday_dd_factor=1.0,
                   dd_style="static", consistency_max_daily_pct=None,
                   keep_curves=0):
    """
    Simulates one evaluation phase.

    Max Daily Loss (FTMO rule):
        Dollar amount is fixed = balance x max_daily_dd_pct.
        Reference resets to each day's opening equity.
        Daily floor = opening_equity_today - daily_loss_abs

    Max Loss (FTMO rule):
        Fixed floor = balance x (1 - max_total_dd_pct). Never changes.

    Min trading days:
        Each element in daily_pnl represents one trading day.
        Must accumulate min_days before the profit target check is run.

    intraday_dd_factor:
        Safety factor applied to BOTH the daily and total DD limits before
        evaluation (1.0 = no adjustment, default).  Setting <1.0 tightens
        the effective limits to leave headroom for intraday floating losses
        on open positions which the end-of-day P&L cannot capture.  e.g.
        0.70 means "treat my 5% daily as if it were 3.5%".

    dd_style ('static' | 'trailing_eod' | 'trailing_intraday'):
        - 'static'           : original behaviour — fixed total floor from
                               the initial balance.
        - 'trailing_eod'     : at the start of each new day, ratchet the
                               total floor UP to ``equity * (1 - total_dd)``
                               whenever that exceeds the current floor.
                               Floor never moves down.
        - 'trailing_intraday': identical to 'trailing_eod' under our EOD-only
                               data granularity (no intraday peak available).
                               Provided for API parity; behaves the same.

    consistency_max_daily_pct (float | None):
        If set (e.g. 0.30 = 30%), at the END of an otherwise passing sim,
        check whether any single day's profit exceeded this fraction of the
        sim's total POSITIVE profit. If so, retroactively flip ``passed`` to
        False and stamp ``fail_reason = 'consistency_violation'``.

    keep_curves (int):
        When > 0, the equity paths for the FIRST ``keep_curves`` sims are
        retained on the returned ``curves`` list (others are dropped to save
        memory). When 0, all curves are returned (legacy behaviour).
    """
    # Apply intraday safety factor: tighter effective limits than the rulebook.
    effective_daily_loss_abs = balance * max_daily_dd_pct * intraday_dd_factor
    effective_total_dd_pct   = max_total_dd_pct * intraday_dd_factor
    effective_total_floor    = balance * (1.0 - effective_total_dd_pct)

    profit_target_abs = balance * profit_target_pct
    daily_loss_abs    = effective_daily_loss_abs   # fixed dollar, never changes
    total_floor       = effective_total_floor       # fixed floor, never moves

    use_markov = (
        trans_matrix is not None
        and regime_pnl_pools is not None
    )

    results = []
    curves  = []
    _trailing = dd_style in ("trailing_eod", "trailing_intraday")
    _keep_n   = max(0, int(keep_curves))

    # v0.6.0: ~200 progress ticks per phase (4× finer than before) for a
    # smoother progress bar. Cheap — just a print every ~50 sims at default n.
    _report_every = max(1, n_sims // 200)
    for _i in range(n_sims):
        if _i % _report_every == 0:
            print(f"MC_SIM {_i}/{n_sims} {phase_label}", flush=True)
        # Cooperative cancel check every 500 sims (no-op when standalone).
        if _i and (_i % 500 == 0):
            check_cancelled()
        equity        = balance
        days_traded   = 0
        passed        = False
        fail_reason   = None
        eq_path       = [equity]
        current_floor = total_floor          # ratchets up under trailing modes
        # Track per-day P&L for the optional consistency-rule post-check.
        track_consistency = consistency_max_daily_pct is not None
        day_pnl_log = [] if track_consistency else None

        # Only burn an RNG draw when the Markov path will actually fire — the
        # predrawn path bypasses Markov, so the legacy unconditional ``integers``
        # call broke seed reproducibility. Mirrors the funded loop fix.
        if use_markov and predrawn_pnl is None:
            current_regime = (
                int(start_regime)
                if start_regime is not None
                else int(rng.integers(0, 5))
            )

        for _day in range(max_sim_days):
            # --- sample daily P&L (Markov-guided or plain resample) ---
            if use_markov and predrawn_pnl is None:
                current_regime = int(
                    rng.choice(5, p=trans_matrix[current_regime])
                )
                pool = regime_pnl_pools.get(current_regime, [])
                if len(pool) >= 5:
                    day_pnl = float(rng.choice(pool))
                else:
                    day_pnl = float(rng.choice(daily_pnl))
            elif predrawn_pnl is not None:
                _d = min(_day, predrawn_pnl.shape[1] - 1)
                day_pnl = float(predrawn_pnl[_i % len(predrawn_pnl), _d])
            else:
                day_pnl = float(rng.choice(daily_pnl))

            # --- apply P&L and check rules (unchanged logic) ---
            day_open     = equity          # midnight snapshot for this day
            # Trailing DD: at the start of each new day, ratchet the total floor
            # UP if today's opening equity supports a higher floor. Never down.
            if _trailing:
                candidate = day_open * (1.0 - max_total_dd_pct * intraday_dd_factor)
                if candidate > current_floor:
                    current_floor = candidate
            days_traded += 1
            equity      += day_pnl
            equity       = max(equity, 0.0)
            eq_path.append(equity)
            if track_consistency:
                day_pnl_log.append(day_pnl)

            # daily loss breach: equity fell more than the fixed $ amount from open
            if equity < (day_open - daily_loss_abs):
                fail_reason = "daily_dd"
                break

            # total loss breach: floor (static or trailing)
            if equity < current_floor:
                fail_reason = "total_dd"
                break

            # profit target reached and min days met
            if (equity - balance) >= profit_target_abs and days_traded >= min_days:
                passed = True
                break

        # If the loop ran out of days without passing or breaching, attribute
        # the failure to profit shortfall (ran the clock without hitting target)
        if not passed and fail_reason is None:
            fail_reason = "profit_shortfall"

        # Consistency rule: a single huge day cannot dominate total profit.
        if passed and track_consistency and day_pnl_log:
            pos_total = sum(p for p in day_pnl_log if p > 0)
            if pos_total > 0:
                biggest = max(day_pnl_log)
                if biggest > pos_total * float(consistency_max_daily_pct):
                    passed      = False
                    fail_reason = "consistency_violation"

        results.append({
            "passed"      : passed,
            "fail_reason" : fail_reason if not passed else None,
            "days"        : days_traded,
            "final_equity": equity,
        })
        # Curve retention: legacy (keep all) when keep_curves==0, else first N.
        if _keep_n == 0 or _i < _keep_n:
            curves.append(eq_path)

    return results, curves


def pad_curves(curves):
    max_len = max(len(c) for c in curves)
    return np.array([c + [c[-1]] * (max_len - len(c)) for c in curves])


# =============================================================================
#  3. PHASE 1 -- FTMO CHALLENGE
# =============================================================================

def simulate_challenge(df, trans_matrix=None, regime_pnl_pools=None):
    scale     = P1_BALANCE / 100_000.0
    daily_pnl = get_daily_pnl(df, P1_LEVERAGE * scale)
    rng       = np.random.default_rng(RANDOM_SEED)

    print("\n[INFO] Running", N_SIMULATIONS, "Phase 1 (Challenge) simulations ...")
    raw, curves = run_eval_phase(
        daily_pnl, P1_BALANCE,
        P1_PROFIT_TARGET, P1_MAX_DAILY_DD, P1_MAX_TOTAL_DD,
        P1_MIN_DAYS, P1_MAX_SIM_DAYS, rng, N_SIMULATIONS,
        trans_matrix=trans_matrix,
        regime_pnl_pools=regime_pnl_pools,
        phase_label="Challenge",
    )

    df_res    = pd.DataFrame(raw)
    pass_rate = df_res["passed"].mean() * 100
    n_passed  = int(df_res["passed"].sum())

    fail_df    = df_res[~df_res["passed"]]
    total_fail = len(fail_df)
    fail_pcts  = {
        r: (int(fail_df["fail_reason"].eq(r).sum()) / total_fail * 100 if total_fail else 0.0)
        for r in ("daily_dd", "total_dd")
    }

    pass_df   = df_res[df_res["passed"]]
    avg_days  = float(pass_df["days"].mean()) if len(pass_df) else 0.0
    days_dist = pass_df["days"].values if len(pass_df) else np.array([])

    print("[INFO] Pass rate: " + str(round(pass_rate, 2)) + "%   avg days to pass: " + str(round(avg_days, 1)))
    return {
        "results_df"      : df_res,
        "pass_rate"       : pass_rate,
        "n_passed"        : n_passed,
        "n_failed"        : N_SIMULATIONS - n_passed,
        "fail_pcts"       : fail_pcts,
        "avg_days"        : avg_days,
        "days_dist"       : days_dist,
        "equity_curves"   : pad_curves(curves),
        "daily_pnl"       : daily_pnl,
        "trans_matrix"    : trans_matrix,
        "regime_pnl_pools": regime_pnl_pools,
    }


# =============================================================================
#  4. PHASE 2 -- VERIFICATION
# =============================================================================

def simulate_verification(p1, df, trans_matrix=None, regime_pnl_pools=None):
    """
    Only runs for simulations that passed Phase 1.
    Independent resampling -- passing P1 gives no momentum into P2.
    """
    n_p1_passed = p1["n_passed"]
    # inherit Markov params from p1 dict if not explicitly supplied
    if trans_matrix is None:
        trans_matrix = p1.get("trans_matrix")
    if regime_pnl_pools is None:
        regime_pnl_pools = p1.get("regime_pnl_pools")

    if n_p1_passed == 0:
        print("[WARN] No Phase 1 passes -- skipping Verification.")
        dummy = np.array([[P2_BALANCE, P2_BALANCE]])
        return {
            "results_df": pd.DataFrame(), "pass_rate": 0.0,
            "n_passed": 0, "n_failed": 0, "fail_pcts": {},
            "avg_days": 0.0, "days_dist": np.array([]),
            "equity_curves": dummy, "combined_pass_rate": 0.0,
            "n_p1_passed": 0,
        }

    scale     = P2_BALANCE / 100_000.0
    daily_pnl = get_daily_pnl(df, P2_LEVERAGE * scale)
    rng       = np.random.default_rng(RANDOM_SEED + 1)

    print("[INFO] Running", n_p1_passed, "Phase 2 (Verification) simulations ...")
    raw, curves = run_eval_phase(
        daily_pnl, P2_BALANCE,
        P2_PROFIT_TARGET, P2_MAX_DAILY_DD, P2_MAX_TOTAL_DD,
        P2_MIN_DAYS, P2_MAX_SIM_DAYS, rng, n_p1_passed,
        trans_matrix=trans_matrix,
        regime_pnl_pools=regime_pnl_pools,
        phase_label="Verification",
    )

    df_res    = pd.DataFrame(raw)
    pass_rate = df_res["passed"].mean() * 100
    n_passed  = int(df_res["passed"].sum())

    fail_df    = df_res[~df_res["passed"]]
    total_fail = len(fail_df)
    fail_pcts  = {
        r: (int(fail_df["fail_reason"].eq(r).sum()) / total_fail * 100 if total_fail else 0.0)
        for r in ("daily_dd", "total_dd")
    }

    pass_df   = df_res[df_res["passed"]]
    avg_days  = float(pass_df["days"].mean()) if len(pass_df) else 0.0
    days_dist = pass_df["days"].values if len(pass_df) else np.array([])

    combined_pass_rate = (n_passed / N_SIMULATIONS) * 100

    print("[INFO] Verification pass rate (of P1 passers): " + str(round(pass_rate, 2)) + "%")
    print("[INFO] Combined P1+P2 pass rate:               " + str(round(combined_pass_rate, 2)) + "%")
    return {
        "results_df"        : df_res,
        "pass_rate"         : pass_rate,
        "n_passed"          : n_passed,
        "n_failed"          : n_p1_passed - n_passed,
        "fail_pcts"         : fail_pcts,
        "avg_days"          : avg_days,
        "days_dist"         : days_dist,
        "equity_curves"     : pad_curves(curves),
        "combined_pass_rate": combined_pass_rate,
        "n_p1_passed"       : n_p1_passed,
        "trans_matrix"      : trans_matrix,
        "regime_pnl_pools"  : regime_pnl_pools,
    }


# =============================================================================
#  5. FUNDED ACCOUNT
# =============================================================================

def simulate_funded(p2, df, trans_matrix=None, regime_pnl_pools=None):
    """
    Runs for every simulation that passed both phases.

    Same DD rules as evaluation phases:
        Daily loss $ fixed = FD_BALANCE x FD_MAX_DAILY_DD, resets each day.
        Total floor fixed  = FD_BALANCE x (1 - FD_MAX_TOTAL_DD), never moves.
        If FD_BALANCE_RESET = True, floor resets to original after each payout.

    No profit target. No consistency rule. No scaling.
    """
    # inherit Markov params from p2 dict if not explicitly supplied
    if trans_matrix is None:
        trans_matrix = p2.get("trans_matrix")
    if regime_pnl_pools is None:
        regime_pnl_pools = p2.get("regime_pnl_pools")

    use_markov_fd = (trans_matrix is not None and regime_pnl_pools is not None)

    n_funded = p2["n_passed"]

    if n_funded == 0:
        print("[WARN] No Phase 2 passes -- skipping Funded simulation.")
        dummy = np.array([[FD_BALANCE, FD_BALANCE]])
        return {
            "results_df": pd.DataFrame(), "breach_rate": 100.0,
            "payout_rate": 0.0, "breach_pcts": {},
            "avg_first_payout_day": 0.0, "avg_total_earnings": 0.0,
            "avg_payout_count": 0.0,
            "survival": np.ones(FD_MAX_SIM_DAYS + 1),
            "equity_curves": dummy, "floor_curves": dummy,
            "n_funded": 0,
        }

    scale             = FD_BALANCE / 100_000.0
    daily_pnl         = get_daily_pnl(df, FD_LEVERAGE * scale)
    rng               = np.random.default_rng(RANDOM_SEED + 2)

    daily_loss_abs    = FD_BALANCE * FD_MAX_DAILY_DD
    total_floor       = FD_BALANCE * (1.0 - FD_MAX_TOTAL_DD)
    payout_thresh_abs = FD_BALANCE * FD_PAYOUT_THRESHOLD

    results         = []
    equity_curves_f = []
    floor_curves_f  = []

    print("[INFO] Running", n_funded, "Funded simulations ...")

    _report_every_fd = max(1, n_funded // 200)   # v0.6.0: finer ticks
    for _fi in range(n_funded):
        if _fi % _report_every_fd == 0:
            print(f"MC_SIM {_fi}/{n_funded} Funded", flush=True)
        equity            = FD_BALANCE
        current_floor     = total_floor
        days_active       = 0
        days_since_payout = 0
        payout_count      = 0
        total_earnings    = 0.0
        breach            = False
        breach_reason     = None
        breach_day        = None
        first_payout_day  = None

        eq_path    = [equity]
        floor_path = [current_floor]

        if use_markov_fd:
            fd_regime = int(rng.integers(0, 5))

        for _fd_day in range(FD_MAX_SIM_DAYS):
            # sample daily P&L
            if use_markov_fd:
                fd_regime = int(rng.choice(5, p=trans_matrix[fd_regime]))
                pool = regime_pnl_pools.get(fd_regime, [])
                if len(pool) >= 5:
                    day_pnl = float(rng.choice(pool))
                else:
                    day_pnl = float(rng.choice(daily_pnl))
            else:
                day_pnl = float(rng.choice(daily_pnl))
            day_open           = equity
            days_active       += 1
            days_since_payout += 1
            equity            += day_pnl
            equity             = max(equity, 0.0)

            eq_path.append(equity)
            floor_path.append(current_floor)

            # daily loss check
            if equity < (day_open - daily_loss_abs):
                breach        = True
                breach_reason = "daily_dd"
                breach_day    = days_active
                break

            # total loss check
            if equity < current_floor:
                breach        = True
                breach_reason = "total_dd"
                breach_day    = days_active
                break

            # payout trigger
            profit_above = equity - FD_BALANCE
            trigger      = False

            if FD_PAYOUT_MODE in ("threshold", "both"):
                if profit_above >= payout_thresh_abs and days_since_payout >= FD_MIN_DAYS_PAYOUT:
                    trigger = True

            if FD_PAYOUT_MODE in ("schedule", "both"):
                if (days_since_payout >= FD_PAYOUT_SCHEDULE
                        and profit_above > 0
                        and days_since_payout >= FD_MIN_DAYS_PAYOUT):
                    trigger = True

            if trigger and profit_above > 0:
                payout          = profit_above * FD_PROFIT_SPLIT
                total_earnings += payout
                payout_count   += 1

                if first_payout_day is None:
                    first_payout_day = days_active

                if FD_BALANCE_RESET:
                    equity        = FD_BALANCE
                    current_floor = total_floor

                days_since_payout = 0

        equity_curves_f.append(eq_path)
        floor_curves_f.append(floor_path)
        results.append({
            "breach"          : breach,
            "breach_reason"   : breach_reason,
            "breach_day"      : breach_day,
            "payout_count"    : payout_count,
            "total_earnings"  : total_earnings,
            "first_payout_day": first_payout_day,
            "days_active"     : days_active,
        })

    results_df  = pd.DataFrame(results)
    breach_rate = results_df["breach"].mean() * 100
    payout_rate = (results_df["payout_count"] > 0).mean() * 100

    breach_df    = results_df[results_df["breach"]]
    total_breach = len(breach_df)
    breach_pcts  = {
        r: (int(breach_df["breach_reason"].eq(r).sum()) / total_breach * 100
            if total_breach else 0.0)
        for r in ("daily_dd", "total_dd")
    }

    paid_df              = results_df[results_df["payout_count"] > 0]
    avg_first_payout_day = float(paid_df["first_payout_day"].mean()) if len(paid_df) else 0.0
    avg_total_earnings   = float(results_df["total_earnings"].mean())
    avg_payout_count     = float(results_df["payout_count"].mean())

    # avg days to reach the average total earnings
    # uses all sims (including those that breached early with zero earnings)
    avg_days_to_earn     = float(results_df["days_active"].mean())

    survival    = np.ones(FD_MAX_SIM_DAYS + 1)
    breach_days = results_df["breach_day"].dropna().astype(int).values
    for d in range(1, FD_MAX_SIM_DAYS + 1):
        alive       = float((breach_days >= d).sum() + (n_funded - len(breach_days)))
        survival[d] = alive / n_funded

    print("[INFO] Funded breach rate:    " + str(round(breach_rate, 2)) + "%")
    print("[INFO] Funded >= 1 payout:    " + str(round(payout_rate, 2)) + "%")
    print("[INFO] Avg total earnings:    $" + str(round(avg_total_earnings, 2)))
    print("[INFO] Avg days active:       " + str(round(avg_days_to_earn, 1)))

    return {
        "results_df"          : results_df,
        "breach_rate"         : breach_rate,
        "payout_rate"         : payout_rate,
        "breach_pcts"         : breach_pcts,
        "avg_first_payout_day": avg_first_payout_day,
        "avg_total_earnings"  : avg_total_earnings,
        "avg_payout_count"    : avg_payout_count,
        "avg_days_to_earn"    : avg_days_to_earn,
        "survival"            : survival,
        "equity_curves"       : pad_curves(equity_curves_f),
        "floor_curves"        : pad_curves(floor_curves_f),
        "n_funded"            : n_funded,
    }


# =============================================================================
#  6. COLOUR PALETTE & LAYOUT HELPERS
# =============================================================================

BG     = "#0d1117"
GRID   = "#21262d"
TEXT   = "#c9d1d9"
ACCENT = "#58a6ff"
GREEN  = "#3fb950"
RED    = "#f85149"
ORANGE = "#d29922"
PURPLE = "#bc8cff"


def dark_layout(title):
    return dict(
        title         = dict(text=title, font=dict(color=TEXT, size=16)),
        paper_bgcolor = BG,
        plot_bgcolor  = BG,
        font          = dict(color=TEXT),
        xaxis         = dict(gridcolor=GRID, zerolinecolor=GRID),
        yaxis         = dict(gridcolor=GRID, zerolinecolor=GRID),
        legend        = dict(bgcolor=BG, bordercolor=GRID),
        autosize      = True,
    )


def equity_fan(curves, title, xaxis_title, ref_lines, daily_loss_abs=None, breach_mask=None):
    """
    ref_lines:      list of (y_value, color, dash, label)
    daily_loss_abs: if provided, draws per-sim daily floor traces.
        floor[sim, day] = curves[sim, day-1] - daily_loss_abs  (FTMO midnight reset rule)
        Displayed as a step function (held flat all day, jumps at midnight).
    breach_mask:    boolean array len == len(curves). True = this sim failed/breached.
        Only sims where breach_mask is True get a floor trace drawn.
        If None, no floor traces are drawn (median floor line still shows).
    """
    x    = list(range(curves.shape[1]))
    pcts = {p: np.percentile(curves, p, axis=0) for p in CONFIDENCE_LEVELS}

    fig = go.Figure()

    rng        = np.random.default_rng(RANDOM_SEED)
    sample_idx = rng.choice(len(curves),
                             size=min(N_DISPLAY_CURVES, len(curves)),
                             replace=False)

    # which of the sampled sims actually breached (from simulation results)
    breaching_set = set()
    if daily_loss_abs is not None and breach_mask is not None:
        for i, idx in enumerate(sample_idx):
            if breach_mask[idx]:
                breaching_set.add(i)

    MAX_FLOOR_TRACES = 20   # cap to keep chart readable

    first_floor_trace = True
    floor_count       = 0
    for i, idx in enumerate(sample_idx):
        fig.add_trace(go.Scatter(
            x=x, y=list(curves[idx]),
            mode="lines",
            line=dict(color="rgba(88,166,255,0.15)", width=1.0),
            name="Simulations" if i == 0 else None,
            legendgroup="sims", showlegend=(i == 0),
            visible=False, hoverinfo="skip",
        ))
        if (daily_loss_abs is not None
                and i in breaching_set
                and floor_count < MAX_FLOOR_TRACES):
            floor_vals = list(curves[idx, :-1] - daily_loss_abs)
            xf         = list(range(1, curves.shape[1]))
            fig.add_trace(go.Scatter(
                x=xf, y=floor_vals,
                mode="lines",
                line=dict(color="rgba(248,81,73,0.50)", width=1.0, shape="hv"),
                name="Daily floor (failed sims)" if first_floor_trace else None,
                legendgroup="floors", showlegend=first_floor_trace,
                visible=False, hoverinfo="skip",
            ))
            first_floor_trace = False
            floor_count      += 1

    n_sim_traces = len(sample_idx) + floor_count

    for (lo, hi), alpha in [((5, 95), 0.10), ((25, 75), 0.20)]:
        fig.add_trace(go.Scatter(
            x=x + x[::-1],
            y=list(pcts[hi]) + list(pcts[lo])[::-1],
            fill="toself",
            fillcolor="rgba(88,166,255," + str(alpha) + ")",
            line=dict(color="rgba(0,0,0,0)"),
            name="P" + str(lo) + "-P" + str(hi),
            hoverinfo="skip",
        ))

    clr = {5: RED, 25: ORANGE, 50: ACCENT, 75: ORANGE, 95: GREEN}
    dsh = {5: "dot", 25: "dash", 50: "solid", 75: "dash", 95: "dot"}
    for p in CONFIDENCE_LEVELS:
        fig.add_trace(go.Scatter(
            x=x, y=list(pcts[p]), mode="lines",
            name="P" + str(p),
            line=dict(color=clr[p], dash=dsh[p], width=1.5),
        ))

    # median floor — always visible reference
    if daily_loss_abs is not None:
        all_floors   = curves[:, :-1] - daily_loss_abs
        median_floor = np.percentile(all_floors, 50, axis=0)
        xf_all       = list(range(1, curves.shape[1]))
        fig.add_trace(go.Scatter(
            x=xf_all, y=list(median_floor),
            mode="lines",
            name="Daily floor (median)",
            line=dict(color=ORANGE, dash="dash", width=1.5, shape="hv"),
            hovertemplate="Daily floor P50: $%{y:,.0f}<extra></extra>",
        ))

    for y_val, color, dash, label in ref_lines:
        fig.add_hline(y=y_val, line_dash=dash, line_color=color, line_width=1.5,
                      annotation_text=label, annotation_font_color=color)

    sim_idx_list = list(range(n_sim_traces))
    fig.update_layout(
        **dark_layout(title),
        xaxis_title=xaxis_title,
        yaxis_title="Account Equity ($)",
        yaxis_rangemode="nonnegative",
        hovermode="x unified",
        updatemenus=[dict(
            type="buttons", direction="left",
            x=0.01, y=1.08, xanchor="left",
            buttons=[
                dict(label="Show Sims", method="restyle",
                     args=[{"visible": True},  sim_idx_list]),
                dict(label="Hide Sims", method="restyle",
                     args=[{"visible": False}, sim_idx_list]),
            ],
            bgcolor=GRID, bordercolor=ACCENT,
            font=dict(color=TEXT, size=11),
            pad=dict(l=4, r=4, t=4, b=4),
        )],
    )
    return fig


def pass_donut(pass_rate, n_passed, n_failed, title, centre_label="Pass Rate"):
    fig = go.Figure(go.Pie(
        labels=["Pass", "Fail"],
        values=[max(pass_rate, 0.0001), max(100 - pass_rate, 0.0001)],
        hole=0.65,
        marker=dict(colors=[GREEN, RED]),
        textinfo="label+percent",
        textfont=dict(color=TEXT, size=13),
        hovertemplate="%{label}: %{value:.2f}%<extra></extra>",
    ))
    fig.add_annotation(text=str(round(pass_rate, 1)) + "%",
                       x=0.5, y=0.56, showarrow=False,
                       font=dict(size=28, color=GREEN if pass_rate >= 50 else RED))
    fig.add_annotation(text=centre_label, x=0.5, y=0.43, showarrow=False,
                       font=dict(size=13, color=TEXT))
    fig.add_annotation(text="Passed<br><b>" + str(n_passed) + "</b>",
                       x=-0.05, y=0.5, showarrow=False,
                       font=dict(size=13, color=GREEN), align="center")
    fig.add_annotation(text="Failed<br><b>" + str(n_failed) + "</b>",
                       x=1.05, y=0.5, showarrow=False,
                       font=dict(size=13, color=RED), align="center")
    fig.update_layout(**dark_layout(title), showlegend=True,
                      margin=dict(l=60, r=60, t=60, b=20))
    return fig


def fail_reasons_bar(fail_pcts, label_map, title):
    labels = list(label_map.values())
    values = [fail_pcts.get(k, 0.0) for k in label_map]
    colors = [RED, ORANGE, PURPLE][:len(labels)]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=values,
        marker_color=colors, opacity=0.85,
        text=[str(round(v, 1)) + "%" for v in values],
        textposition="outside", textfont=dict(color=TEXT),
    ))
    fig.update_layout(**dark_layout(title),
                      yaxis_title="% of Failed Runs", bargap=0.35)
    return fig


def days_histogram(days_dist, avg_days, title, min_days=None):
    if len(days_dist) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No passing simulations",
                           x=0.5, y=0.5, showarrow=False,
                           font=dict(size=16, color=TEXT))
        fig.update_layout(**dark_layout(title))
        return fig

    vals, counts = np.unique(days_dist.astype(int), return_counts=True)
    pct          = counts / counts.sum() * 100
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=vals, y=pct, marker_color=GREEN, opacity=0.75,
        hovertemplate="Day %{x}: %{y:.1f}%<extra></extra>",
    ))
    fig.add_vline(x=avg_days, line_dash="dash", line_color=ACCENT,
                  annotation_text="Avg: " + str(round(avg_days, 1)) + "d",
                  annotation_font_color=ACCENT, annotation_position="top right")
    if min_days:
        fig.add_vline(x=min_days, line_dash="dot", line_color=ORANGE,
                      annotation_text="Min: " + str(min_days) + "d",
                      annotation_font_color=ORANGE, annotation_position="bottom right")
    fig.update_layout(**dark_layout(title),
                      xaxis_title="Trading Days", yaxis_title="Probability (%)",
                      bargap=0.15)
    return fig


def kpi_table(labels, values, colors, height=110):
    fig = go.Figure(go.Table(
        header=dict(values=labels, fill_color=GRID,
                    font=dict(color=TEXT, size=13),
                    align="center", height=36),
        cells=dict(values=[[v] for v in values], fill_color=BG,
                   font=dict(color=colors, size=14, family="monospace"),
                   align="center", height=44),
    ))
    fig.update_layout(
        paper_bgcolor = BG,
        margin        = dict(t=10, b=0, l=0, r=0),
        height        = height,
        autosize      = True,
    )
    return fig


# =============================================================================
#  6b. REGIME / MARKOV HELPERS
# =============================================================================

REGIME_LABELS = ["TrendUp", "TrendDn", "Squeeze", "WideVol", "Choppy"]


def compute_regime_transitions(trades_df):
    """
    Build a Markov transition matrix from day-dominant regime labels.

    Parameters
    ----------
    trades_df : DataFrame with 'time' (or 'trade_date') and 'regime' columns.

    Returns
    -------
    trans_matrix   : np.ndarray (5, 5) — row-normalised probability matrix
    stationary_dist: np.ndarray (5,)   — stationary distribution
    regime_daily   : pd.Series         — date-indexed dominant regime per day
    """
    if "regime" not in trades_df.columns or trades_df["regime"].isna().all():
        return None, None, None

    df = trades_df.copy()
    if "trade_date" not in df.columns:
        if "time" in df.columns:
            df["trade_date"] = pd.to_datetime(df["time"]).dt.date
        else:
            return None, None, None

    df = df.dropna(subset=["regime"])
    if df.empty:
        return None, None, None

    # Per-day dominant regime (mode — take first if tie)
    regime_daily = (
        df.groupby("trade_date")["regime"]
        .agg(lambda x: x.mode().iloc[0])
        .astype(int)
    )

    # Build 5x5 transition count matrix from consecutive days
    counts = np.zeros((5, 5), dtype=float)
    regimes_list = regime_daily.values
    for i in range(len(regimes_list) - 1):
        r_from = int(regimes_list[i])
        r_to   = int(regimes_list[i + 1])
        if 0 <= r_from < 5 and 0 <= r_to < 5:
            counts[r_from, r_to] += 1

    # Row-normalise; rows with zero sum → uniform
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    trans_matrix = counts / row_sums
    # Fix any all-zero rows (still uniform after the above)
    for i in range(5):
        if trans_matrix[i].sum() == 0:
            trans_matrix[i] = 0.2

    # Stationary distribution via left eigenvector of transition matrix
    try:
        eigenvalues, eigenvectors = np.linalg.eig(trans_matrix.T)
        # Find eigenvector for eigenvalue closest to 1
        idx = np.argmin(np.abs(eigenvalues - 1.0))
        stat = np.real(eigenvectors[:, idx])
        stat = np.abs(stat)
        stat_sum = stat.sum()
        if stat_sum > 0:
            stationary_dist = stat / stat_sum
        else:
            stationary_dist = np.ones(5) / 5.0
    except Exception:
        stationary_dist = np.ones(5) / 5.0

    return trans_matrix, stationary_dist, regime_daily


def plot_regime_heatmap(trans_matrix, stationary_dist):
    """
    Returns a go.Figure with:
      - Heatmap of the 5x5 transition probability matrix (top)
      - Bar chart of the stationary distribution (bottom)
    Dark theme matching existing style.
    """
    from plotly.subplots import make_subplots

    text_vals = [["{:.2f}".format(trans_matrix[i, j]) for j in range(5)]
                 for i in range(5)]

    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.70, 0.30],
        subplot_titles=["Transition Probabilities", "Stationary Distribution"],
        vertical_spacing=0.12,
    )

    fig.add_trace(
        go.Heatmap(
            z=trans_matrix,
            x=REGIME_LABELS,
            y=REGIME_LABELS,
            text=text_vals,
            texttemplate="%{text}",
            colorscale="Blues",
            showscale=True,
            zmin=0.0, zmax=1.0,
            hovertemplate="From %{y} → To %{x}: %{z:.2f}<extra></extra>",
        ),
        row=1, col=1,
    )

    fig.add_trace(
        go.Bar(
            x=REGIME_LABELS,
            y=list(stationary_dist),
            marker_color=ACCENT,
            opacity=0.85,
            text=["{:.2f}".format(v) for v in stationary_dist],
            textposition="outside",
            textfont=dict(color=TEXT),
            hovertemplate="%{x}: %{y:.2f}<extra></extra>",
        ),
        row=2, col=1,
    )

    fig.update_layout(
        title=dict(
            text="Regime Transition Probabilities (Markov)",
            font=dict(color=TEXT, size=16),
        ),
        paper_bgcolor=BG,
        plot_bgcolor=BG,
        font=dict(color=TEXT),
        showlegend=False,
        autosize=True,
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    for ann in fig.layout.annotations:
        ann.font = dict(color=TEXT, size=12)

    return fig


# =============================================================================
#  7. CHARTS PER PHASE
# =============================================================================

def build_challenge_charts(p1):
    b = P1_BALANCE
    fan = equity_fan(
        p1["equity_curves"],
        title="Phase 1 — Challenge Equity Fan",
        xaxis_title="Trading Day #",
        ref_lines=[
            (b,                          "rgba(255,255,255,0.9)", "dash", "Start: $" + str(int(b))),
            (b * (1 + P1_PROFIT_TARGET), GREEN,                  "dash", "Target +" + str(int(P1_PROFIT_TARGET * 100)) + "%: $" + str(int(b * (1 + P1_PROFIT_TARGET)))),
            (b * (1 - P1_MAX_TOTAL_DD),  RED,                    "dot",  "Max Loss floor: $" + str(int(b * (1 - P1_MAX_TOTAL_DD)))),
        ],
        daily_loss_abs = P1_BALANCE * P1_MAX_DAILY_DD,
        breach_mask    = ~p1["results_df"]["passed"].values,
    )
    donut = pass_donut(p1["pass_rate"], p1["n_passed"], p1["n_failed"],
                       "Challenge Pass Rate")
    fails = fail_reasons_bar(
        p1["fail_pcts"],
        {"daily_dd": "Daily Loss Exceeded", "total_dd": "Max Loss Exceeded"},
        "Challenge — Fail Reasons",
    )
    hist = days_histogram(p1["days_dist"], p1["avg_days"],
                          "Challenge — Days to Pass", P1_MIN_DAYS)
    kpi = kpi_table(
        ["Account", "Leverage", "Profit Target", "Max Daily Loss", "Max Loss", "Min Days", "Pass Rate", "Avg Days"],
        [
            "$" + str(int(P1_BALANCE)),
            str(P1_LEVERAGE) + "x",
            str(int(P1_PROFIT_TARGET * 100)) + "%  ($" + str(int(P1_BALANCE * P1_PROFIT_TARGET)) + ")",
            str(int(P1_MAX_DAILY_DD   * 100)) + "%  ($" + str(int(P1_BALANCE * P1_MAX_DAILY_DD)) + ") — fixed $, daily reset",
            str(int(P1_MAX_TOTAL_DD   * 100)) + "%  ($" + str(int(P1_BALANCE * P1_MAX_TOTAL_DD)) + ") — fixed floor",
            str(P1_MIN_DAYS),
            str(round(p1["pass_rate"], 2)) + "%",
            str(round(p1["avg_days"],  1)) + "d" if p1["avg_days"] > 0 else "N/A",
        ],
        [TEXT, ACCENT, TEXT, TEXT, TEXT, TEXT,
         GREEN if p1["pass_rate"] >= 50 else RED, ACCENT],
    )
    return kpi, fan, donut, fails, hist


def build_verification_charts(p2):
    b = P2_BALANCE
    fan = equity_fan(
        p2["equity_curves"],
        title="Phase 2 — Verification Equity Fan",
        xaxis_title="Trading Day #",
        ref_lines=[
            (b,                          "rgba(255,255,255,0.9)", "dash", "Start: $" + str(int(b))),
            (b * (1 + P2_PROFIT_TARGET), GREEN,                  "dash", "Target +" + str(int(P2_PROFIT_TARGET * 100)) + "%: $" + str(int(b * (1 + P2_PROFIT_TARGET)))),
            (b * (1 - P2_MAX_TOTAL_DD),  RED,                    "dot",  "Max Loss floor: $" + str(int(b * (1 - P2_MAX_TOTAL_DD)))),
        ],
        daily_loss_abs = P2_BALANCE * P2_MAX_DAILY_DD,
        breach_mask    = ~p2["results_df"]["passed"].values,
    )
    donut = pass_donut(p2["pass_rate"], p2["n_passed"], p2["n_failed"],
                       "Verification Pass Rate  (of P1 passers)")
    fails = fail_reasons_bar(
        p2["fail_pcts"],
        {"daily_dd": "Daily Loss Exceeded", "total_dd": "Max Loss Exceeded"},
        "Verification — Fail Reasons",
    )
    hist = days_histogram(p2["days_dist"], p2["avg_days"],
                          "Verification — Days to Pass", P2_MIN_DAYS)

    n_total = N_SIMULATIONS
    n_p1    = p2.get("n_p1_passed", 0)
    n_p2    = p2["n_passed"]
    funnel_text = [
        str(n_total) + "  (100%)",
        str(n_p1) + "  (" + str(round(n_p1 / n_total * 100, 1)) + "% of total)",
        str(n_p2) + "  (" + str(round(n_p2 / n_total * 100, 1)) + "% of total)",
    ]
    funnel  = go.Figure(go.Funnel(
        y=["Started Challenge", "Passed Challenge", "Passed Verification (Funded)"],
        x=[n_total, n_p1, n_p2],
        text=funnel_text,
        textinfo="text",
        textfont=dict(color=TEXT, size=13),
        marker=dict(color=[ACCENT, ORANGE, GREEN]),
    ))
    funnel.update_layout(
        **dark_layout("Evaluation Funnel  (" + str(n_total) + " simulations)"),
        margin=dict(l=220),
    )

    kpi = kpi_table(
        ["Account", "Leverage", "Profit Target", "Max Daily Loss", "Max Loss", "Min Days", "Pass Rate (P1)", "Combined Rate"],
        [
            "$" + str(int(P2_BALANCE)),
            str(P2_LEVERAGE) + "x",
            str(int(P2_PROFIT_TARGET * 100)) + "%  ($" + str(int(P2_BALANCE * P2_PROFIT_TARGET)) + ")",
            str(int(P2_MAX_DAILY_DD   * 100)) + "%  ($" + str(int(P2_BALANCE * P2_MAX_DAILY_DD)) + ") — fixed $, daily reset",
            str(int(P2_MAX_TOTAL_DD   * 100)) + "%  ($" + str(int(P2_BALANCE * P2_MAX_TOTAL_DD)) + ") — fixed floor",
            str(P2_MIN_DAYS),
            str(round(p2["pass_rate"],              2)) + "%",
            str(round(p2.get("combined_pass_rate", 0), 2)) + "%",
        ],
        [TEXT, ACCENT, TEXT, TEXT, TEXT, TEXT,
         GREEN if p2["pass_rate"] >= 50 else RED,
         GREEN if p2.get("combined_pass_rate", 0) >= 20 else ORANGE],
    )
    return kpi, fan, donut, fails, hist, funnel


def build_funded_charts(fd):
    curves = fd["equity_curves"]
    floors = fd["floor_curves"]
    df_res = fd["results_df"]

    # equity fan with fixed floor overlay
    x    = list(range(curves.shape[1]))
    pcts = {p: np.percentile(curves, p, axis=0) for p in CONFIDENCE_LEVELS}

    fig_fan = go.Figure()
    rng        = np.random.default_rng(RANDOM_SEED)
    sample_idx = rng.choice(len(curves),
                             size=min(N_DISPLAY_CURVES, len(curves)),
                             replace=False)
    for i, idx in enumerate(sample_idx):
        fig_fan.add_trace(go.Scatter(
            x=x, y=list(curves[idx]),
            mode="lines", line=dict(color="rgba(88,166,255,0.15)", width=1.0),
            name="Simulations" if i == 0 else None,
            legendgroup="sims", showlegend=(i == 0),
            visible=False, hoverinfo="skip",
        ))
    n_sim_traces = len(sample_idx)

    for (lo, hi), alpha in [((5, 95), 0.10), ((25, 75), 0.20)]:
        fig_fan.add_trace(go.Scatter(
            x=x + x[::-1],
            y=list(pcts[hi]) + list(pcts[lo])[::-1],
            fill="toself", fillcolor="rgba(88,166,255," + str(alpha) + ")",
            line=dict(color="rgba(0,0,0,0)"),
            name="P" + str(lo) + "-P" + str(hi), hoverinfo="skip",
        ))

    clr = {5: RED, 25: ORANGE, 50: ACCENT, 75: ORANGE, 95: GREEN}
    dsh = {5: "dot", 25: "dash", 50: "solid", 75: "dash", 95: "dot"}
    for p in CONFIDENCE_LEVELS:
        fig_fan.add_trace(go.Scatter(
            x=x, y=list(pcts[p]), mode="lines",
            name="P" + str(p),
            line=dict(color=clr[p], dash=dsh[p], width=1.5),
        ))

    # fixed floor line
    floor_level = FD_BALANCE * (1.0 - FD_MAX_TOTAL_DD)
    fig_fan.add_hline(
        y=floor_level, line_dash="dot", line_color=RED, line_width=1.5,
        annotation_text="Max Loss floor: $" + str(int(floor_level)),
        annotation_font_color=RED,
    )
    fig_fan.add_hline(
        y=FD_BALANCE, line_dash="dash", line_color="rgba(255,255,255,0.7)", line_width=1.0,
        annotation_text="Start: $" + str(int(FD_BALANCE)),
        annotation_font_color="rgba(255,255,255,0.7)",
    )

    sim_idx_list = list(range(n_sim_traces))
    fig_fan.update_layout(
        **dark_layout("Funded — Equity Fan"),
        xaxis_title="Trading Day #",
        yaxis_title="Account Equity ($)",
        yaxis_rangemode="nonnegative",
        hovermode="x unified",
        updatemenus=[dict(
            type="buttons", direction="left",
            x=0.01, y=1.08, xanchor="left",
            buttons=[
                dict(label="Show Sims", method="restyle",
                     args=[{"visible": True},  sim_idx_list]),
                dict(label="Hide Sims", method="restyle",
                     args=[{"visible": False}, sim_idx_list]),
            ],
            bgcolor=GRID, bordercolor=ACCENT,
            font=dict(color=TEXT, size=11),
            pad=dict(l=4, r=4, t=4, b=4),
        )],
    )

    # survival curve
    days_x   = list(range(FD_MAX_SIM_DAYS + 1))
    fig_surv = go.Figure()
    fig_surv.add_trace(go.Scatter(
        x=days_x, y=list(fd["survival"] * 100),
        mode="lines", fill="tozeroy",
        line=dict(color=GREEN, width=2),
        fillcolor="rgba(63,185,80,0.15)",
        hovertemplate="Day %{x}: %{y:.1f}% surviving<extra></extra>",
    ))
    fig_surv.add_hline(y=50, line_dash="dash", line_color=ORANGE,
                       annotation_text="50% survival", annotation_font_color=ORANGE)
    fig_surv.update_layout(**dark_layout("Funded — Survival Curve"),
                           xaxis_title="Trading Day #",
                           yaxis_title="% of Accounts Still Active")
    fig_surv.update_yaxes(range=[0, 105], gridcolor=GRID)

    # breach / no breach donut
    fig_donut = go.Figure(go.Pie(
        labels=["No Breach", "Breached"],
        values=[max(100 - fd["breach_rate"], 0.0001), max(fd["breach_rate"], 0.0001)],
        hole=0.65,
        marker=dict(colors=[GREEN, RED]),
        textinfo="label+percent",
        textfont=dict(color=TEXT, size=13),
    ))
    fig_donut.add_annotation(text=str(round(fd["breach_rate"], 1)) + "%",
                              x=0.5, y=0.56, showarrow=False,
                              font=dict(size=28, color=RED if fd["breach_rate"] > 50 else GREEN))
    fig_donut.add_annotation(text="Breach Rate", x=0.5, y=0.43, showarrow=False,
                              font=dict(size=13, color=TEXT))
    fig_donut.update_layout(**dark_layout("Funded — Breach Rate"),
                             showlegend=True, margin=dict(l=60, r=60, t=60, b=20))

    # earnings distribution
    earn_vals = df_res["total_earnings"].values
    fig_earn  = go.Figure()
    fig_earn.add_trace(go.Histogram(
        x=earn_vals, nbinsx=50,
        marker_color=GREEN, opacity=0.75,
        hovertemplate="$%{x:.0f}: %{y} sims<extra></extra>",
    ))
    fig_earn.add_vline(x=fd["avg_total_earnings"], line_dash="dash", line_color=ACCENT,
                       annotation_text="Avg: $" + str(round(fd["avg_total_earnings"], 0)),
                       annotation_font_color=ACCENT)
    fig_earn.update_layout(**dark_layout("Funded — Total Earnings Distribution"),
                            xaxis_title="Total Earnings ($)", yaxis_title="Count")

    # payout count distribution
    pc_vals, pc_counts = np.unique(df_res["payout_count"].values.astype(int),
                                   return_counts=True)
    fig_pc = go.Figure(go.Bar(
        x=pc_vals, y=pc_counts / pc_counts.sum() * 100,
        marker_color=ACCENT, opacity=0.8,
        hovertemplate="%{x} payouts: %{y:.1f}%<extra></extra>",
    ))
    fig_pc.update_layout(**dark_layout("Funded — Payout Count Distribution"),
                          xaxis_title="Number of Payouts", yaxis_title="Probability (%)",
                          bargap=0.2)

    # breach reasons bar
    fig_br = fail_reasons_bar(
        fd["breach_pcts"],
        {"daily_dd": "Daily Loss Exceeded", "total_dd": "Max Loss Exceeded"},
        "Funded — Breach Reasons",
    )

    # breach day histogram
    breach_days = df_res["breach_day"].dropna().values.astype(int)
    if len(breach_days) > 0:
        bvals, bcounts = np.unique(breach_days, return_counts=True)
        fig_bt = go.Figure(go.Bar(
            x=bvals, y=bcounts / len(df_res) * 100,
            marker_color=RED, opacity=0.75,
            hovertemplate="Day %{x}: %{y:.1f}%<extra></extra>",
        ))
        fig_bt.add_vline(x=breach_days.mean(), line_dash="dash", line_color=ORANGE,
                         annotation_text="Avg: " + str(round(breach_days.mean(), 1)) + "d",
                         annotation_font_color=ORANGE)
    else:
        fig_bt = go.Figure()
        fig_bt.add_annotation(text="No breaches", x=0.5, y=0.5, showarrow=False,
                               font=dict(color=GREEN, size=18))
    fig_bt.update_layout(**dark_layout("Funded — Breach Day Distribution"),
                          xaxis_title="Trading Day of Breach", yaxis_title="% of All Sims")

    # earnings by number of payouts
    _pc = df_res["payout_count"].values
    _cd = (_pc * FD_PAYOUT_SCHEDULE).astype(int)   # approx trading days
    _max_pc = int(_pc.max()) if len(_pc) else 0
    _afpd   = fd.get("avg_first_payout_day") or FD_PAYOUT_SCHEDULE
    _tickvals = list(range(0, _max_pc + 1))
    _ticktext = ["0"] + [
        f"{n}<br>({round(_afpd + (n - 1) * FD_PAYOUT_SCHEDULE)}d)"
        for n in range(1, _max_pc + 1)
    ]
    fig_ed = go.Figure()
    fig_ed.add_trace(go.Scatter(
        x=list(_pc),
        y=list(df_res["total_earnings"]),
        customdata=list(_cd),
        mode="markers",
        marker=dict(color=ACCENT, size=3, opacity=0.3),
        hovertemplate="Payouts: %{x} (~%{customdata}d) — $%{y:.0f}<extra></extra>",
    ))
    _ed_layout = dark_layout("Funded — Earnings vs Number of Payouts")
    _ed_layout["xaxis"].update(
        title="Number of Payouts",
        tickvals=_tickvals,
        ticktext=_ticktext,
    )
    fig_ed.update_layout(**_ed_layout, yaxis_title="Total Earnings ($)")

    # avg payout day histogram
    fpd_vals = df_res["first_payout_day"].dropna().values
    if len(fpd_vals) > 0:
        fig_apd = go.Figure(go.Histogram(
            x=fpd_vals, nbinsx=40,
            marker_color=PURPLE, opacity=0.75,
            hovertemplate="Day %{x}: %{y} sims<extra></extra>",
        ))
        fig_apd.add_vline(x=fpd_vals.mean(), line_dash="dash", line_color=ACCENT,
                          annotation_text="Avg: " + str(round(fpd_vals.mean(), 1)) + "d",
                          annotation_font_color=ACCENT)
    else:
        fig_apd = go.Figure()
        fig_apd.add_annotation(text="No payouts", x=0.5, y=0.5, showarrow=False,
                                font=dict(color=RED, size=18))
    fig_apd.update_layout(**dark_layout("Funded — Days to First Payout"),
                           xaxis_title="Trading Day", yaxis_title="Count")

    kpi = kpi_table(
        ["Account", "Leverage", "Max Daily Loss", "Max Loss",
         "Profit Split", "Payout Mode",
         "≥1 Payout Rate", "Breach Rate",
         "Avg Total Earnings", "Avg Days Active",
         "Avg Payouts", "Avg First Payout Day"],
        [
            "$" + str(int(FD_BALANCE)),
            str(FD_LEVERAGE) + "x",
            str(int(FD_MAX_DAILY_DD * 100)) + "%  ($" + str(int(FD_BALANCE * FD_MAX_DAILY_DD)) + ")",
            str(int(FD_MAX_TOTAL_DD * 100)) + "%  ($" + str(int(FD_BALANCE * FD_MAX_TOTAL_DD)) + ")",
            str(int(FD_PROFIT_SPLIT * 100)) + "%",
            FD_PAYOUT_MODE + " @ " + str(int(FD_PAYOUT_THRESHOLD * 100)) + "% or " + str(FD_PAYOUT_SCHEDULE) + "d",
            str(round(fd["payout_rate"],       2)) + "%",
            str(round(fd["breach_rate"],        2)) + "%",
            "$" + str(round(fd["avg_total_earnings"], 0)),
            str(round(fd["avg_days_to_earn"],   1)) + "d",
            str(round(fd["avg_payout_count"],   2)),
            str(round(fd["avg_first_payout_day"], 1)) + "d" if fd["avg_first_payout_day"] > 0 else "N/A",
        ],
        [TEXT, ACCENT, ACCENT, TEXT, TEXT, TEXT, TEXT,
         GREEN if fd["payout_rate"] >= 50 else RED,
         RED   if fd["breach_rate"] > 50  else ORANGE,
         GREEN if fd["avg_total_earnings"] > 0 else RED,
         ACCENT, ACCENT, ACCENT],
        height=130,
    )
    return kpi, fig_fan, fig_surv, fig_donut, fig_earn, fig_pc, fig_bt, fig_br, fig_ed, fig_apd


# =============================================================================
#  7b. LONG-TERM MONTE CARLO
# =============================================================================

def simulate_longterm(daily_pnl, n_sims=LT_SIMS, n_days=LT_DAYS,
                      starting_balance=10_000, ruin_pct=LT_RUIN_PCT,
                      trans_matrix=None, regime_pnl_pools=None):
    """
    Monte Carlo over n_days steps per path.

    Returns
    -------
    dict with keys:
        equity_paths : list of lists (each len n_days+1)
        max_dd       : list of floats (per-path max drawdown fraction)
        final_equity : list of floats
        sharpe       : list of floats
        pass_rate    : float (fraction of paths not reaching ruin)
    """
    use_markov = (trans_matrix is not None and regime_pnl_pools is not None)
    rng        = np.random.default_rng(RANDOM_SEED + 99)

    ruin_floor = starting_balance * (1.0 - ruin_pct)

    equity_paths = []
    max_dd_list  = []
    final_eq     = []
    sharpe_list  = []

    _report_every_lt = max(1, n_sims // 50)
    for _li in range(n_sims):
        if _li % _report_every_lt == 0:
            print(f"MC_SIM {_li}/{n_sims} Long-term", flush=True)
        equity        = starting_balance
        path          = [equity]
        peak          = equity
        daily_returns = []

        if use_markov:
            cur_regime = int(rng.integers(0, 5))

        ruined = False
        for _d in range(n_days):
            if use_markov:
                cur_regime = int(rng.choice(5, p=trans_matrix[cur_regime]))
                pool = regime_pnl_pools.get(cur_regime, [])
                if len(pool) >= 5:
                    pnl = float(rng.choice(pool))
                else:
                    pnl = float(rng.choice(daily_pnl))
            else:
                pnl = float(rng.choice(daily_pnl))

            ret    = pnl / equity if equity > 0 else 0.0
            equity = max(equity + pnl, 0.0)
            daily_returns.append(ret)
            path.append(equity)
            if equity > peak:
                peak = equity
            if equity <= ruin_floor:
                ruined = True
                # pad path to n_days+1
                path += [equity] * (n_days - len(path) + 1)
                break

        # max drawdown fraction
        peak_so_far = starting_balance
        max_dd      = 0.0
        for v in path:
            if v > peak_so_far:
                peak_so_far = v
            dd = (peak_so_far - v) / peak_so_far if peak_so_far > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # Sharpe: annualised
        dr = np.array(daily_returns)
        if len(dr) > 1 and dr.std() > 0:
            sharpe = float(dr.mean() / dr.std() * np.sqrt(252))
        else:
            sharpe = 0.0

        equity_paths.append(path)
        max_dd_list.append(max_dd)
        final_eq.append(path[-1])
        sharpe_list.append(sharpe)

    pass_rate = float(np.mean([e > ruin_floor for e in final_eq]))

    return {
        "equity_paths": equity_paths,
        "max_dd"      : max_dd_list,
        "final_equity": final_eq,
        "sharpe"      : sharpe_list,
        "pass_rate"   : pass_rate,
    }


# Common display-names -> Yahoo Finance ticker symbols
_TICKER_ALIASES = {
    "s&p 500": "^GSPC", "s&p500": "^GSPC", "sp500": "^GSPC",
    "spx": "^GSPC", "spy": "SPY",
    "nasdaq": "^IXIC", "nasdaq 100": "^NDX", "nasdaq100": "^NDX",
    "ndx": "^NDX", "qqq": "QQQ",
    "dow": "^DJI", "djia": "^DJI", "dow jones": "^DJI",
    "gold": "GLD", "xauusd": "GLD", "gld": "GLD",
    "silver": "SLV", "xagusd": "SLV",
    "oil": "USO", "crude": "USO", "wti": "USO",
    "btc": "BTC-USD", "bitcoin": "BTC-USD", "btcusd": "BTC-USD",
    "eth": "ETH-USD", "ethereum": "ETH-USD",
    "euro": "EURUSD=X", "eurusd": "EURUSD=X",
    "gbpusd": "GBPUSD=X", "usdjpy": "JPY=X",
    "vix": "^VIX",
}


def _resolve_ticker(raw: str) -> str:
    """Map common display names to Yahoo Finance symbols; pass through unknowns."""
    return _TICKER_ALIASES.get(raw.strip().lower(), raw.strip())


def _fetch_benchmark(ticker, n_days, starting_balance):
    """
    Fetch the last *n_days* trading-day closes for *ticker* from Yahoo Finance,
    normalize to *starting_balance*, and return (equity_list, final_equity).
    Returns (None, None) on any failure.
    """
    resolved = _resolve_ticker(ticker)
    if resolved != ticker:
        print(f"[INFO] Benchmark: '{ticker}' resolved to Yahoo Finance ticker '{resolved}'", flush=True)
    try:
        import yfinance as yf
        import datetime as _dt
        # Request extra calendar days to guarantee we get n_days trading days
        calendar_days = int(n_days * 1.5) + 60
        start = str(_dt.date.today() - _dt.timedelta(days=calendar_days))
        end   = str(_dt.date.today())
        df_bm = yf.download(resolved, start=start, end=end,
                            progress=False, auto_adjust=True)
        if df_bm is None or len(df_bm) < 10:
            print(
                f"[WARN] Benchmark '{resolved}': too few rows returned "
                f"({len(df_bm) if df_bm is not None else 0}). "
                f"Use a Yahoo Finance ticker symbol (e.g. ^GSPC, SPY, GLD, QQQ).",
                flush=True,
            )
            return None, None
        # Extract Close — handle both flat and multi-level column DataFrames
        # (yfinance ≥0.2 returns multi-level columns when group_by='ticker')
        close_col = df_bm["Close"]
        if hasattr(close_col, "ndim") and close_col.ndim > 1:
            close_col = close_col.iloc[:, 0]   # first (only) ticker column
        closes = close_col.dropna().to_numpy().flatten()
        closes = closes[-n_days:] if len(closes) >= n_days else closes
        daily_rets = np.diff(closes) / closes[:-1]
        equity = [starting_balance]
        for r in daily_rets:
            equity.append(equity[-1] * (1.0 + float(r)))
        print(f"[INFO] Benchmark '{resolved}': {len(closes)} trading days fetched.", flush=True)
        return equity, equity[-1]
    except ImportError:
        print("[WARN] yfinance not installed — run: pip install yfinance", flush=True)
        return None, None
    except Exception as exc:
        print(f"[WARN] Benchmark fetch failed for '{resolved}': {exc}", flush=True)
        return None, None


def build_longterm_charts(lt_results, starting_balance=10_000, benchmark_ticker=""):
    """
    Returns list of go.Figure:
      [0] equity fan (percentile bands) — includes buy&hold line if ticker provided
      [1] max DD% histogram
      [2] KPI table (median final equity, P(ruin), median Sharpe, [benchmark ratio])
    """
    paths_arr = np.array(lt_results["equity_paths"])
    x         = list(range(paths_arr.shape[1]))
    pcts      = {p: np.percentile(paths_arr, p, axis=0) for p in CONFIDENCE_LEVELS}

    # ---- equity fan ----
    fig_fan = go.Figure()
    rng        = np.random.default_rng(RANDOM_SEED)
    sample_idx = rng.choice(len(paths_arr),
                             size=min(N_DISPLAY_CURVES, len(paths_arr)),
                             replace=False)
    for i, idx in enumerate(sample_idx):
        fig_fan.add_trace(go.Scatter(
            x=x, y=list(paths_arr[idx]),
            mode="lines",
            line=dict(color="rgba(88,166,255,0.12)", width=0.8),
            name="Simulations" if i == 0 else None,
            legendgroup="sims", showlegend=(i == 0),
            visible=False, hoverinfo="skip",
        ))
    n_sim_tr = len(sample_idx)

    for (lo, hi), alpha in [((5, 95), 0.10), ((25, 75), 0.20)]:
        fig_fan.add_trace(go.Scatter(
            x=x + x[::-1],
            y=list(pcts[hi]) + list(pcts[lo])[::-1],
            fill="toself",
            fillcolor="rgba(88,166,255," + str(alpha) + ")",
            line=dict(color="rgba(0,0,0,0)"),
            name="P" + str(lo) + "-P" + str(hi),
            hoverinfo="skip",
        ))
    clr = {5: RED, 25: ORANGE, 50: ACCENT, 75: ORANGE, 95: GREEN}
    dsh = {5: "dot", 25: "dash", 50: "solid", 75: "dash", 95: "dot"}
    for p in CONFIDENCE_LEVELS:
        fig_fan.add_trace(go.Scatter(
            x=x, y=list(pcts[p]), mode="lines",
            name="P" + str(p),
            line=dict(color=clr[p], dash=dsh[p], width=1.5),
        ))
    fig_fan.add_hline(y=starting_balance,
                      line_dash="dash", line_color="rgba(255,255,255,0.7)",
                      annotation_text="Start: $" + str(int(starting_balance)),
                      annotation_font_color="rgba(255,255,255,0.7)")
    ruin_floor = starting_balance * (1.0 - LT_RUIN_PCT)
    fig_fan.add_hline(y=ruin_floor,
                      line_dash="dot", line_color=RED,
                      annotation_text="Ruin floor: $" + str(int(ruin_floor)),
                      annotation_font_color=RED)
    # ---- optional buy & hold benchmark line ----
    bm_equity    = None
    bm_final     = None
    bm_ratio_val = None
    bm_ratio_str = "N/A"
    bm_return_str = "N/A"
    if benchmark_ticker:
        bm_equity, bm_final = _fetch_benchmark(benchmark_ticker, LT_DAYS, starting_balance)
        if bm_equity is not None:
            bm_x = list(range(len(bm_equity)))
            fig_fan.add_trace(go.Scatter(
                x=bm_x, y=bm_equity,
                mode="lines",
                name=f"Buy & Hold {benchmark_ticker}",
                line=dict(color="#f7c948", width=2.5, dash="solid"),
                hovertemplate=f"B&H {benchmark_ticker}: $%{{y:,.0f}}<extra></extra>",
            ))
            # Compute ratio: strategy_return / bm_return (absolute)
            med_final   = float(np.median(lt_results["final_equity"]))
            strat_ret   = (med_final  - starting_balance) / starting_balance
            bm_ret      = (bm_final   - starting_balance) / starting_balance
            bm_return_str = f"{bm_ret * 100:.1f}%"
            if abs(bm_ret) > 0.001:
                bm_ratio_val = strat_ret / bm_ret
                color_hint   = "up" if bm_ratio_val >= 1.0 else "down"
                bm_ratio_str = f"{bm_ratio_val:.2f}x  ({'strategy ahead' if bm_ratio_val >= 1.0 else 'benchmark ahead'})"
            else:
                bm_ratio_str = "N/A (benchmark flat)"

    sim_idx_list = list(range(n_sim_tr))
    fig_fan.update_layout(
        **dark_layout("Long-term — " + str(LT_DAYS) + "-Day Equity Fan (" + str(LT_SIMS) + " sims)"),
        xaxis_title="Trading Day #",
        yaxis_title="Account Equity ($)",
        yaxis_rangemode="nonnegative",
        hovermode="x unified",
        updatemenus=[dict(
            type="buttons", direction="left",
            x=0.01, y=1.08, xanchor="left",
            buttons=[
                dict(label="Show Sims", method="restyle",
                     args=[{"visible": True},  sim_idx_list]),
                dict(label="Hide Sims", method="restyle",
                     args=[{"visible": False}, sim_idx_list]),
            ],
            bgcolor=GRID, bordercolor=ACCENT,
            font=dict(color=TEXT, size=11),
            pad=dict(l=4, r=4, t=4, b=4),
        )],
    )

    # ---- max DD histogram ----
    dd_pct = [v * 100 for v in lt_results["max_dd"]]
    fig_dd = go.Figure()
    fig_dd.add_trace(go.Histogram(
        x=dd_pct, nbinsx=50,
        marker_color=RED, opacity=0.75,
        hovertemplate="%{x:.1f}%: %{y} sims<extra></extra>",
    ))
    med_dd = float(np.median(dd_pct))
    fig_dd.add_vline(x=med_dd, line_dash="dash", line_color=ACCENT,
                     annotation_text="Median: " + str(round(med_dd, 1)) + "%",
                     annotation_font_color=ACCENT)
    fig_dd.update_layout(**dark_layout("Long-term — Max Drawdown % Distribution"),
                          xaxis_title="Max Drawdown (%)", yaxis_title="Count")

    # ---- KPI table ----
    med_eq    = float(np.median(lt_results["final_equity"]))
    p_ruin    = 1.0 - lt_results["pass_rate"]
    med_sharp = float(np.median(lt_results["sharpe"]))

    kpi_labels = ["Horizon", "Simulations", "Median Final Equity",
                  "P(Ruin < " + str(int(LT_RUIN_PCT * 100)) + "%)", "Median Sharpe"]
    kpi_values = [
        str(LT_DAYS) + " trading days (~1 year)",
        str(LT_SIMS),
        "$" + str(round(med_eq, 0)),
        str(round(p_ruin * 100, 2)) + "%",
        str(round(med_sharp, 2)),
    ]
    kpi_colors = [
        TEXT, ACCENT,
        GREEN if med_eq >= starting_balance else RED,
        RED if p_ruin > 0.10 else GREEN,
        GREEN if med_sharp > 1.0 else (ORANGE if med_sharp > 0 else RED),
    ]
    kpi_height = 110

    if benchmark_ticker and bm_equity is not None:
        kpi_labels += [f"B&H {benchmark_ticker} Return", "Strategy vs B&H Ratio"]
        kpi_values += [bm_return_str, bm_ratio_str]
        kpi_height  = 155
        ratio_color = GREEN if (bm_ratio_val is not None and bm_ratio_val >= 1.0) else ORANGE
        kpi_colors += [ACCENT, ratio_color]

    kpi = kpi_table(kpi_labels, kpi_values, kpi_colors, height=kpi_height)

    return [fig_fan, fig_dd, kpi]


# =============================================================================
#  8. TABBED HTML DASHBOARD
# =============================================================================

def build_dashboard(p1, p2, fd, lt_results=None,
                    trans_matrix=None, stationary_dist=None,
                    benchmark_ticker=""):
    p1_kpi, p1_fan, p1_donut, p1_fails, p1_hist                    = build_challenge_charts(p1)
    p2_kpi, p2_fan, p2_donut, p2_fails, p2_hist, p2_funnel          = build_verification_charts(p2)
    fd_kpi, fd_fan, fd_surv, fd_donut, fd_earn, fd_pc, fd_bt, fd_br, fd_ed, fd_apd = build_funded_charts(fd)

    def fh(fig):
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def card(fig, cls="card"):
        return '<div class="' + cls + '">' + fh(fig) + "</div>"

    # Regime heatmap — only rendered when Markov data is available
    regime_heatmap_html = ""
    if trans_matrix is not None and stationary_dist is not None:
        fig_hm = plot_regime_heatmap(trans_matrix, stationary_dist)
        regime_heatmap_html = (
            '<div class="grid">'
            + card(fig_hm, "card full")
            + "</div>"
        )

    p1_body = (
        '<div class="kpi">' + fh(p1_kpi) + "</div>"
        + '<div class="grid">'
        + card(p1_fan,   "card full")
        + card(p1_donut)
        + card(p1_fails)
        + card(p1_hist,  "card full")
        + "</div>"
        + regime_heatmap_html
    )

    p2_body = (
        '<div class="kpi">' + fh(p2_kpi) + "</div>"
        + '<div class="grid">'
        + card(p2_fan,    "card full")
        + card(p2_donut)
        + card(p2_fails)
        + card(p2_hist,   "card full")
        + card(p2_funnel, "card full")
        + "</div>"
    )

    fd_body = (
        '<div class="kpi">' + fh(fd_kpi) + "</div>"
        + '<div class="grid">'
        + card(fd_fan,   "card full")
        + card(fd_surv,  "card full")
        + card(fd_donut)
        + card(fd_earn)
        + card(fd_ed,    "card full")
        + "</div>"
        + '<div class="grid3">'
        + card(fd_pc)
        + card(fd_apd)
        + card(fd_bt)
        + "</div>"
        + '<div class="grid3">'
        + card(fd_br)
        + "</div>"
    )

    # Tab 4: Long-term
    if lt_results is not None:
        lt_figs = build_longterm_charts(lt_results, starting_balance=FD_BALANCE,
                                        benchmark_ticker=benchmark_ticker)
        lt_fan, lt_dd, lt_kpi = lt_figs
        lt_body = (
            '<div class="kpi">' + fh(lt_kpi) + "</div>"
            + '<div class="grid">'
            + card(lt_fan, "card full")
            + card(lt_dd,  "card full")
            + "</div>"
        )
        lt_tab_html  = "  <div class=\"tab\" onclick=\"showTab('lt',this)\">Long-term</div>\n"
        lt_panel_html = "<div id=\"lt\" class=\"panel\">" + lt_body + "</div>\n"
    else:
        lt_tab_html   = ""
        lt_panel_html = ""

    source_label = "MT5 Strategy Tester" if DATA_SOURCE == "mt5_html" else "TradingView"

    html = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"UTF-8\">\n"
        "<title>FTMO 2-Step Simulator</title>\n"
        "<script src=\"https://cdn.plot.ly/plotly-2.30.0.min.js\"></script>\n"
        "<style>\n"
        "* { box-sizing:border-box; margin:0; padding:0; }\n"
        "body { background:#0d1117; font-family:'Segoe UI',sans-serif; color:#c9d1d9; }\n"
        ".header { padding:24px 24px 8px; border-bottom:1px solid #21262d; }\n"
        ".header h1 { font-size:1.5rem; color:#c9d1d9; }\n"
        ".header p  { color:#8b949e; font-size:.85rem; margin-top:6px; }\n"
        ".note { background:#1c2128; border-left:3px solid #d29922; padding:10px 16px;\n"
        "        margin:12px 24px 0; border-radius:4px; font-size:.82rem; color:#8b949e; }\n"
        ".note strong { color:#d29922; }\n"
        ".tabs { display:flex; gap:4px; padding:16px 24px 0; border-bottom:1px solid #21262d; }\n"
        ".tab  { padding:8px 22px; border-radius:6px 6px 0 0; cursor:pointer;\n"
        "        background:#161b22; color:#8b949e; font-size:.92rem;\n"
        "        border:1px solid #21262d; border-bottom:none; user-select:none; }\n"
        ".tab.active { background:#21262d; color:#c9d1d9; border-color:#30363d; }\n"
        ".tab:hover  { background:#21262d; color:#c9d1d9; }\n"
        ".panel { display:none; padding:16px 20px; }\n"
        ".panel.active { display:block; }\n"
        ".grid  { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px; }\n"
        ".grid3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:12px; }\n"
        ".full  { grid-column:1/-1; }\n"
        ".card  { background:#161b22; border:1px solid #21262d; border-radius:8px; padding:4px; overflow:hidden; }\n"
        ".kpi   { margin-bottom:12px; overflow-x:auto; }\n"
        "</style>\n</head>\n<body>\n"

        "<div class=\"header\">\n"
        "  <h1>FTMO 2-Step Evaluation Simulator</h1>\n"
        "  <p>" + str(N_SIMULATIONS) + " simulations per phase &nbsp;&middot;&nbsp; "
        "Bootstrap resampling from " + source_label + " backtest trades</p>\n"
        "</div>\n"

        "<div class=\"note\">"
        "<strong>&#9888; Simulation Limitation:</strong> "
        "Daily loss is approximated from end-of-day aggregated P&amp;L measured from each day's "
        "opening balance. Intraday floating drawdown on positions held overnight (which FTMO "
        "checks in real-time) is <em>not captured</em>. Results will slightly "
        "<strong>underestimate breach probability</strong> vs live trading. "
        "Commission and swap impact on the daily loss limit is also not modelled from the backtest data."
        "</div>\n"

        "<div class=\"tabs\">\n"
        "  <div class=\"tab active\" onclick=\"showTab('p1',this)\">Phase 1 &#8212; Challenge</div>\n"
        "  <div class=\"tab\"        onclick=\"showTab('p2',this)\">Phase 2 &#8212; Verification</div>\n"
        "  <div class=\"tab\"        onclick=\"showTab('fd',this)\">Funded Account</div>\n"
        + lt_tab_html +
        "</div>\n"

        "<div id=\"p1\" class=\"panel active\">" + p1_body + "</div>\n"
        "<div id=\"p2\" class=\"panel\">"        + p2_body + "</div>\n"
        "<div id=\"fd\" class=\"panel\">"        + fd_body + "</div>\n"
        + lt_panel_html +

        "<script>\n"
        "function showTab(id,el){\n"
        "  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));\n"
        "  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));\n"
        "  document.getElementById(id).classList.add('active');\n"
        "  el.classList.add('active');\n"
        "  setTimeout(function(){\n"
        "    document.getElementById(id).querySelectorAll('.js-plotly-plot').forEach(function(p){\n"
        "      Plotly.relayout(p, {autosize:true});\n"
        "    });\n"
        "  }, 50);\n"
        "}\n"
        "window.addEventListener('load', function(){\n"
        "  document.querySelectorAll('.js-plotly-plot').forEach(function(p){\n"
        "    Plotly.relayout(p, {autosize:true});\n"
        "  });\n"
        "});\n"
        "</script>\n"
        "</body></html>"
    )

    output_path = pathlib.Path("ftmo_dashboard.html")
    output_path.write_text(html, encoding="utf-8")
    print("\n[INFO] Dashboard saved to:", output_path.resolve())

    import webbrowser
    webbrowser.open(output_path.resolve().as_uri())


# =============================================================================
#  9. MAIN
# =============================================================================

def main():
    print("\n" + "=" * 72)
    print("  FTMO 2-STEP EVALUATION SIMULATOR")
    print("=" * 72)

    # ------------------------------------------------------------------
    # Load data based on DATA_SOURCE setting
    # ------------------------------------------------------------------
    print("MC_STAGE 1/5 Loading data", flush=True)
    if DATA_SOURCE == "mt5_html":
        print("[INFO] Data source: MetaTrader 5 HTML report")
        df = load_mt5_html(FILE_PATH_MT5_HTML)
    elif DATA_SOURCE == "tradingview":
        print("[INFO] Data source: TradingView CSV")
        df = load_tradingview_csv(FILE_PATH)
    else:
        sys.exit(
            "[ERROR] Unknown DATA_SOURCE: '" + DATA_SOURCE + "'\n"
            "  Set DATA_SOURCE to 'tradingview' or 'mt5_html'\n"
        )

    # ------------------------------------------------------------------
    # Compute Markov regime parameters (graceful fallback if no regime data)
    # ------------------------------------------------------------------
    trans_matrix, stationary_dist, regime_daily = compute_regime_transitions(df)
    if trans_matrix is not None:
        print("[INFO] Regime Markov matrix computed from",
              len(regime_daily), "trading days.")
        # Build per-regime P&L pools using funded scale (general pool)
        scale_fd = FD_BALANCE / 100_000.0
        _, regime_pnl_pools = _build_regime_daily(df, scale=FD_LEVERAGE * scale_fd)
    else:
        print("[INFO] No regime data found — using standard random resampling.")
        regime_pnl_pools = None
        stationary_dist  = None

    print("MC_STAGE 2/5 Challenge", flush=True)
    p1 = simulate_challenge(df, trans_matrix=trans_matrix, regime_pnl_pools=regime_pnl_pools)

    print("MC_STAGE 3/5 Verification", flush=True)
    p2 = simulate_verification(p1, df)

    print("MC_STAGE 4/5 Funded", flush=True)
    fd = simulate_funded(p2, df)

    # Long-term simulation
    print("MC_STAGE 5/5 Long-term + Report", flush=True)
    scale_lt   = FD_BALANCE / 100_000.0
    lt_pnl     = get_daily_pnl(df, FD_LEVERAGE * scale_lt)
    lt_results = simulate_longterm(
        lt_pnl,
        trans_matrix=trans_matrix,
        regime_pnl_pools=regime_pnl_pools,
    )

    print("\n" + "=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print("  Challenge pass rate:          " + str(round(p1["pass_rate"], 2)) + "%")
    print("  Verification pass rate (P1):  " + str(round(p2["pass_rate"], 2)) + "%")
    print("  Combined P1+P2 pass rate:     " + str(round(p2.get("combined_pass_rate", 0), 2)) + "%")
    print("  Funded >= 1 payout rate:      " + str(round(fd["payout_rate"], 2)) + "%")
    print("  Funded breach rate:           " + str(round(fd["breach_rate"], 2)) + "%")
    print("  Avg total earnings (funded):  $" + str(round(fd["avg_total_earnings"], 2)))
    print("=" * 72)

    build_dashboard(p1, p2, fd, lt_results,
                    trans_matrix=trans_matrix,
                    stationary_dist=stationary_dist,
                    benchmark_ticker=LT_BENCHMARK_TICKER)
    print("MC_DONE", flush=True)


# =============================================================================
#  10. PROGRAMMATIC API  — callable by other modules without the Streamlit UI
# =============================================================================

def _make_rng(seed):
    """Return a numpy default_rng, or a fresh one if seed is None."""
    return np.random.default_rng(seed)


def _eval_phase_stats(results, n_total):
    """Summarise run_eval_phase raw results into a rich stats dict."""
    df_r      = pd.DataFrame(results)
    n_passed  = int(df_r["passed"].sum())
    pass_rate = n_passed / n_total * 100
    fail_df   = df_r[~df_r["passed"]]
    total_fail = len(fail_df)
    fail_pcts  = {
        r: (int(fail_df["fail_reason"].eq(r).sum()) / total_fail * 100 if total_fail else 0.0)
        for r in ("daily_dd", "total_dd", "profit_shortfall", "consistency_violation")
    }
    pass_df   = df_r[df_r["passed"]]
    days_arr  = pass_df["days"].values if len(pass_df) else np.array([0])
    return {
        "pass_rate"              : pass_rate,
        "n_passed"               : n_passed,
        "n_failed"               : n_total - n_passed,
        "fail_pcts"              : fail_pcts,
        "daily_dd_breach_pct"    : fail_pcts.get("daily_dd", 0.0),
        "total_dd_breach_pct"    : fail_pcts.get("total_dd", 0.0),
        "profit_shortfall_pct"   : fail_pcts.get("profit_shortfall", 0.0),
        "consistency_violation_pct": fail_pcts.get("consistency_violation", 0.0),
        "avg_days"               : float(days_arr.mean()) if len(days_arr) else 0.0,
        "days_p10"               : float(np.percentile(days_arr, 10)) if len(days_arr) else 0.0,
        "days_p50"               : float(np.percentile(days_arr, 50)) if len(days_arr) else 0.0,
        "days_p90"               : float(np.percentile(days_arr, 90)) if len(days_arr) else 0.0,
        "days_worst"             : int(days_arr.max()) if len(days_arr) else 0,
        "results_df"             : df_r,
    }


def run_mc_phase1(daily_pnl: np.ndarray, *, balance=100_000, profit_pct=0.10,
                  daily_dd_pct=0.05, total_dd_pct=0.10, min_days=4,
                  n_sims=10_000, max_days=60, seed=None,
                  trans_matrix=None, regime_pnl_pools=None,
                  regime_transition=None, predrawn_pnl=None,
                  intraday_dd_factor=1.0,
                  dd_style="static", consistency_max_daily_pct=None,
                  keep_curves=0) -> dict:
    """Run Phase 1 (FTMO Challenge) MC simulation; return rich stats dict.

    ``trans_matrix`` and ``regime_pnl_pools`` enable Markov regime sampling
    (matrix is the 5x5 transition probability matrix, pools is a dict of
    regime_int -> list of daily P&L draws). ``regime_transition`` is kept as
    a backward-compat alias for ``trans_matrix``.

    ``intraday_dd_factor`` (default 1.0) tightens the effective DD limits to
    leave headroom for intraday floating losses; see ``run_eval_phase``.

    ``dd_style``, ``consistency_max_daily_pct``, ``keep_curves``: see
    ``run_eval_phase`` docstring. When ``keep_curves > 0`` the result dict
    additionally contains ``equity_curves`` (padded to ``max_days+1``).
    """
    daily_pnl = np.asarray(daily_pnl, dtype=float)
    rng = _make_rng(seed)
    if trans_matrix is None and regime_transition is not None:
        trans_matrix = regime_transition
    results, curves = run_eval_phase(
        daily_pnl, balance, profit_pct, daily_dd_pct, total_dd_pct,
        min_days, max_days, rng, n_sims,
        trans_matrix=trans_matrix,
        regime_pnl_pools=regime_pnl_pools,
        phase_label="P1",
        predrawn_pnl=predrawn_pnl,
        intraday_dd_factor=intraday_dd_factor,
        dd_style=dd_style,
        consistency_max_daily_pct=consistency_max_daily_pct,
        keep_curves=keep_curves,
    )
    stats = _eval_phase_stats(results, n_sims)
    stats["phase"] = "phase1"
    if keep_curves and curves:
        # Pad to a uniform length of max_days+1 for downstream chart consumers.
        target = max_days + 1
        padded = []
        for c in curves[:keep_curves]:
            if len(c) < target:
                c = c + [c[-1]] * (target - len(c))
            padded.append([float(v) for v in c])
        stats["equity_curves"] = padded
    return stats


def run_mc_phase2(daily_pnl, *, balance=100_000, profit_pct=0.05,
                  daily_dd_pct=0.05, total_dd_pct=0.10, min_days=4,
                  n_sims=10_000, max_days=60, seed=None, predrawn_pnl=None,
                  trans_matrix=None, regime_pnl_pools=None,
                  intraday_dd_factor=1.0,
                  dd_style="static", consistency_max_daily_pct=None,
                  keep_curves=0) -> dict:
    """Run Phase 2 (FTMO Verification) MC simulation; return rich stats dict.

    See ``run_mc_phase1`` for ``trans_matrix`` / ``regime_pnl_pools``,
    ``intraday_dd_factor``, ``dd_style``, ``consistency_max_daily_pct`` and
    ``keep_curves`` semantics.
    """
    daily_pnl = np.asarray(daily_pnl, dtype=float)
    rng = _make_rng(seed)
    results, curves = run_eval_phase(
        daily_pnl, balance, profit_pct, daily_dd_pct, total_dd_pct,
        min_days, max_days, rng, n_sims,
        trans_matrix=trans_matrix,
        regime_pnl_pools=regime_pnl_pools,
        phase_label="P2",
        predrawn_pnl=predrawn_pnl,
        intraday_dd_factor=intraday_dd_factor,
        dd_style=dd_style,
        consistency_max_daily_pct=consistency_max_daily_pct,
        keep_curves=keep_curves,
    )
    stats = _eval_phase_stats(results, n_sims)
    stats["phase"] = "phase2"
    if keep_curves and curves:
        target = max_days + 1
        padded = []
        for c in curves[:keep_curves]:
            if len(c) < target:
                c = c + [c[-1]] * (target - len(c))
            padded.append([float(v) for v in c])
        stats["equity_curves"] = padded
    return stats


def _run_funded_loop(daily_pnl, *, balance=100_000, daily_dd_pct=0.05,
                     total_dd_pct=0.10, payout_cadence_days=30,
                     months=12, n_sims=10_000, rng=None, predrawn_pnl=None,
                     payout_mode="schedule", payout_threshold=0.05,
                     profit_split=0.80, balance_reset=True,
                     compound_profits=False,
                     min_days_payout=4, max_days=None,
                     trans_matrix=None, regime_pnl_pools=None,
                     intraday_dd_factor=1.0,
                     dd_style="static", consistency_max_daily_pct=None,
                     min_days_first_payout=0, keep_curves=0) -> dict:
    """Internal funded simulation loop; shared by run_mc_funded and helpers.

    Payout modes
    ------------
    schedule  : pay every ``payout_cadence_days`` if profit > 0 and at least
                ``min_days_payout`` days have elapsed since the last payout.
    threshold : pay whenever profit_above >= ``payout_threshold * balance``
                AND at least ``min_days_payout`` have elapsed.
    both      : either condition triggers a payout (whichever fires first).

    profit_split   : trader's share of profit_above on payout (0.80 = 80%).
    balance_reset  : True  → reset equity to ``balance`` after payout (the
                              firm withdraws all profit above the line);
                     False → withdraw only the trader's share, leaving the
                              firm's share in the account as a buffer.
                              The total floor in this mode is a TRAILING
                              high-water rule: it only ratchets UP as the
                              account grows, and never moves back down.
    compound_profits (v0.6.0, default False):
                     When True, the trader's share is NOT withdrawn — the
                     full profit_above stays in the account. Models a scaling
                     account where you reinvest payouts to grow size over
                     time. Floor ratchets up to a fixed % of the new equity.
                     OVERRIDES balance_reset when True (they are mutually
                     exclusive — compound means "never withdraw").
                     ``total_earnings`` still tracks the *notional* trader
                     share so KPIs continue to make sense (Avg ROI, Expected
                     $/month etc.).

    trans_matrix / regime_pnl_pools:
        When both are provided, daily P&L is sampled via Markov regime
        switching: each day picks a new regime via ``trans_matrix`` and
        draws from that regime's pool (falls back to global ``daily_pnl``
        if the pool has fewer than 5 samples). When ``predrawn_pnl`` is
        also supplied, it takes precedence over Markov sampling.

    intraday_dd_factor (default 1.0):
        Multiplicative tightener applied to BOTH the daily and total DD
        limits to leave headroom for intraday floating losses.

    dd_style ('static' | 'trailing_eod' | 'trailing_intraday'):
        Floor behaviour. 'static' uses the fixed initial floor. The two
        trailing modes ratchet the floor UP at the start of each day to
        ``equity * (1 - total_dd_pct)`` whenever that is higher; never down.
        Under our EOD-only data ``trailing_intraday`` is identical to
        ``trailing_eod`` (no intraday peak available).

    consistency_max_daily_pct (float | None):
        If set, after a sim ends with no breach, check whether any single
        day's profit > this fraction of the sim's total positive profit.
        If so, count it as a ``consistency_breach`` in the results.

    min_days_first_payout (int):
        Gates the FIRST payout by this many absolute days from account
        start. Distinct from ``min_days_payout`` which is the spacing
        between subsequent payouts.

    keep_curves (int):
        When > 0, the result dict additionally contains:
            ``equity_curves`` — first N equity paths padded to ``max_days+1``
            ``floor_curves``  — corresponding total-floor paths
            ``survival``      — list len ``max_days+1``, fraction of sims
                                 still active at day d (no breach yet).
    """
    daily_pnl      = np.asarray(daily_pnl, dtype=float)
    if rng is None:
        rng = _make_rng(None)
    if max_days is None or max_days <= 0:
        max_days   = months * 21          # approx trading days per month
    daily_loss_abs = balance * daily_dd_pct * intraday_dd_factor
    total_floor    = balance * (1.0 - total_dd_pct * intraday_dd_factor)
    payout_mode    = (payout_mode or "schedule").lower()
    use_markov     = (trans_matrix is not None and regime_pnl_pools is not None)
    _trailing      = dd_style in ("trailing_eod", "trailing_intraday")
    _keep_n        = max(0, int(keep_curves))
    _track_consistency = consistency_max_daily_pct is not None
    # Effective per-day-recompute total-DD fraction (with intraday tightener).
    _eff_total_dd  = total_dd_pct * intraday_dd_factor

    results = []
    consistency_breaches = 0
    eq_curves: list[list[float]] = []
    fl_curves: list[list[float]] = []
    # Survival[d] = number of sims still active by day d (pre-breach).
    survival_counts = np.zeros(max_days + 1, dtype=np.int64)
    for _i in range(n_sims):
        if _i and (_i % 500 == 0):
            check_cancelled()
        equity            = balance
        current_floor     = total_floor
        days_active       = 0
        days_since_payout = 0
        payout_count      = 0
        total_earnings    = 0.0
        breach            = False
        breach_reason     = None
        breach_day        = None
        first_payout_day  = None
        keep_this         = (_keep_n == 0) or (_i < _keep_n)
        eq_path = [equity] if keep_this else None
        fl_path = [current_floor] if keep_this else None
        day_pnl_log = [] if _track_consistency else None

        # Only burn an RNG draw when Markov sampling will actually fire.
        # The predrawn path bypasses both Markov and ``rng.choice``, so the
        # legacy unconditional ``integers`` call broke seed reproducibility.
        if use_markov and predrawn_pnl is None:
            current_regime = int(rng.integers(0, 5))

        for _day in range(max_days):
            if predrawn_pnl is not None:
                _d = min(_day, predrawn_pnl.shape[1] - 1)
                day_pnl = float(predrawn_pnl[_i % len(predrawn_pnl), _d])
            elif use_markov:
                current_regime = int(rng.choice(5, p=trans_matrix[current_regime]))
                pool = regime_pnl_pools.get(current_regime, [])
                if len(pool) >= 5:
                    day_pnl = float(rng.choice(pool))
                else:
                    day_pnl = float(rng.choice(daily_pnl))
            else:
                day_pnl = float(rng.choice(daily_pnl))
            day_open           = equity
            # Trailing DD: ratchet the floor UP at the start of each new day
            # if the previous EOD equity supports a higher floor. Never down.
            if _trailing:
                candidate = day_open * (1.0 - _eff_total_dd)
                if candidate > current_floor:
                    current_floor = candidate
            days_active       += 1
            days_since_payout += 1
            equity            += day_pnl
            equity             = max(equity, 0.0)
            if _track_consistency:
                day_pnl_log.append(day_pnl)

            if equity < (day_open - daily_loss_abs):
                breach = True; breach_reason = "daily_dd"; breach_day = days_active
                if keep_this:
                    eq_path.append(equity); fl_path.append(current_floor)
                break
            if equity < current_floor:
                breach = True; breach_reason = "total_dd"; breach_day = days_active
                if keep_this:
                    eq_path.append(equity); fl_path.append(current_floor)
                break

            profit_above = equity - balance
            sched_hit = (payout_mode in ("schedule", "both")
                         and days_since_payout >= payout_cadence_days
                         and profit_above > 0)
            thr_hit   = (payout_mode in ("threshold", "both")
                         and profit_above >= balance * payout_threshold)
            elig      = days_since_payout >= min_days_payout
            # Gate the FIRST payout by an absolute days-from-start threshold.
            first_gate_ok = (
                first_payout_day is not None
                or days_active >= min_days_first_payout
            )
            if elig and first_gate_ok and (sched_hit or thr_hit):
                payout          = profit_above * profit_split
                total_earnings += payout
                payout_count   += 1
                if first_payout_day is None:
                    first_payout_day = days_active
                if compound_profits:
                    # v0.6.0 compound mode: nothing withdrawn — full profit
                    # stays in the account. Floor ratchets up to a fresh % of
                    # the new equity (trailing high-water like balance_reset
                    # =False, but on the full equity, not after a withdrawal).
                    candidate = equity * (1.0 - total_dd_pct)
                    if candidate > current_floor:
                        current_floor = candidate
                elif balance_reset:
                    equity        = balance
                    current_floor = total_floor
                else:
                    # Withdraw only the trader's share — firm's share stays as a buffer.
                    equity       -= payout
                    # Trailing high-water floor: the floor only ever ratchets UP
                    # as the account grows; it never moves down. This matches
                    # how prop firms treat the post-payout drawdown reference.
                    candidate = equity * (1.0 - total_dd_pct)
                    if candidate > current_floor:
                        current_floor = candidate
                days_since_payout = 0
            if keep_this:
                eq_path.append(equity); fl_path.append(current_floor)

        # Survival: each day from 0..days_active inclusive counts as "active".
        survival_counts[: days_active + 1] += 1

        # Optional consistency-rule post-check (funded variant: separate counter).
        cons_breach = False
        if (not breach) and _track_consistency and day_pnl_log:
            pos_total = sum(p for p in day_pnl_log if p > 0)
            if pos_total > 0:
                biggest = max(day_pnl_log)
                if biggest > pos_total * float(consistency_max_daily_pct):
                    cons_breach = True
                    consistency_breaches += 1

        results.append({
            "breach"             : breach,
            "breach_reason"      : breach_reason,
            "breach_day"         : breach_day,
            "payout_count"       : payout_count,
            "total_earnings"     : total_earnings,
            "first_payout_day"   : first_payout_day,
            "days_active"        : days_active,
            "consistency_breach" : cons_breach,
        })

        if keep_this and eq_path is not None:
            eq_curves.append(eq_path)
            fl_curves.append(fl_path)

    df_r        = pd.DataFrame(results)
    breach_rate = df_r["breach"].mean() * 100
    payout_rate = (df_r["payout_count"] > 0).mean() * 100
    breach_df   = df_r[df_r["breach"]]
    total_breach = len(breach_df)
    breach_pcts = {
        r: (int(breach_df["breach_reason"].eq(r).sum()) / total_breach * 100 if total_breach else 0.0)
        for r in ("daily_dd", "total_dd")
    }
    paid_df = df_r[df_r["payout_count"] > 0]
    fpd_arr = paid_df["first_payout_day"].dropna().values
    out: dict = {
        "breach_rate"          : breach_rate,
        "payout_rate"          : payout_rate,
        "breach_pcts"          : breach_pcts,
        "avg_total_earnings"   : float(df_r["total_earnings"].mean()),
        "avg_payout_count"     : float(df_r["payout_count"].mean()),
        "avg_first_payout_day" : float(fpd_arr.mean()) if len(fpd_arr) else 0.0,
        "avg_days_active"      : float(df_r["days_active"].mean()),
        "consistency_breaches" : int(consistency_breaches),
        "consistency_breach_rate": (consistency_breaches / max(n_sims, 1)) * 100,
        "results_df"           : df_r,
    }
    if _keep_n > 0:
        target = max_days + 1
        def _pad(paths):
            return [
                [float(v) for v in (p + [p[-1]] * (target - len(p)))] if len(p) < target
                else [float(v) for v in p]
                for p in paths
            ]
        out["equity_curves"] = _pad(eq_curves)
        out["floor_curves"]  = _pad(fl_curves)
        # Survival fraction at each day index.
        out["survival"] = [float(c) / float(max(n_sims, 1)) for c in survival_counts.tolist()]
    return out


def run_mc_funded(daily_pnl, *, balance=100_000, daily_dd_pct=0.05,
                  total_dd_pct=0.10, payout_cadence_days=30,
                  months=12, n_sims=10_000, seed=None, predrawn_pnl=None,
                  payout_mode="schedule", payout_threshold=0.05,
                  profit_split=0.80, balance_reset=True,
                  compound_profits=False,
                  min_days_payout=4, max_days=None,
                  trans_matrix=None, regime_pnl_pools=None,
                  intraday_dd_factor=1.0,
                  dd_style="static", consistency_max_daily_pct=None,
                  min_days_first_payout=0, keep_curves=0) -> dict:
    """Run funded-account MC simulation; return breach/payout/earnings stats dict.

    See ``_run_funded_loop`` for ``trans_matrix`` / ``regime_pnl_pools``,
    ``intraday_dd_factor``, ``dd_style``, ``consistency_max_daily_pct``,
    ``min_days_first_payout`` and ``keep_curves`` semantics.
    """
    rng = _make_rng(seed)
    return _run_funded_loop(
        daily_pnl, balance=balance, daily_dd_pct=daily_dd_pct,
        total_dd_pct=total_dd_pct, payout_cadence_days=payout_cadence_days,
        months=months, n_sims=n_sims, rng=rng, predrawn_pnl=predrawn_pnl,
        payout_mode=payout_mode, payout_threshold=payout_threshold,
        profit_split=profit_split, balance_reset=balance_reset,
        compound_profits=compound_profits,
        min_days_payout=min_days_payout, max_days=max_days,
        trans_matrix=trans_matrix, regime_pnl_pools=regime_pnl_pools,
        intraday_dd_factor=intraday_dd_factor,
        dd_style=dd_style,
        consistency_max_daily_pct=consistency_max_daily_pct,
        min_days_first_payout=min_days_first_payout,
        keep_curves=keep_curves,
    )


def run_mc_longterm(daily_pnl, *, balance=100_000, years=5,
                    n_sims=10_000, seed=None, predrawn_pnl=None,
                    benchmark_ticker="", ruin_pct=0.20,
                    sample_paths=200, n_days=None,
                    trans_matrix=None, regime_pnl_pools=None,
                    intraday_dd_factor=1.0) -> dict:
    """Run long-term equity MC; return equity/Sharpe/max_dd stats.

    Horizon is set by ``n_days`` when provided, else ``years * 252``.
    Also returns up to ``sample_paths`` padded equity paths for chart rendering.

    trans_matrix / regime_pnl_pools:
        When both are provided, daily P&L is sampled via Markov regime
        switching (5x5 transition matrix + per-regime daily P&L pools).
        ``predrawn_pnl`` takes precedence when supplied.

    intraday_dd_factor (default 1.0):
        Tightens the effective ruin floor (``ruin_pct *= intraday_dd_factor``)
        to reflect intraday floating losses. Less impactful than for short-
        horizon eval phases but kept for API consistency.
    """
    daily_pnl = np.asarray(daily_pnl, dtype=float)
    if n_days is None or n_days <= 0:
        n_days = int(years * 252)
    else:
        n_days = int(n_days)
    rng        = _make_rng(seed)
    effective_ruin_pct = ruin_pct * intraday_dd_factor
    ruin_floor = balance * (1.0 - effective_ruin_pct)
    use_markov = (trans_matrix is not None and regime_pnl_pools is not None)

    # ── v0.5.0 fast-path: NumPy vectorized when no Markov ───────────────────
    # When the path-dependent Markov sampler isn't requested, the whole
    # simulation reduces to: sample (n_sims, n_days) PnL → equity = balance +
    # cumsum → ruin_day = first idx where equity ≤ ruin_floor → max_dd via
    # running peak. That replaces ~12.6M Python iterations (10k sims × 1260
    # days at default) with a handful of NumPy operations. ~20–50× speedup.
    #
    # The Markov / predrawn paths still use the original loop because each
    # path's regime evolves stochastically and can't be vectorized cleanly.
    if not use_markov and predrawn_pnl is None:
        # Sample once: (n_sims, n_days) bootstrap matrix
        pnls   = rng.choice(daily_pnl, size=(n_sims, n_days), replace=True)
        cum    = np.cumsum(pnls, axis=1)
        equity = balance + cum                                       # (n_sims, n_days)
        # Ruin = first day where equity ≤ ruin_floor (per row)
        ruined_mask = equity <= ruin_floor
        any_ruin    = ruined_mask.any(axis=1)
        # First-True column index per row; np.argmax returns 0 when no True,
        # so we mask that out explicitly to "n_days" (no ruin).
        first_ruin_day = np.where(any_ruin, np.argmax(ruined_mask, axis=1), n_days)
        # For ruined rows, freeze equity at ruin_floor from ruin_day onward
        # so max_dd math reflects the floor and final_equity is the floor.
        row_idx = np.arange(n_days)
        if any_ruin.any():
            mask    = row_idx[None, :] >= first_ruin_day[:, None]    # (n_sims, n_days)
            equity  = np.where(mask, ruin_floor, equity)
        # Running peak per row, then drawdown
        prepended = np.concatenate([np.full((n_sims, 1), balance, dtype=float), equity], axis=1)
        peak      = np.maximum.accumulate(prepended, axis=1)
        dd        = (peak - prepended) / np.where(peak > 0, peak, 1.0)
        max_dd_arr = dd.max(axis=1)
        # Final equity = last column
        final_eq_arr = equity[:, -1]
        # Sharpe: per-day return = pnl / prev_equity; but only for pre-ruin
        # days (post-ruin returns would deflate volatility — same bug we
        # already documented in the loop version).
        prev_eq = prepended[:, :-1]                                  # equity at start of each day
        # Returns relative to start-of-day equity; mask out post-ruin days
        valid_day = row_idx[None, :] < first_ruin_day[:, None]       # True for pre-ruin days
        # Avoid division by zero on rows where prev_eq becomes 0 (shouldn't
        # happen pre-ruin but guard anyway).
        safe_prev = np.where(prev_eq > 0, prev_eq, 1.0)
        rets_arr  = np.where(valid_day, pnls / safe_prev, np.nan)
        # Per-row mean/std ignoring NaN; require at least 2 valid days
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_r = np.nanmean(rets_arr, axis=1)
            std_r  = np.nanstd(rets_arr,  axis=1)
            count  = np.sum(valid_day, axis=1)
            sharpe_arr = np.where(
                (count > 1) & (std_r > 0),
                mean_r / std_r * np.sqrt(252),
                0.0,
            )
        # Sampled paths for the chart fan
        sample_count = min(sample_paths, n_sims)
        sample_idx_arr = rng.choice(n_sims, size=sample_count, replace=False)
        # Each path = [balance, equity_day_0, equity_day_1, ..., equity_day_{n-1}]
        sampled_paths = prepended[sample_idx_arr].tolist()

        final_eqs = final_eq_arr.tolist()
        max_dds   = max_dd_arr.tolist()
        sharpes   = [float(s) if np.isfinite(s) else 0.0 for s in sharpe_arr]
        fe = final_eq_arr
    else:
        # ── Original per-sim loop (Markov / predrawn paths only) ────────────
        final_eqs = []
        max_dds   = []
        sharpes   = []
        sample_idx = set(rng.choice(n_sims, size=min(sample_paths, n_sims), replace=False).tolist())
        sampled_paths = []
        for _i in range(n_sims):
            if _i and (_i % 500 == 0):
                check_cancelled()
            equity   = balance
            peak     = equity
            path     = [equity]
            rets     = []
            if use_markov and predrawn_pnl is None:
                current_regime = int(rng.integers(0, 5))
            for _day in range(n_days):
                if predrawn_pnl is not None:
                    _d = min(_day, predrawn_pnl.shape[1] - 1)
                    pnl = float(predrawn_pnl[_i % len(predrawn_pnl), _d])
                elif use_markov:
                    current_regime = int(rng.choice(5, p=trans_matrix[current_regime]))
                    pool = regime_pnl_pools.get(current_regime, [])
                    if len(pool) >= 5:
                        pnl = float(rng.choice(pool))
                    else:
                        pnl = float(rng.choice(daily_pnl))
                else:
                    pnl = float(rng.choice(daily_pnl))
                ret    = pnl / equity if equity > 0 else 0.0
                equity = max(equity + pnl, 0.0)
                rets.append(ret)
                path.append(equity)
                if equity > peak:
                    peak = equity
                if equity <= ruin_floor:
                    path += [equity] * (n_days - len(path) + 1)
                    break
            peak_v = balance; max_dd = 0.0
            for v in path:
                if v > peak_v: peak_v = v
                dd = (peak_v - v) / peak_v if peak_v > 0 else 0.0
                if dd > max_dd: max_dd = dd
            n_real = len(rets)
            dr = np.array(rets[:n_real])
            sharpe = float(dr.mean() / dr.std() * np.sqrt(252)) if (len(dr) > 1 and dr.std() > 0) else 0.0
            final_eqs.append(path[-1])
            max_dds.append(max_dd)
            sharpes.append(sharpe)
            if _i in sample_idx:
                if len(path) < n_days + 1:
                    path = path + [path[-1]] * (n_days + 1 - len(path))
                sampled_paths.append([float(v) for v in path])
        fe = np.array(final_eqs)
    result: dict = {
        "pass_rate"        : float((fe > ruin_floor).mean()),
        "median_equity"    : float(np.median(fe)),
        "p10_equity"       : float(np.percentile(fe, 10)),
        "p90_equity"       : float(np.percentile(fe, 90)),
        "median_max_dd"    : float(np.median(max_dds)),
        "median_sharpe"    : float(np.median(sharpes)),
        "annualized_return": float((np.median(fe) / balance) ** (1.0 / years) - 1),
        "benchmark"        : None,
        "n_days"           : int(n_days),
        "ruin_floor"       : float(ruin_floor),
        "balance"          : float(balance),
        "equity_paths"     : sampled_paths,
        "max_dd"           : [float(v) for v in max_dds],
        "final_equity"     : [float(v) for v in final_eqs],
        "sharpe"           : [float(v) for v in sharpes],
    }
    if benchmark_ticker:
        try:
            import yfinance as yf  # type: ignore[import-not-found]
            ticker_data = yf.download(
                benchmark_ticker,
                period=f"{years}y",
                interval="1d",
                progress=False,
                auto_adjust=True,
            )
            if not ticker_data.empty:
                closes = ticker_data["Close"].dropna().values.flatten().astype(float)
                bm_start = float(closes[0])
                bm_end   = float(closes[-1])
                bm_rets  = np.diff(closes) / closes[:-1]
                bm_ann_ret = float((bm_end / bm_start) ** (1.0 / years) - 1)
                bm_sharpe  = float(bm_rets.mean() / bm_rets.std() * np.sqrt(252)) if bm_rets.std() > 0 else 0.0
                result["benchmark"] = {
                    "ticker"           : benchmark_ticker,
                    "start_price"      : bm_start,
                    "end_price"        : bm_end,
                    "annualized_return": bm_ann_ret,
                    "sharpe"           : bm_sharpe,
                    "final_equity"     : balance * (bm_end / bm_start),
                }
        except Exception as _bm_exc:
            result["benchmark"] = {"error": str(_bm_exc)}
    return result


# =============================================================================
#  11. NEW METRIC FUNCTIONS
# =============================================================================

def failure_mode_breakdown(daily_pnl, **p1_kwargs) -> dict:
    """Return % of all sims failing via profit shortfall, daily DD breach, total DD breach."""
    r = run_mc_phase1(daily_pnl, **p1_kwargs)
    n_total    = r["n_passed"] + r["n_failed"]
    n_failed   = r["n_failed"]
    df_r       = r["results_df"]
    fail_df    = df_r[~df_r["passed"]]
    daily_dd_n = int(fail_df["fail_reason"].eq("daily_dd").sum())
    total_dd_n = int(fail_df["fail_reason"].eq("total_dd").sum())
    shortfall_n = n_failed - daily_dd_n - total_dd_n
    return {
        "profit_shortfall_pct" : shortfall_n / n_total * 100 if n_total else 0.0,
        "daily_dd_breach_pct"  : daily_dd_n  / n_total * 100 if n_total else 0.0,
        "total_dd_breach_pct"  : total_dd_n  / n_total * 100 if n_total else 0.0,
    }


def time_to_pass_distribution(daily_pnl, **p1_kwargs) -> dict:
    """Return p10/p50/p90/worst trading-days-to-pass for Phase 1."""
    r = run_mc_phase1(daily_pnl, **p1_kwargs)
    return {"p10": r["days_p10"], "p50": r["days_p50"],
            "p90": r["days_p90"], "worst": r["days_worst"]}


def lot_size_sweep(daily_pnl_per_lot_unit, base_lot, *, lots=None, **p1_kwargs) -> pd.DataFrame:
    """Scale daily_pnl by lot ratio and run Phase 1 per lot; return DataFrame(lot, pass_rate, median_days, p95_total_dd)."""
    if lots is None:
        lots = [base_lot * f for f in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)]
    daily_pnl = np.asarray(daily_pnl_per_lot_unit, dtype=float)
    rows = []
    for lot in lots:
        scaled = daily_pnl * (lot / base_lot)
        r      = run_mc_phase1(scaled, **p1_kwargs)
        df_r   = r["results_df"]
        pass_df = df_r[df_r["passed"]]
        med_d   = float(pass_df["days"].median()) if len(pass_df) else float("nan")
        # p95 total-dd: largest equity drop among all sims (approximated from final_equity)
        # Use days_p90 as proxy; real total DD requires equity paths — omit for speed
        rows.append({
            "lot"         : lot,
            "pass_rate"   : r["pass_rate"],
            "median_days" : med_d,
            "p95_total_dd": float("nan"),  # would need equity paths; left as nan
        })
    return pd.DataFrame(rows)


def recovery_probability(daily_pnl, current_dd_pct, **p1_kwargs) -> dict:
    """Start simulation at balance*(1-current_dd_pct); return pass_rate and median_days."""
    base_balance = p1_kwargs.pop("balance", 100_000)
    start_equity = base_balance * (1.0 - current_dd_pct)
    r = run_mc_phase1(daily_pnl, balance=start_equity, **p1_kwargs)
    return {"pass_rate": r["pass_rate"], "median_days": r["days_p50"]}


def worst_streak_check(daily_pnl, *, balance=100_000, daily_dd_pct=0.05,
                        n_sims=10_000, seed=None) -> dict:
    """Return p95 worst-streak USD, daily DD limit USD, and whether it would breach."""
    daily_pnl = np.asarray(daily_pnl, dtype=float)
    rng       = _make_rng(seed)
    streaks   = []
    for _ in range(n_sims):
        idx = rng.integers(0, len(daily_pnl), size=30)
        samp = daily_pnl[idx]
        worst = float(min(
            sum(samp[i:j].clip(max=0).sum() for i in range(len(samp))
                for j in range(i + 1, len(samp) + 1)
                if i == 0) or 0.0,
            0.0))
        # simpler: running minimum cumsum
        cumsum = np.cumsum(samp)
        running_low = cumsum - np.maximum.accumulate(cumsum)
        streaks.append(float(running_low.min()))
    p95_streak   = float(np.percentile(streaks, 5))   # 5th pct of negative values
    dd_limit_usd = balance * daily_dd_pct
    return {
        "p95_streak_usd"    : p95_streak,
        "daily_dd_limit_usd": dd_limit_usd,
        "would_breach"      : p95_streak < -dd_limit_usd,
    }


def conditional_phase2_pass_rate(daily_pnl, *, balance=100_000, n_sims=10_000,
                                  seed=None) -> dict:
    """Return P(P2 passes | P1 passed), combined, p1, and unconditional p2 rates."""
    p1 = run_mc_phase1(daily_pnl, balance=balance, n_sims=n_sims, seed=seed)
    p2_kwargs = dict(balance=balance, n_sims=n_sims,
                     seed=None if seed is None else seed + 1)
    p2_uncond = run_mc_phase2(daily_pnl, **p2_kwargs)
    n_p1_pass = p1["n_passed"]
    if n_p1_pass == 0:
        return {
            "combined_pass_rate"        : 0.0,
            "conditional_p2_given_p1"   : 0.0,
            "p1_pass_rate"              : 0.0,
            "unconditional_p2_pass_rate": p2_uncond["pass_rate"],
        }
    p2_cond = run_mc_phase2(daily_pnl, balance=balance, n_sims=n_p1_pass,
                             seed=None if seed is None else seed + 2)
    combined = (p1["pass_rate"] / 100) * (p2_cond["pass_rate"] / 100) * 100
    return {
        "combined_pass_rate"        : combined,
        "conditional_p2_given_p1"   : p2_cond["pass_rate"],
        "p1_pass_rate"              : p1["pass_rate"],
        "unconditional_p2_pass_rate": p2_uncond["pass_rate"],
    }


def conservative_mode_simulator(daily_pnl, *, balance=100_000, lot_reduction_after_p1=0.5,
                                 n_sims=10_000, seed=None) -> dict:
    """Reduce lot after P1 passes; compare combined pass rate normal vs conservative."""
    daily_pnl = np.asarray(daily_pnl, dtype=float)
    p1_normal = run_mc_phase1(daily_pnl, balance=balance, n_sims=n_sims, seed=seed)
    p2_normal = run_mc_phase2(daily_pnl, balance=balance, n_sims=n_sims,
                               seed=None if seed is None else seed + 1)
    combined_normal = (p1_normal["pass_rate"] / 100) * (p2_normal["pass_rate"] / 100) * 100

    reduced_pnl   = daily_pnl * lot_reduction_after_p1
    p2_conserv    = run_mc_phase2(reduced_pnl, balance=balance, n_sims=n_sims,
                                   seed=None if seed is None else seed + 2)
    combined_conserv = (p1_normal["pass_rate"] / 100) * (p2_conserv["pass_rate"] / 100) * 100
    return {
        "normal_combined_pass_rate"      : combined_normal,
        "conservative_combined_pass_rate": combined_conserv,
        "p1_pass_rate"                   : p1_normal["pass_rate"],
        "p2_normal_pass_rate"            : p2_normal["pass_rate"],
        "p2_conservative_pass_rate"      : p2_conserv["pass_rate"],
    }


def phase2_time_to_pass(daily_pnl, **p2_kwargs) -> dict:
    """Return p10/p50/p90/worst trading-days-to-pass for Phase 2."""
    r = run_mc_phase2(daily_pnl, **p2_kwargs)
    return {"p10": r["days_p10"], "p50": r["days_p50"],
            "p90": r["days_p90"], "worst": r["days_worst"]}


def time_to_first_payout(daily_pnl, *, balance=100_000, payout_cadence_days=30,
                         n_sims=10_000, seed=None) -> dict:
    """Return p10/p50/p90 days to first payout and P(blowup before first payout)."""
    r    = run_mc_funded(daily_pnl, balance=balance, payout_cadence_days=payout_cadence_days,
                         n_sims=n_sims, seed=seed)
    df_r = r["results_df"]
    fpd  = df_r["first_payout_day"].dropna().values
    blowup_before = ((df_r["breach"]) & (df_r["first_payout_day"].isna())).mean()
    if len(fpd) == 0:
        fpd = np.array([float("nan")])
    return {
        "p10"                        : float(np.percentile(fpd, 10)),
        "p50"                        : float(np.percentile(fpd, 50)),
        "p90"                        : float(np.percentile(fpd, 90)),
        "p_blowup_before_first_payout": float(blowup_before),
    }


def payout_cadence_optimizer(daily_pnl, *, balance=100_000,
                              cadences=(14, 30, 60), months=12,
                              n_sims=10_000, seed=None) -> pd.DataFrame:
    """One row per cadence: cadence_days, avg_total_payouts_usd, avg_payout_count, blowup_rate."""
    rows = []
    for cad in cadences:
        r = run_mc_funded(daily_pnl, balance=balance, payout_cadence_days=cad,
                          months=months, n_sims=n_sims, seed=seed)
        rows.append({
            "cadence_days"         : cad,
            "avg_total_payouts_usd": r["avg_total_earnings"],
            "avg_payout_count"     : r["avg_payout_count"],
            "blowup_rate"          : r["breach_rate"],
        })
    return pd.DataFrame(rows)


def funded_lifetime(daily_pnl, *, balance=100_000, max_months=36,
                    n_sims=10_000, seed=None) -> dict:
    """Return median/p10/p90 funded lifetime in months and a monthly survival curve."""
    daily_pnl      = np.asarray(daily_pnl, dtype=float)
    rng            = _make_rng(seed)
    daily_dd_abs   = balance * 0.05
    total_floor    = balance * 0.90
    max_days       = max_months * 21
    breach_days    = []
    for _ in range(n_sims):
        equity = balance
        breach_day = max_days
        for d in range(1, max_days + 1):
            pnl      = float(rng.choice(daily_pnl))
            day_open = equity
            equity   = max(equity + pnl, 0.0)
            if equity < (day_open - daily_dd_abs) or equity < total_floor:
                breach_day = d
                break
        breach_days.append(breach_day)
    bd  = np.array(breach_days)
    # survival curve per month
    surv = np.array([float((bd >= m * 21).mean()) for m in range(max_months + 1)])
    med_months = float(np.median(bd / 21))
    p10_months = float(np.percentile(bd / 21, 10))
    p90_months = float(np.percentile(bd / 21, 90))
    return {
        "median_months" : med_months,
        "p10_months"    : p10_months,
        "p90_months"    : p90_months,
        "survival_curve": surv,
    }


def kelly_fraction(daily_pnl) -> dict:
    """Compute continuous Kelly fraction from daily P&L; return kelly_f, half/quarter Kelly, expected growth rate."""
    pnl  = np.asarray(daily_pnl, dtype=float)
    mu   = float(pnl.mean())
    var  = float(pnl.var())
    if var <= 0:
        return {"kelly_f": 0.0, "half_kelly": 0.0, "quarter_kelly": 0.0,
                "expected_growth_rate": 0.0}
    kelly_f = mu / var
    eg_rate = mu ** 2 / (2.0 * var)   # log-growth rate at Kelly fraction
    return {
        "kelly_f"             : kelly_f,
        "half_kelly"          : kelly_f / 2.0,
        "quarter_kelly"       : kelly_f / 4.0,
        "expected_growth_rate": eg_rate,
    }


def risk_of_ruin_horizons(daily_pnl, *, balance=100_000,
                           horizons_days=(30, 180, 365, 1825),
                           ruin_dd_pct=0.10, n_sims=10_000, seed=None) -> pd.DataFrame:
    """One row per horizon: horizon_days, ruin_probability."""
    daily_pnl  = np.asarray(daily_pnl, dtype=float)
    rng        = _make_rng(seed)
    ruin_floor = balance * (1.0 - ruin_dd_pct)
    max_h      = int(max(horizons_days))
    # Run each sim to max horizon, record first ruin day
    ruin_days  = np.full(n_sims, max_h + 1)
    for i in range(n_sims):
        equity = balance
        for d in range(1, max_h + 1):
            equity = max(equity + float(rng.choice(daily_pnl)), 0.0)
            if equity <= ruin_floor:
                ruin_days[i] = d
                break
    rows = []
    for h in horizons_days:
        rows.append({
            "horizon_days"    : h,
            "ruin_probability": float((ruin_days <= h).mean()),
        })
    return pd.DataFrame(rows)


def multi_strategy_portfolio(daily_pnl_list: list, *, weights=None, balance=100_000,
                              n_sims=10_000, seed=None) -> dict:
    """Combine N daily-pnl arrays (aligned by length); return Sharpe/Sortino/max_dd/correlation/return."""
    arrays = [np.asarray(p, dtype=float) for p in daily_pnl_list]
    min_len = min(len(a) for a in arrays)
    arrays  = [a[:min_len] for a in arrays]
    n       = len(arrays)
    if weights is None:
        weights = np.ones(n) / n
    else:
        weights = np.asarray(weights, dtype=float)
        weights = weights / weights.sum()
    mat = np.column_stack(arrays)    # shape (min_len, n)
    combined = mat @ weights          # weighted daily pnl
    rng = _make_rng(seed)
    # Simulate n_sims paths of length min_len by bootstrapping combined
    final_eqs = []
    for _ in range(n_sims):
        idx    = rng.integers(0, len(combined), size=min_len)
        path   = balance + np.cumsum(combined[idx])
        final_eqs.append(float(path[-1]))
    daily_ret  = combined / balance
    mu         = float(daily_ret.mean())
    sig        = float(daily_ret.std())
    sharpe     = float(mu / sig * np.sqrt(252)) if sig > 0 else 0.0
    neg_ret    = daily_ret[daily_ret < 0]
    sortino_d  = float(neg_ret.std()) if len(neg_ret) > 1 else 1e-9
    sortino    = float(mu / sortino_d * np.sqrt(252)) if sortino_d > 0 else 0.0
    # max drawdown
    cum = np.cumsum(combined)
    roll_max = np.maximum.accumulate(cum)
    dd = (roll_max - cum) / (balance + roll_max)
    max_dd_pct = float(dd.max())
    corr_df    = pd.DataFrame(mat).corr()
    ann_ret    = float((np.median(final_eqs) / balance) - 1) * (252.0 / min_len)
    return {
        "sharpe"              : sharpe,
        "sortino"             : sortino,
        "max_dd_pct"          : max_dd_pct,
        "correlation_matrix"  : corr_df,
        "combined_daily_pnl"  : combined,
        "annualized_return"   : ann_ret,
    }


def fat_tail_stress(daily_pnl, *, shock_sigma=3.0, n_shocks=5, balance=100_000,
                    n_sims=10_000, seed=None) -> dict:
    """Inject random negative shocks of shock_sigma*std; return survival_rate, median/worst recovery_days."""
    daily_pnl  = np.asarray(daily_pnl, dtype=float)
    rng        = _make_rng(seed)
    shock_size = float(daily_pnl.std()) * shock_sigma
    ruin_floor = balance * 0.90
    survivals  = 0
    rec_days   = []
    for _ in range(n_sims):
        equity   = balance
        shocked  = 0
        peak     = balance
        trough   = balance
        in_drawdown = False
        rec_day  = None
        for d in range(500):
            pnl = float(rng.choice(daily_pnl))
            # Randomly inject a shock
            if shocked < n_shocks and rng.random() < n_shocks / 500:
                pnl -= shock_size
                shocked += 1
            equity = max(equity + pnl, 0.0)
            if equity > peak:
                peak = equity
                if in_drawdown:
                    rec_days.append(d)
                    in_drawdown = False
                    rec_day = d
            if equity < peak * 0.95 and not in_drawdown:
                trough = equity
                in_drawdown = True
        if equity > ruin_floor:
            survivals += 1
    surv_rate = survivals / n_sims
    return {
        "survival_rate"       : surv_rate,
        "median_recovery_days": float(np.median(rec_days)) if rec_days else float("nan"),
        "worst_recovery_days" : int(np.max(rec_days)) if rec_days else 0,
    }


# =============================================================================
#  12. MAIN (unchanged)
# =============================================================================

if __name__ == "__main__":
    # ── app override (written by tab_mc.py when launched from the UI) ─────
    _ov = pathlib.Path(__file__).parent / "_app_override.json"
    if _ov.exists():
        import json as _json
        _cfg = _json.loads(_ov.read_text())
        for _k, _v in _cfg.items():
            if _k in globals():
                globals()[_k] = _v   # update module-level config variables

    # ── smoke test: triggered by --smoke-test flag (also the default when
    #    no override is present, so CI/import checks always work) ─────────
    _run_smoke = "--smoke-test" in sys.argv or not _ov.exists()

    if _run_smoke:
        print("Running smoke test ...")
        _rng_st = np.random.default_rng(42)
        _pnl    = _rng_st.normal(150, 1200, 250)   # synthetic daily P&L, positive mean

        print("run_mc_phase1:", list(run_mc_phase1(_pnl, n_sims=500, seed=1).keys()))
        print("run_mc_phase2:", list(run_mc_phase2(_pnl, n_sims=500, seed=2).keys()))
        print("run_mc_funded:", list(run_mc_funded(_pnl, n_sims=500, seed=3).keys()))
        print("run_mc_longterm:", list(run_mc_longterm(_pnl, years=2, n_sims=200, seed=4).keys()))

        print("failure_mode_breakdown:", failure_mode_breakdown(_pnl, n_sims=500, seed=5))
        print("time_to_pass_distribution:", time_to_pass_distribution(_pnl, n_sims=500, seed=6))
        print("lot_size_sweep cols:", list(lot_size_sweep(_pnl, base_lot=0.1, n_sims=200, seed=7).columns))
        print("recovery_probability:", recovery_probability(_pnl, 0.03, n_sims=500, seed=8))
        print("worst_streak_check:", worst_streak_check(_pnl, n_sims=500, seed=9))
        print("conditional_phase2_pass_rate:", conditional_phase2_pass_rate(_pnl, n_sims=300, seed=10))
        print("conservative_mode_simulator:", list(conservative_mode_simulator(_pnl, n_sims=300, seed=11).keys()))
        print("phase2_time_to_pass:", phase2_time_to_pass(_pnl, n_sims=500, seed=12))
        print("time_to_first_payout:", time_to_first_payout(_pnl, n_sims=500, seed=13))
        print("payout_cadence_optimizer cols:", list(payout_cadence_optimizer(_pnl, n_sims=200, seed=14).columns))
        print("funded_lifetime:", list(funded_lifetime(_pnl, n_sims=200, seed=15).keys()))
        print("kelly_fraction:", kelly_fraction(_pnl))
        print("risk_of_ruin_horizons cols:", list(risk_of_ruin_horizons(_pnl, n_sims=200, seed=16).columns))
        print("multi_strategy_portfolio:", list(multi_strategy_portfolio([_pnl, _pnl * 0.8], n_sims=200, seed=17).keys()))
        print("fat_tail_stress:", fat_tail_stress(_pnl, n_sims=200, seed=18))
        print("All smoke tests passed.")
    else:
        main()