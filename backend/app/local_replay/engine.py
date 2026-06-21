"""Deterministic event-driven bid/ask tick replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from ..research.models import StrategySpec
from .features import build_features
from .models import ReplayMetrics, ReplayRequest


ENGINE_VERSION = "1.1.0"
FEATURE_NAMES = (
    "rsi14", "macd_norm", "atr_pct", "bb_width", "trend", "mtf_bull_score",
    "body_pct", "rng_atr", "vol_ratio", "vol_body_conf", "regime", "vol_price_div",
    "bb_expanding", "prev_sess_bias", "poc_dist", "bull", "uwk_pct", "lwk_pct",
    "stoch_k", "stoch_d", "pin_bar", "inside_bar", "outside_bar", "htf_div",
    "rolling_sharpe", "sd_zone", "vwap_dist",
)
SESSION_KEYS = ("TradeAsian", "TradeLondon", "TradeNY", "TradeOverlap", "TradeOff")
TF_MINUTES = {
    "m1": 1, "m2": 2, "m3": 3, "m4": 4, "m5": 5, "m10": 10, "m15": 15,
    "m20": 20, "m30": 30, "h1": 60, "h2": 120, "h4": 240, "h6": 360,
    "h8": 480, "h12": 720, "d1": 1440,
}


def _float(params: dict[str, str], key: str, default: float) -> float:
    try:
        return float(params.get(key, default))
    except (TypeError, ValueError):
        return default


def _int(params: dict[str, str], key: str, default: int) -> int:
    return int(_float(params, key, default))


def _bool(params: dict[str, str], key: str, default: bool) -> bool:
    value = str(params.get(key, str(default))).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _signal(row: pd.Series, params: dict[str, str]) -> bool:
    session = int(row.get("session", 4))
    if session < 0 or session >= len(SESSION_KEYS) or not _bool(params, SESSION_KEYS[session], True):
        return False
    for feature in FEATURE_NAMES:
        lo_key, hi_key = f"{feature}_lo", f"{feature}_hi"
        if lo_key not in params and hi_key not in params:
            continue
        value = float(row.get(feature, 0.0))
        if value < _float(params, lo_key, -999.0) or value > _float(params, hi_key, 999.0):
            return False
    return True


def _direction(row: pd.Series, params: dict[str, str]) -> int:
    mode = _int(params, "DirectionMode", 0)
    if mode == 0:
        return 1
    if mode == 1:
        return -1
    columns = ["trend", "bull", "macd_norm", "rsi14", "session", "regime"]
    col = columns[max(0, min(len(columns) - 1, _int(params, "Discrim_Col", 1)))]
    value = float(row.get(col, 0.0))
    above = value >= _float(params, "Discrim_Thresh", 0.0)
    preferred = 1 if _int(params, "Discrim_Dir", 1) >= 0 else -1
    return preferred if above else -preferred


def _metrics(ledger: pd.DataFrame, initial_balance: float) -> ReplayMetrics:
    pnl = ledger["net_pnl"].to_numpy(dtype=float) if not ledger.empty else np.array([], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    equity = initial_balance + np.cumsum(pnl)
    if equity.size:
        peaks = np.maximum.accumulate(np.r_[initial_balance, equity])[:-1]
        drawdowns = peaks - equity
        max_dd = float(drawdowns.max(initial=0.0))
        max_dd_pct = float(np.max(np.divide(drawdowns, peaks, out=np.zeros_like(drawdowns), where=peaks != 0)) * 100)
    else:
        max_dd = max_dd_pct = 0.0
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    return ReplayMetrics(
        trades=len(pnl), wins=len(wins),
        win_rate_pct=round(100 * len(wins) / len(pnl), 4) if len(pnl) else None,
        net_profit=round(float(pnl.sum()), 4), gross_profit=round(gross_profit, 4),
        gross_loss=round(gross_loss, 4),
        profit_factor=round(gross_profit / gross_loss, 4) if gross_loss else None,
        expected_payoff=round(float(pnl.mean()), 4) if len(pnl) else None,
        max_drawdown=round(max_dd, 4), max_drawdown_pct=round(max_dd_pct, 4),
    )


def run_replay(
    ticks: pd.DataFrame, bars: pd.DataFrame, strategy: StrategySpec,
    request: ReplayRequest, point_size: float,
    signal_bars: dict[str, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, ReplayMetrics, pd.DataFrame]:
    return run_replay_stream(
        (ticks,), bars, strategy, request, point_size,
        signal_bars=signal_bars, total_ticks=len(ticks),
    )


def run_replay_stream(
    tick_batches: Iterable[pd.DataFrame], bars: pd.DataFrame, strategy: StrategySpec,
    request: ReplayRequest, point_size: float,
    signal_bars: dict[str, pd.DataFrame] | None = None,
    total_ticks: int | None = None,
) -> tuple[pd.DataFrame, ReplayMetrics, pd.DataFrame]:
    """Replay chronologically ordered tick batches without retaining all ticks in RAM."""
    params = strategy.parameters
    features = build_features(bars, request.session_utc_offset, signal_bars)
    minutes = TF_MINUTES.get(request.timeframe.lower())
    if minutes is None:
        raise ValueError(f"unsupported replay timeframe: {request.timeframe}")
    decision_columns = {"session", "regime", "trend", "bull", "macd_norm", "rsi14"}
    decision_columns.update(
        feature for feature in FEATURE_NAMES
        if f"{feature}_lo" in params or f"{feature}_hi" in params
    )
    decisions = features[[column for column in decision_columns if column in features]].copy()
    decisions["decision_time"] = decisions.index + pd.to_timedelta(minutes, unit="m")
    decisions = decisions.reset_index()
    decision_times = decisions["decision_time"].tolist()

    sl_pct = _float(params, "SL_Pct", 0.005)
    tp_pct = _float(params, "TP_Pct", 0.005)
    lots = _float(params, "Lots", 0.1)
    cooldown = _int(params, "CooldownBars", 0)
    max_hold = _int(params, "MaxHoldBars", 0)
    max_spread = _float(params, "MaxSpreadPoints", 0.0) * point_size
    slippage = request.slippage_points * point_size
    commission = request.commission_per_lot_round_turn * lots
    records: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    decision_index = 0
    last_entry_decision = -10**9

    from ..jobs.runners import check_cancelled, get_current_job

    processed_ticks = 0
    last_quote: tuple[pd.Timestamp, float, float] | None = None
    for ticks in tick_batches:
        if ticks.empty:
            continue
        ticks = ticks.sort_values("time").copy()
        ticks["time"] = pd.to_datetime(ticks["time"], utc=True)
        for now, raw_bid, raw_ask in ticks[["time", "bid", "ask"]].itertuples(index=False, name=None):
            tick_index = processed_ticks
            processed_ticks += 1
            if tick_index % 100_000 == 0:
                check_cancelled()
                current_job = get_current_job()
                if current_job is not None:
                    current_job.mark_stage(
                        "Bid/ask tick replay", tick_index + 1, max(total_ticks or 0, 1)
                    )
            quote_bid, quote_ask = float(raw_bid), float(raw_ask)
            last_quote = (now, quote_bid, quote_ask)
            if position is not None:
                direction = position["direction_sign"]
                exit_price = quote_bid - slippage if direction == 1 else quote_ask + slippage
                reason = None
                if direction == 1 and exit_price <= position["stop_price"]:
                    exit_price, reason = min(exit_price, position["stop_price"]), "stop_loss"
                elif direction == 1 and exit_price >= position["target_price"]:
                    exit_price, reason = max(exit_price, position["target_price"]), "take_profit"
                elif direction == -1 and exit_price >= position["stop_price"]:
                    exit_price, reason = max(exit_price, position["stop_price"]), "stop_loss"
                elif direction == -1 and exit_price <= position["target_price"]:
                    exit_price, reason = min(exit_price, position["target_price"]), "take_profit"
                if max_hold and now >= position["max_hold_time"]:
                    reason = "max_hold"
                if reason:
                    gross = direction * (exit_price - position["entry_price"]) * lots * request.contract_size
                    net = gross - commission
                    initial_risk = abs(position["entry_price"] - position["stop_price"]) * lots * request.contract_size + commission
                    records.append({
                        **position, "exit_time": now, "exit_price": exit_price, "exit_reason": reason,
                        "gross_pnl": gross, "spread_cost": position["entry_spread"] * lots * request.contract_size,
                        "commission": commission, "swap": 0.0, "slippage": request.slippage_points,
                        "net_pnl": net, "initial_risk": initial_risk,
                        "r_multiple": net / initial_risk if initial_risk else 0.0,
                        "hold_seconds": (now - position["entry_time"]).total_seconds(),
                    })
                    position = None

            while decision_index < len(decisions) and decision_times[decision_index] <= now:
                row = decisions.iloc[decision_index]
                if (
                    position is None and decision_index - last_entry_decision > cooldown and _signal(row, params)
                    and (max_spread <= 0 or quote_ask - quote_bid <= max_spread)
                ):
                    direction = _direction(row, params)
                    entry = quote_ask + slippage if direction == 1 else quote_bid - slippage
                    stop = entry * (1 - direction * sl_pct)
                    target = entry * (1 + direction * tp_pct)
                    position = {
                        "strategy_fingerprint": strategy.fingerprint,
                        "entry_time": now, "entry_price": entry,
                        "direction": "long" if direction == 1 else "short", "direction_sign": direction,
                        "size_lots": lots, "stop_price": stop, "target_price": target,
                        "entry_spread": quote_ask - quote_bid, "signal_time": row["time"],
                        "session": int(row.get("session", 4)), "regime": int(row.get("regime", 4)),
                        "max_hold_time": now + timedelta(minutes=minutes * max_hold) if max_hold else pd.Timestamp.max.tz_localize("UTC"),
                    }
                    last_entry_decision = decision_index
                decision_index += 1

    if position is not None and last_quote is not None:
        last_time, last_bid, last_ask = last_quote
        direction = position["direction_sign"]
        exit_price = last_bid if direction == 1 else last_ask
        gross = direction * (exit_price - position["entry_price"]) * lots * request.contract_size
        net = gross - commission
        initial_risk = abs(position["entry_price"] - position["stop_price"]) * lots * request.contract_size + commission
        records.append({
            **position, "exit_time": last_time, "exit_price": exit_price, "exit_reason": "end_of_data",
            "gross_pnl": gross, "spread_cost": position["entry_spread"] * lots * request.contract_size,
            "commission": commission, "swap": 0.0, "slippage": request.slippage_points,
            "net_pnl": net, "initial_risk": initial_risk,
            "r_multiple": net / initial_risk if initial_risk else 0.0,
            "hold_seconds": (last_time - position["entry_time"]).total_seconds(),
        })
    ledger = pd.DataFrame(records)
    return ledger, _metrics(ledger, request.initial_balance), features.reset_index()
