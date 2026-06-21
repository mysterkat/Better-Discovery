"""Regression tests for discovery-to-MT5 behavioral fidelity."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.toolkit import pattern_discovery_v6 as pd6


def test_htf_alignment_uses_only_previous_completed_bar() -> None:
    base_index = pd.DatetimeIndex(
        ["2024-01-01 00:30", "2024-01-01 01:30"], name="time"
    )
    htf_index = pd.DatetimeIndex(
        ["2024-01-01 00:00", "2024-01-01 01:00"], name="time"
    )
    base = pd.DataFrame({"trend": [0, 0]}, index=base_index)
    htf = pd.DataFrame({"trend": [1, -1], "rsi14": [41.0, 77.0]}, index=htf_index)

    out = pd6._align_htf(base, htf, "tf2")

    assert pd.isna(out.loc[base_index[0], "tf2_rsi14"])
    assert out.loc[base_index[1], "tf2_rsi14"] == pytest.approx(41.0)
    assert out.loc[base_index[1], "mtf_bull_score"] == 1


def test_set_export_preserves_discrete_rule_states(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pd6, "TF1_FILE", "xauusd_m10.csv")
    monkeypatch.setattr(pd6, "TF2_FILE", "xauusd_m30.csv")
    monkeypatch.setattr(pd6, "TF3_FILE", "xauusd_h1.csv")
    monkeypatch.setattr(pd6, "TF4_FILE", "xauusd_h4.csv")
    monkeypatch.setattr(pd6, "TF5_FILE", "")
    monkeypatch.setattr(pd6, "PRIMARY_TF", 1)
    monkeypatch.setattr(pd6, "HTF_DIV_TF", 4)

    target = tmp_path / "pattern.set"
    pd6.generate_set_file(
        1,
        12,
        "LONG",
        {
            "regime": (0.095, 2.745),
            "sd_zone": (-1.356, 0.466),
            "mtf_bull_score": (0.188, 5.923),
        },
        0.00105,
        0.00134,
        "LONG_ONLY",
        None,
        {},
        target,
    )
    text = target.read_text(encoding="utf-8")

    assert "regime_lo=1.000000" in text
    assert "regime_hi=2.000000" in text
    assert "sd_zone_lo=-1.000000" in text
    assert "sd_zone_hi=0.000000" in text
    assert "mtf_bull_score_lo=1.000000" in text
    assert "mtf_bull_score_hi=5.000000" in text
    assert "HtfDivSignalSlot=3" in text


def test_metrics_include_net_expectancy_and_breakeven() -> None:
    trades = [
        (0, "WIN", 1.5),
        (1, "LOSS", -1.0),
        (2, "LOSS", -0.5),
    ]
    metrics = pd6._calc_metrics(trades, member_count=3, trading_days=3)

    assert metrics["expectancy_r"] == pytest.approx(0.0)
    assert metrics["avg_loss_r"] == pytest.approx(0.75)
    assert metrics["breakeven_wr"] == pytest.approx(33.3)


def test_ea_source_uses_parity_algorithms() -> None:
    source = (Path(__file__).parents[1] / "ea" / "PatternDiscoveryEA.mq5").read_text(
        encoding="utf-8"
    )

    assert "sShift = iBarShift(_Symbol, g_signalTFs[i], barTime, false) + 1" in source
    assert "g_nSignals - 1" in source
    assert "CopyTickVolume(_Symbol, PERIOD_CURRENT, shift, 20" in source
    assert "LinearQuantile(atrHistory, 0.50)" in source

    mt5_root = Path(__file__).parents[1] / "mt5"
    mtf = (mt5_root / "indicators" / "BD_MtfBullScore.mq5").read_text(encoding="utf-8")
    htf_div = (mt5_root / "indicators" / "BD_HtfDiv.mq5").read_text(encoding="utf-8")
    feature_dump = (mt5_root / "services" / "BD_FeatureDump.mq5").read_text(encoding="utf-8")
    assert "barTime, false) + 1" in mtf
    assert "time[i], false) + 1" in htf_div
    assert "for(int k=0;k<20;k++)" in feature_dump
