from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from app.bridge import hypothesis_to_mql
from app.bridge import mt5_setup
from app.hypothesis.models import HypothesisSpec
from app.main import app


def test_hypothesis_export_writes_standalone_ea_set_and_spec(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(mt5_setup, "_resolve_mt5_paths", lambda: (_ for _ in ()).throw(RuntimeError("MT5 unavailable")))
    strategy = HypothesisSpec(
        strategy_id="sweep_reclaim_export_test",
        lineage="liquidity_sweep_reclaim",
        timeframe="m15",
        hypothesis="A swept XAUUSD swing low that closes back above the level can reverse.",
        parameters={
            "sweep_lookback": 24,
            "penetration_atr": 0.1,
            "reclaim_buffer_atr": 0.0,
            "wick_reject_min": 0.45,
            "close_location_min": 0.55,
            "atr_stop": 1.2,
            "reward_risk": 1.5,
            "max_hold_bars": 12,
            "context_filter": "avoid_h4_opposite",
            "direction_mode": "both",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
        },
    )

    result = hypothesis_to_mql.export(
        strategy,
        output_name="_test_hypothesis_sweep_reclaim",
        risk_fraction=0.015,
        daily_loss_pct=4.0,
        max_loss_pct=8.0,
        max_trades_per_day=6,
    )

    mq5_text = Path(result["mq5_path"]).read_text(encoding="utf-8")
    set_text = Path(result["set_path"]).read_text(encoding="utf-8")
    spec_text = Path(result["spec_path"]).read_text(encoding="utf-8")

    assert "void OnTick()" in mq5_text
    assert 'input string InpLineage = "liquidity_sweep_reclaim";' in mq5_text
    assert "bool RiskGuardOk()" in mq5_text
    assert "int h4_atr = iATR(_Symbol, PERIOD_H4, 14);" in mq5_text
    assert "InpSignalTimeframe=15" in set_text
    assert "InpRiskFraction=0.015" in set_text
    assert "InpMaxTradesPerDay=6" in set_text
    assert '"strategy_id": "sweep_reclaim_export_test"' in spec_text


def test_hypothesis_export_installs_copy_to_active_mt5_data_folder(tmp_path, monkeypatch) -> None:
    data_path = tmp_path / "terminal_data"
    include_path = data_path / "MQL5" / "Include" / "Trade"
    include_path.mkdir(parents=True)
    (include_path / "Trade.mqh").write_text("// standard lib placeholder", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(
        mt5_setup,
        "_resolve_mt5_paths",
        lambda: {"install": str(tmp_path / "terminal"), "data": str(data_path), "common": str(tmp_path / "common")},
    )
    strategy = HypothesisSpec(
        strategy_id="mt5_install_export_test",
        lineage="liquidity_sweep_reclaim",
        timeframe="m5",
        hypothesis="A swept XAUUSD swing low that closes back above the level can reverse.",
        parameters={
            "sweep_lookback": 24,
            "penetration_atr": 0.1,
            "reclaim_buffer_atr": 0.0,
            "wick_reject_min": 0.45,
            "close_location_min": 0.55,
            "atr_stop": 1.2,
            "reward_risk": 1.5,
            "max_hold_bars": 12,
            "context_filter": "none",
            "direction_mode": "both",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
        },
    )

    result = hypothesis_to_mql.export(strategy, output_name="_test_mt5_install_export")

    preferred = Path(result["preferred_mq5_path"])
    assert result["mt5_installed"] is True
    assert preferred.is_file()
    assert preferred.parent == data_path / "MQL5" / "Experts" / "BetterDiscovery" / "Hypothesis"
    assert Path(result["mt5_set_path"]).is_file()
    assert Path(result["mt5_spec_path"]).is_file()


def test_hypothesis_export_installs_copy_to_normal_terminal_when_portable_is_active(tmp_path, monkeypatch) -> None:
    appdata = tmp_path / "appdata"
    portable_data = appdata / "MetaQuotes" / "Terminal" / "PORTABLE"
    normal_data = appdata / "MetaQuotes" / "Terminal" / "NORMAL"
    for data_path, origin in [
        (portable_data, r"C:\Users\micha\AppData\Local\BetterDiscoveryResearch\mt5_portable"),
        (normal_data, r"C:\Program Files\MetaTrader 5"),
    ]:
        (data_path / "MQL5" / "Include" / "Trade").mkdir(parents=True)
        (data_path / "MQL5" / "Include" / "Trade" / "Trade.mqh").write_text("// standard lib placeholder", encoding="utf-8")
        (data_path / "origin.txt").write_text(origin, encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(
        mt5_setup,
        "_resolve_mt5_paths",
        lambda: {"install": str(tmp_path / "portable"), "data": str(portable_data), "common": str(tmp_path / "common")},
    )
    strategy = HypothesisSpec(
        strategy_id="mt5_multi_install_export_test",
        lineage="trend_pullback",
        timeframe="m5",
        hypothesis="A pullback continuation should export to every detected terminal.",
        parameters={
            "ema_length": 20,
            "pullback_atr": 0.5,
            "rsi_trigger": 50,
            "atr_stop": 1.2,
            "reward_risk": 1.5,
            "max_hold_bars": 12,
            "context_filter": "none",
            "direction_mode": "both",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
        },
    )

    result = hypothesis_to_mql.export(strategy, output_name="_test_mt5_multi_install_export")

    assert result["mt5_installed"] is True
    assert len(result["mt5_installs"]) == 2
    assert Path(result["mt5_mq5_path"]).is_file()
    assert Path(result["mt5_mq5_path"]).is_relative_to(normal_data)
    assert (portable_data / "MQL5" / "Experts" / "BetterDiscovery" / "Hypothesis" / "test_mt5_multi_install_export.mq5").is_file()
    assert (normal_data / "MQL5" / "Experts" / "BetterDiscovery" / "Hypothesis" / "test_mt5_multi_install_export.mq5").is_file()


def test_hypothesis_export_translates_strategy_grammar_rule_tree(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(mt5_setup, "_resolve_mt5_paths", lambda: (_ for _ in ()).throw(RuntimeError("MT5 unavailable")))
    strategy = HypothesisSpec(
        strategy_id="grammar_export_test",
        lineage="strategy_grammar",
        timeframe="m5",
        hypothesis="A generated rule tree should export as explicit MQL block functions.",
        parameters={
            "rule_blocks": [
                {"name": "liquidity_sweep_reclaim", "lookback": 24, "penetration_atr": 0.1, "timeframe": "m5"},
                {"name": "market_structure_shift", "swing_left": 2, "swing_right": 2, "timeframe": "m10"},
                {"name": "fair_value_gap", "mode": "new_or_retrace", "timeframe": "m15"},
            ],
            "block_logic": "all",
            "stop_mode": "structure",
            "direction_mode": "both",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
            "atr_stop": 1.0,
            "reward_risk": 1.0,
            "max_hold_bars": 8,
        },
    )

    result = hypothesis_to_mql.export(strategy, output_name="_test_grammar_export")
    mq5_text = Path(result["mq5_path"]).read_text(encoding="utf-8")
    set_text = Path(result["set_path"]).read_text(encoding="utf-8")

    assert 'input string InpLineage = "strategy_grammar";' in mq5_text
    assert "bool GrammarBlock0(const int direction)" in mq5_text
    assert "bool GrammarBlock1(const int direction)" in mq5_text
    assert "bool GrammarBlock2(const int direction)" in mq5_text
    assert 'else if(InpLineage == "strategy_grammar")' in mq5_text
    assert "ENUM_TIMEFRAMES tf = PERIOD_M5;" in mq5_text
    assert "ENUM_TIMEFRAMES tf = PERIOD_M10;" in mq5_text
    assert "ENUM_TIMEFRAMES tf = PERIOD_M15;" in mq5_text
    assert "GLatestSwingLow(tf, 2, 2, level)" in mq5_text
    assert "InpGrammarStopMode=structure" in set_text


def test_hypothesis_export_route_accepts_ui_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(mt5_setup, "_resolve_mt5_paths", lambda: (_ for _ in ()).throw(RuntimeError("MT5 unavailable")))
    client = TestClient(app)

    response = client.post(
        "/mql/hypothesis-export",
        json={
            "strategy": {
                "strategy_id": "route_export_test",
                "lineage": "trend_pullback",
                "timeframe": "m15",
                "hypothesis": "An aligned XAUUSD pullback can continue when momentum reclaims.",
                "parameters": {
                    "ema_length": 20,
                    "pullback_atr": 0.5,
                    "rsi_trigger": 50,
                    "atr_stop": 1.2,
                    "reward_risk": 1.4,
                    "max_hold_bars": 16,
                    "direction_mode": "both",
                    "session_start_utc": 0,
                    "session_end_utc": 24,
                    "volatility_filter": "none",
                },
            },
            "output_name": "_test_hypothesis_route_export",
            "risk_fraction": 0.01,
            "daily_loss_pct": 3.0,
            "max_loss_pct": 8.0,
            "max_trades_per_day": 4,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert Path(body["mq5_path"]).is_file()
    assert Path(body["set_path"]).is_file()
    assert Path(body["spec_path"]).is_file()
