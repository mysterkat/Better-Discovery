from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .models import HypothesisBarRequest


def summarize_ledger(ledger: pd.DataFrame, initial_balance: float) -> dict[str, Any]:
    pnl = ledger["net_pnl"].to_numpy(dtype=float) if not ledger.empty else np.array([], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    equity = initial_balance + np.cumsum(pnl)
    if equity.size:
        peaks = np.maximum.accumulate(np.r_[initial_balance, equity])[:-1]
        drawdown = peaks - equity
        max_dd = float(drawdown.max(initial=0.0))
        max_dd_pct = float(np.max(np.divide(drawdown, peaks, out=np.zeros_like(drawdown), where=peaks > 0)) * 100)
    else:
        max_dd = max_dd_pct = 0.0
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    exits = pd.to_datetime(ledger["exit_time"], utc=True) if not ledger.empty else pd.Series(dtype="datetime64[ns, UTC]")
    if len(exits):
        monthly_keys = exits.dt.strftime("%Y-%m")
        quarterly_keys = exits.dt.year.astype(str) + "Q" + (((exits.dt.month - 1) // 3) + 1).astype(str)
        yearly_keys = exits.dt.year.astype(str)
        monthly = ledger.assign(month=monthly_keys).groupby("month")["net_pnl"].sum()
        quarterly = ledger.assign(quarter=quarterly_keys).groupby("quarter")["net_pnl"].sum()
        yearly = ledger.assign(year=yearly_keys).groupby("year")["net_pnl"].sum()
    else:
        monthly = quarterly = yearly = pd.Series(dtype=float)
    return {
        "trades": int(len(pnl)),
        "wins": int(len(wins)),
        "win_rate_pct": float(100 * len(wins) / len(pnl)) if len(pnl) else None,
        "net_profit": float(pnl.sum()),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "expected_payoff": float(pnl.mean()) if len(pnl) else None,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "positive_month_fraction": float((monthly > 0).mean()) if len(monthly) else 0.0,
        "positive_quarter_fraction": float((quarterly > 0).mean()) if len(quarterly) else 0.0,
        "positive_years": int((yearly > 0).sum()),
        "positive_year_fraction": float((yearly > 0).mean()) if len(yearly) else 0.0,
        "monthly_net_profit": monthly.to_dict(),
        "quarterly_net_profit": quarterly.to_dict(),
        "yearly_net_profit": yearly.to_dict(),
    }


def run_bar_replay(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    request: HypothesisBarRequest,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = bars.copy().sort_values("time").reset_index(drop=True)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    normalized_signals = signals.copy()
    normalized_signals.index.name = "time"
    signal = normalized_signals.reset_index().set_index("time").reindex(frame["time"]).reset_index(drop=True)
    lot = request.lot_size
    commission = request.commission_per_lot_round_turn * lot
    slippage = request.slippage_price_units
    records: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None

    for index, row in frame.iterrows():
        now = row["time"]
        signal_index = index - 1
        if position is None and signal_index >= 0:
            signal_row = signal.iloc[signal_index]
            direction = int(signal_row.get("signal_direction", 0))
            stop_distance = float(signal_row.get("stop_distance", 0.0))
            if direction and stop_distance > 0:
                entry = (float(row["ask_open"]) if direction == 1 else float(row["bid_open"])) + direction * slippage
                target_distance = float(signal_row.get("target_distance", 0.0))
                explicit_target = float(signal_row.get("signal_target_price", 0.0))
                target = explicit_target if explicit_target > 0 else (
                    entry + direction * target_distance if target_distance > 0 else float("nan")
                )
                if not np.isfinite(target) or direction * (target - entry) > 0:
                    position = {
                        "strategy_fingerprint": request.strategy.fingerprint,
                        "strategy_id": request.strategy.strategy_id,
                        "lineage": request.strategy.lineage,
                        "signal_time": frame.iloc[signal_index]["time"],
                        "entry_time": now,
                        "entry_bar": index,
                        "entry_price": entry,
                        "entry_spread": float(row["ask_open"] - row["bid_open"]),
                        "direction": "long" if direction == 1 else "short",
                        "direction_sign": direction,
                        "size_lots": lot,
                        "stop_price": entry - direction * stop_distance,
                        "target_price": target,
                        "initial_risk": stop_distance * lot * request.contract_size + commission,
                        "trail_atr": float(signal_row.get("trail_atr", 0.0)),
                        "max_hold_bars": max(1, int(signal_row.get("max_hold_bars", 1))),
                    }

        if position is not None:
            direction = position["direction_sign"]
            if direction == 1:
                stop_hit = float(row["bid_low"]) <= position["stop_price"]
                target_hit = np.isfinite(position["target_price"]) and float(row["bid_high"]) >= position["target_price"]
            else:
                stop_hit = float(row["ask_high"]) >= position["stop_price"]
                target_hit = np.isfinite(position["target_price"]) and float(row["ask_low"]) <= position["target_price"]
            reason = None
            if stop_hit:
                reason = "stop_loss"
                exit_price = position["stop_price"] - direction * slippage
            elif target_hit:
                reason = "take_profit"
                exit_price = position["target_price"] - direction * slippage
            elif index - position["entry_bar"] >= position["max_hold_bars"]:
                reason = "max_hold"
                exit_price = (float(row["bid_close"]) if direction == 1 else float(row["ask_close"])) - direction * slippage
            if reason is not None:
                gross = direction * (exit_price - position["entry_price"]) * lot * request.contract_size
                records.append({
                    **position,
                    "exit_time": now,
                    "exit_price": exit_price,
                    "exit_reason": reason,
                    "gross_pnl": gross,
                    "commission": commission,
                    "net_pnl": gross - commission,
                    "hold_bars": index - position["entry_bar"],
                })
                position = None
            elif position["trail_atr"] > 0:
                atr = float(signal.iloc[index].get("atr14", 0.0))
                if direction == 1:
                    position["stop_price"] = max(position["stop_price"], float(row["bid_high"]) - position["trail_atr"] * atr)
                else:
                    position["stop_price"] = min(position["stop_price"], float(row["ask_low"]) + position["trail_atr"] * atr)

    if position is not None and not frame.empty:
        row = frame.iloc[-1]
        direction = position["direction_sign"]
        exit_price = (float(row["bid_close"]) if direction == 1 else float(row["ask_close"])) - direction * slippage
        gross = direction * (exit_price - position["entry_price"]) * lot * request.contract_size
        records.append({
            **position,
            "exit_time": row["time"],
            "exit_price": exit_price,
            "exit_reason": "end_of_data",
            "gross_pnl": gross,
            "commission": commission,
            "net_pnl": gross - commission,
            "hold_bars": len(frame) - 1 - position["entry_bar"],
        })
    ledger = pd.DataFrame(records)
    if ledger.empty:
        ledger = pd.DataFrame(columns=[
            "strategy_fingerprint", "strategy_id", "lineage", "signal_time",
            "entry_time", "entry_price", "entry_spread", "direction",
            "direction_sign", "size_lots", "stop_price", "target_price",
            "initial_risk", "exit_time", "exit_price", "exit_reason",
            "gross_pnl", "commission", "net_pnl", "hold_bars",
        ])
    return ledger, summarize_ledger(ledger, request.initial_balance)


def run_bar_replay_fast_metrics(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    request: HypothesisBarRequest,
) -> dict[str, float | int | None]:
    """NumPy-backed metric path for repeated timing permutations.

    This mirrors the canonical closed-bar replay but keeps only PnL values
    rather than allocating a full ledger for variants that will be rejected.
    """
    frame = bars.sort_values("time").reset_index(drop=True)
    normalized = signals.copy()
    normalized.index.name = "time"
    frame_times = pd.DatetimeIndex(pd.to_datetime(frame["time"], utc=True), name="time")
    if normalized.index.equals(frame_times):
        aligned = normalized
    else:
        aligned = normalized.reset_index().set_index("time").reindex(frame_times).fillna(0)
    bid_high = frame["bid_high"].to_numpy(dtype=float)
    bid_low = frame["bid_low"].to_numpy(dtype=float)
    bid_close = frame["bid_close"].to_numpy(dtype=float)
    bid_open = frame["bid_open"].to_numpy(dtype=float)
    ask_open = frame["ask_open"].to_numpy(dtype=float)
    ask_high = frame["ask_high"].to_numpy(dtype=float)
    ask_low = frame["ask_low"].to_numpy(dtype=float)
    ask_close = frame["ask_close"].to_numpy(dtype=float)
    directions = aligned["signal_direction"].to_numpy(dtype=np.int8)
    stop_distances = aligned["stop_distance"].to_numpy(dtype=float)
    target_distances = aligned["target_distance"].to_numpy(dtype=float)
    explicit_targets = aligned["signal_target_price"].to_numpy(dtype=float)
    trail_atrs = aligned["trail_atr"].to_numpy(dtype=float)
    max_holds = aligned["max_hold_bars"].to_numpy(dtype=np.int32)
    atrs = aligned["atr14"].to_numpy(dtype=float)
    lot = request.lot_size
    commission = request.commission_per_lot_round_turn * lot
    slippage = request.slippage_price_units
    multiplier = lot * request.contract_size
    entry = stop = target = np.nan
    direction = 0
    trail = 0.0
    max_hold = entry_bar = 0
    active = False
    pnl: list[float] = []
    for index in range(len(frame)):
        signal_index = index - 1
        if not active and signal_index >= 0 and directions[signal_index] and stop_distances[signal_index] > 0:
            direction = int(directions[signal_index])
            entry = (ask_open[index] if direction == 1 else bid_open[index]) + direction * slippage
            stop = entry - direction * stop_distances[signal_index]
            explicit = explicit_targets[index - 1]
            distance = target_distances[index - 1]
            target = explicit if explicit > 0 else (entry + direction * distance if distance > 0 else np.nan)
            if not np.isfinite(target) or direction * (target - entry) > 0:
                trail = trail_atrs[index - 1]
                max_hold = max(1, int(max_holds[index - 1]))
                entry_bar = index
                active = True
        if active:
            exit_price = np.nan
            if direction == 1:
                stop_hit = bid_low[index] <= stop
                target_hit = np.isfinite(target) and bid_high[index] >= target
            else:
                stop_hit = ask_high[index] >= stop
                target_hit = np.isfinite(target) and ask_low[index] <= target
            if stop_hit:
                exit_price = stop - direction * slippage
            elif target_hit:
                exit_price = target - direction * slippage
            elif index - entry_bar >= max_hold:
                exit_price = (bid_close[index] if direction == 1 else ask_close[index]) - direction * slippage
            if np.isfinite(exit_price):
                pnl.append(direction * (exit_price - entry) * multiplier - commission)
                active = False
            elif trail > 0:
                if direction == 1:
                    stop = max(stop, bid_high[index] - trail * atrs[index])
                else:
                    stop = min(stop, ask_low[index] + trail * atrs[index])
    if active and len(frame):
        exit_price = (bid_close[-1] if direction == 1 else ask_close[-1]) - direction * slippage
        pnl.append(direction * (exit_price - entry) * multiplier - commission)
    values = np.asarray(pnl, dtype=float)
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(abs(values[values < 0].sum()))
    equity = request.initial_balance + np.cumsum(values)
    if equity.size:
        peaks = np.maximum.accumulate(np.r_[request.initial_balance, equity])[:-1]
        drawdowns = peaks - equity
        max_dd_pct = float(np.max(np.divide(drawdowns, peaks, out=np.zeros_like(drawdowns), where=peaks > 0)) * 100)
    else:
        max_dd_pct = 0.0
    return {
        "trades": int(len(values)),
        "net_profit": float(values.sum()),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "expected_payoff": float(values.mean()) if len(values) else None,
        "max_drawdown_pct": max_dd_pct,
    }
