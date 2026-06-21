"""Monte Carlo and parity comparison for local replay versus native MT5."""

from __future__ import annotations

import pandas as pd

from ..bridge import mc as mc_bridge
from ..schemas.mc import MCCompareRequest
from .report import parse_mt5_report


def _headline(result: dict) -> dict:
    return {
        "phase1_pass_rate": result.get("phase1", {}).get("pass_rate"),
        "phase2_pass_rate": result.get("phase2", {}).get(
            "combined_pass_rate", result.get("phase2", {}).get("pass_rate")
        ),
        "funded_payout_rate": result.get("funded", {}).get("payout_rate"),
        "funded_breach_rate": result.get("funded", {}).get("breach_rate"),
        "longterm_pass_rate": result.get("longterm", {}).get("pass_rate"),
        "longterm_median_max_dd": result.get("longterm", {}).get("median_max_dd"),
    }


def compare_sources(req: MCCompareRequest) -> dict:
    local_pnl = mc_bridge.load_daily_pnl("local_ledger", req.local_ledger_path)
    mt5_pnl = mc_bridge.load_daily_pnl("mt5_html", req.mt5_report_path)
    if not len(local_pnl) or not len(mt5_pnl):
        raise ValueError("both local and MT5 sources must contain closed trades")
    global_params = dict(req.global_params)
    global_params.setdefault("seed", 42)
    arguments = (
        global_params, dict(req.phase1_params), dict(req.phase2_params),
        dict(req.funded_params), dict(req.longterm_params),
    )
    local = mc_bridge.run_all_phases(
        local_pnl, *arguments,
        regime_data=mc_bridge.compute_regime_from_file("local_ledger", req.local_ledger_path),
    )
    mt5 = mc_bridge.run_all_phases(
        mt5_pnl, *arguments,
        regime_data=mc_bridge.compute_regime_from_file("mt5_html", req.mt5_report_path),
    )
    ledger = (
        pd.read_parquet(req.local_ledger_path)
        if req.local_ledger_path.lower().endswith(".parquet")
        else pd.read_csv(req.local_ledger_path)
    )
    mt5_report = parse_mt5_report(req.mt5_report_path)
    local_trades, mt5_trades = len(ledger), int(mt5_report.total_trades or 0)
    local_net, mt5_net = float(ledger["net_pnl"].sum()), float(mt5_report.net_profit or 0.0)
    trade_delta = 100 * abs(local_trades - mt5_trades) / max(mt5_trades, 1)
    net_delta = 100 * abs(local_net - mt5_net) / max(abs(mt5_net), 1.0)
    local_headline, mt5_headline = _headline(local), _headline(mt5)
    deltas = {
        key: float(local_headline[key]) - float(mt5_headline[key])
        if local_headline[key] is not None and mt5_headline[key] is not None else None
        for key in local_headline
    }
    return {
        "settings": {
            "global": global_params, "phase1": req.phase1_params, "phase2": req.phase2_params,
            "funded": req.funded_params, "longterm": req.longterm_params,
        },
        "parity": {
            "decision": "pass" if (
                trade_delta <= req.max_trade_count_delta_pct
                and net_delta <= req.max_net_profit_delta_pct
            ) else "block",
            "local_trades": local_trades, "mt5_trades": mt5_trades,
            "trade_count_delta_pct": trade_delta, "local_net_profit": local_net,
            "mt5_net_profit": mt5_net, "net_profit_delta_pct": net_delta,
        },
        "local": local, "mt5": mt5,
        "headlines": {"local": local_headline, "mt5": mt5_headline, "delta": deltas},
    }
