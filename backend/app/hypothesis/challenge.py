from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .models import FTMOChallengeConfig


PROP_FAIL_REASONS = {"daily_loss", "max_loss"}


def _prepare_trades(ledger: pd.DataFrame) -> pd.DataFrame:
    required = {"entry_time", "exit_time", "net_pnl", "initial_risk"}
    missing = required - set(ledger.columns)
    if missing:
        raise ValueError(f"closed-trade ledger missing required columns: {sorted(missing)}")
    if ledger.empty:
        return pd.DataFrame(columns=["entry_time", "exit_time", "r_multiple"])
    trades = ledger[list(required)].copy()
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True)
    trades["initial_risk"] = pd.to_numeric(trades["initial_risk"], errors="coerce")
    trades["net_pnl"] = pd.to_numeric(trades["net_pnl"], errors="coerce")
    trades = trades[(trades["initial_risk"] > 0) & trades["net_pnl"].notna()].copy()
    trades["r_multiple"] = trades["net_pnl"] / trades["initial_risk"]
    trades = trades.replace([np.inf, -np.inf], np.nan).dropna(subset=["r_multiple"])
    return trades[["entry_time", "exit_time", "r_multiple"]].sort_values("entry_time").reset_index(drop=True)


def _attempt_starts(trades: pd.DataFrame, config: FTMOChallengeConfig) -> pd.DatetimeIndex:
    if trades.empty:
        return pd.DatetimeIndex([], tz="UTC")
    first = pd.Timestamp(trades["entry_time"].min()).floor("D")
    last = pd.Timestamp(trades["entry_time"].max()).floor("D")
    latest_full_start = last - pd.Timedelta(days=config.max_attempt_days)
    if latest_full_start < first:
        latest_full_start = first
    try:
        return pd.date_range(first, latest_full_start, freq=config.start_frequency, tz="UTC")
    except ValueError as exc:
        raise ValueError(f"invalid challenge start_frequency: {config.start_frequency}") from exc


def simulate_attempt(
    trades: pd.DataFrame,
    start: pd.Timestamp,
    *,
    risk_fraction: float,
    internal_daily_stop_pct: float,
    max_trades_per_day: int,
    config: FTMOChallengeConfig,
) -> dict[str, Any]:
    start = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start).tz_localize("UTC")
    end = start + pd.Timedelta(days=config.max_attempt_days)
    window = trades[(trades["entry_time"] >= start) & (trades["entry_time"] < end)]
    if window.empty:
        return {
            "start": start.isoformat(),
            "status": "no_trades",
            "fail_reason": None,
            "days_to_target": None,
            "trades_taken": 0,
            "ending_equity": config.initial_balance,
            "max_drawdown_pct": 0.0,
        }

    target_equity = config.initial_balance * (1.0 + config.target_profit_pct / 100.0)
    max_loss_floor = config.initial_balance * (1.0 - config.max_loss_pct / 100.0)
    daily_loss_limit = config.initial_balance * config.daily_loss_pct / 100.0
    internal_daily_limit = config.initial_balance * internal_daily_stop_pct / 100.0

    equity = config.initial_balance
    peak = equity
    max_drawdown_pct = 0.0
    current_day = None
    daily_start_equity = equity
    trades_today = 0
    halted_today = False
    trades_taken = 0
    skipped_by_day_limit = 0
    internal_halts = 0

    for row in window.itertuples(index=False):
        entry_time = pd.Timestamp(row.entry_time)
        entry_day = entry_time.date()
        if current_day != entry_day:
            current_day = entry_day
            daily_start_equity = equity
            trades_today = 0
            halted_today = False

        if halted_today:
            continue
        if trades_today >= max_trades_per_day:
            skipped_by_day_limit += 1
            continue

        trades_today += 1
        trades_taken += 1
        pnl = equity * risk_fraction * float(row.r_multiple)
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - equity) / peak * 100.0)

        if equity >= target_equity:
            return {
                "start": start.isoformat(),
                "status": "pass",
                "fail_reason": None,
                "days_to_target": float((entry_time - start).total_seconds() / 86_400.0),
                "trades_taken": trades_taken,
                "ending_equity": float(equity),
                "max_drawdown_pct": float(max_drawdown_pct),
                "skipped_by_day_limit": skipped_by_day_limit,
                "internal_halts": internal_halts,
            }
        if equity <= max_loss_floor:
            return {
                "start": start.isoformat(),
                "status": "fail",
                "fail_reason": "max_loss",
                "days_to_target": None,
                "trades_taken": trades_taken,
                "ending_equity": float(equity),
                "max_drawdown_pct": float(max_drawdown_pct),
                "skipped_by_day_limit": skipped_by_day_limit,
                "internal_halts": internal_halts,
            }

        daily_loss = daily_start_equity - equity
        if daily_loss >= daily_loss_limit:
            return {
                "start": start.isoformat(),
                "status": "fail",
                "fail_reason": "daily_loss",
                "days_to_target": None,
                "trades_taken": trades_taken,
                "ending_equity": float(equity),
                "max_drawdown_pct": float(max_drawdown_pct),
                "skipped_by_day_limit": skipped_by_day_limit,
                "internal_halts": internal_halts,
            }
        if daily_loss >= internal_daily_limit:
            halted_today = True
            internal_halts += 1

    return {
        "start": start.isoformat(),
        "status": "timeout",
        "fail_reason": None,
        "days_to_target": None,
        "trades_taken": trades_taken,
        "ending_equity": float(equity),
        "max_drawdown_pct": float(max_drawdown_pct),
        "skipped_by_day_limit": skipped_by_day_limit,
        "internal_halts": internal_halts,
    }


def summarize_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(attempts)
    if total == 0:
        return {
            "start_windows": 0,
            "active_starts": 0,
            "pass_count": 0,
            "prop_fail_count": 0,
            "pass_rate": 0.0,
            "active_pass_rate": 0.0,
            "prop_fail_rate": 0.0,
            "median_days_to_target": None,
            "best_days_to_target": None,
            "median_trades_to_target": None,
            "mean_max_drawdown_pct": 0.0,
            "best_month_pass_share": None,
        }
    statuses = pd.Series([item["status"] for item in attempts])
    pass_attempts = [item for item in attempts if item["status"] == "pass"]
    prop_fails = [
        item for item in attempts
        if item["status"] == "fail" and item.get("fail_reason") in PROP_FAIL_REASONS
    ]
    active = [item for item in attempts if item["status"] != "no_trades"]
    pass_days = [float(item["days_to_target"]) for item in pass_attempts if item["days_to_target"] is not None]
    pass_trades = [int(item["trades_taken"]) for item in pass_attempts]
    best_month_share = None
    if pass_attempts:
        months = pd.Series([pd.Timestamp(item["start"]).strftime("%Y-%m") for item in pass_attempts])
        best_month_share = float(months.value_counts().iloc[0] / len(pass_attempts))
    drawdowns = [float(item.get("max_drawdown_pct", 0.0)) for item in attempts]
    return {
        "start_windows": total,
        "active_starts": len(active),
        "pass_count": len(pass_attempts),
        "prop_fail_count": len(prop_fails),
        "timeout_count": int((statuses == "timeout").sum()),
        "no_trade_count": int((statuses == "no_trades").sum()),
        "pass_rate": float(len(pass_attempts) / total),
        "active_pass_rate": float(len(pass_attempts) / len(active)) if active else 0.0,
        "prop_fail_rate": float(len(prop_fails) / total),
        "median_days_to_target": float(np.median(pass_days)) if pass_days else None,
        "best_days_to_target": float(np.min(pass_days)) if pass_days else None,
        "median_trades_to_target": float(np.median(pass_trades)) if pass_trades else None,
        "mean_max_drawdown_pct": float(np.mean(drawdowns)) if drawdowns else 0.0,
        "best_month_pass_share": best_month_share,
    }


def score_challenge(summary: dict[str, Any]) -> float:
    median_days = summary["median_days_to_target"]
    days_penalty = float(median_days if median_days is not None else 30.0)
    concentration = float(summary["best_month_pass_share"] or 1.0)
    return (
        120.0 * float(summary["active_pass_rate"])
        + 60.0 * float(summary["pass_rate"])
        - 250.0 * float(summary["prop_fail_rate"])
        - 4.0 * days_penalty
        - 1.5 * float(summary["mean_max_drawdown_pct"])
        - 20.0 * max(0.0, concentration - 0.4)
    )


def evaluate_challenge(
    ledger: pd.DataFrame,
    *,
    risk_fraction: float,
    internal_daily_stop_pct: float,
    max_trades_per_day: int,
    config: FTMOChallengeConfig,
) -> dict[str, Any]:
    trades = _prepare_trades(ledger)
    starts = _attempt_starts(trades, config)
    attempts = [
        simulate_attempt(
            trades,
            start,
            risk_fraction=risk_fraction,
            internal_daily_stop_pct=internal_daily_stop_pct,
            max_trades_per_day=max_trades_per_day,
            config=config,
        )
        for start in starts
    ]
    summary = summarize_attempts(attempts)
    summary.update({
        "risk_fraction": float(risk_fraction),
        "internal_daily_stop_pct": float(internal_daily_stop_pct),
        "max_trades_per_day": int(max_trades_per_day),
        "score": float(score_challenge(summary)),
    })
    return {"summary": summary, "attempts": attempts}


def evaluate_challenge_grid(ledger: pd.DataFrame, config: FTMOChallengeConfig) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for risk_fraction in config.risk_fractions:
        for internal_daily_stop_pct in config.internal_daily_stop_pcts:
            for max_trades_per_day in config.max_trades_per_day_options:
                results.append(
                    evaluate_challenge(
                        ledger,
                        risk_fraction=risk_fraction,
                        internal_daily_stop_pct=internal_daily_stop_pct,
                        max_trades_per_day=max_trades_per_day,
                        config=config,
                    )
                )
    best = max(results, key=lambda item: item["summary"]["score"]) if results else {
        "summary": summarize_attempts([]),
        "attempts": [],
    }
    return {
        "best": best,
        "grid": results,
    }
