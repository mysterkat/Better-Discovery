from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.models import PromotionPolicy, ReportMetrics, StrategySpec
from app.research.report import parse_mt5_report
from app.research.service import ResearchService, evaluate
from app.bridge.set_to_mql import parse_set_file


def _set_file(path: Path) -> Path:
    path.write_text(
        """; Pattern 1 - Cluster 12 [LONG] [LONG_ONLY]
; Train: WR=54.7% Wilson=51.6% PF=1.52 Score=1.16
; Test: WR=45.1% PF=1.02 Trades=144
MagicNumber=10001
DirectionMode=0
SL_Pct=0.00105
TP_Pct=0.00134
regime_lo=1
regime_hi=2
""",
        encoding="utf-8",
    )
    return path


def test_strategy_fingerprint_ignores_source_path(tmp_path: Path) -> None:
    first = StrategySpec.from_set(_set_file(tmp_path / "first.set"))
    second_path = tmp_path / "second.set"
    second_path.write_text((tmp_path / "first.set").read_text(), encoding="utf-8")
    second = StrategySpec.from_set(second_path)
    assert first.fingerprint != second.fingerprint  # names identify generated EAs
    second.name = first.name
    assert first.fingerprint == second.fingerprint


def test_gate_is_mechanical() -> None:
    result = evaluate(
        ReportMetrics(
            total_trades=104,
            profit_factor=0.96,
            expected_payoff=-1.11,
            net_profit=-115.32,
            maximal_equity_drawdown_pct=5.57,
        ),
        PromotionPolicy(),
    )
    assert result.decision == "reject"
    assert any("profit factor" in reason for reason in result.failed)
    assert any("drawdown" in reason for reason in result.passed)


def test_legacy_superset_header_is_distinguished_from_ea_oos() -> None:
    legacy = parse_set_file(
        "; SignalRetention=74.9 (EA box is a superset -> live inflates)\n"
        "; Test: WR=56.6% PF=1.93 Trades=106\nMagicNumber=10001\n"
    )["meta"]
    current = parse_set_file(
        "; SignalRetention=74.9 (EA box is a superset -> live inflates)\n"
        "; Test: WR=56.6% PF=1.93 Trades=814 "
        "(EA-faithful box-only OOS - compare THIS to MT5)\nMagicNumber=10001\n"
    )["meta"]
    assert legacy["box_superset_warning"] is True
    assert "ea_faithful_oos" not in legacy
    assert current["ea_faithful_oos"] is True


def test_variant_requires_hypothesis_and_freezes_after_lockbox(tmp_path: Path) -> None:
    service = ResearchService(tmp_path / "research.sqlite3")
    source = _set_file(tmp_path / "candidate.set")
    strategy = StrategySpec.from_set(source)
    with pytest.raises(ValueError, match="at least 20"):
        service.create_variant(str(source), {"regime_lo": 2}, "squeeze bad")

    experiment_id = service.store.create(
        "mt5_backtest", {}, strategy.fingerprint, dataset_role="lockbox"
    )
    service.store.finish(experiment_id, {"report": "done"})
    with pytest.raises(ValueError, match="frozen"):
        service.create_variant(
            str(source),
            {"regime_lo": 2},
            "Exclude squeeze regime because its independent expectancy is negative.",
        )


def test_local_and_mt5_lockboxes_are_tracked_separately(tmp_path: Path) -> None:
    service = ResearchService(tmp_path / "research.sqlite3")
    source = _set_file(tmp_path / "candidate.set")
    fingerprint = StrategySpec.from_set(source).fingerprint
    local_id = service.store.create(
        "local_replay", {}, fingerprint, dataset_role="lockbox"
    )
    service.store.finish(local_id, {"ledger": "done"})
    assert service.store.has_completed_lockbox(fingerprint)
    assert service.store.has_completed_lockbox(fingerprint, kind="local_replay")
    assert not service.store.has_completed_lockbox(fingerprint, kind="mt5_backtest")


def test_parse_minimal_mt5_report(tmp_path: Path) -> None:
    report = tmp_path / "report.html"
    report.write_text(
        """<html><table>
<tr><td>Settings</td></tr>
<tr><td>Expert:</td><td><b>candidate</b></td></tr>
<tr><td>Symbol:</td><td><b>XAUUSD</b></td></tr>
<tr><td>Period:</td><td><b>M10 (2026.01.01 - 2026.06.01)</b></td></tr>
<tr><td>Inputs:</td><td><b>MagicNumber=10001</b></td></tr>
<tr><td>Results</td></tr>
<tr><td>Total Net Profit:</td><td><b>125.50</b></td></tr>
<tr><td>Gross Profit:</td><td><b>1 200.00</b></td></tr>
<tr><td>Gross Loss:</td><td><b>-1 074.50</b></td></tr>
<tr><td>Profit Factor:</td><td><b>1.12</b></td></tr>
<tr><td>Expected Payoff:</td><td><b>1.26</b></td></tr>
<tr><td>Equity Drawdown Maximal:</td><td><b>561.00 (5.61%)</b></td></tr>
<tr><td>Total Trades:</td><td><b>100</b></td></tr>
<tr><td>Profit Trades (% of total):</td><td><b>45 (45.00%)</b></td></tr>
</table></html>""",
        encoding="utf-8",
    )
    metrics = parse_mt5_report(report)
    assert metrics.expert == "candidate"
    assert metrics.inputs == {"MagicNumber": "10001"}
    assert metrics.total_trades == 100
    assert metrics.win_rate_pct == 45.0
    assert metrics.maximal_equity_drawdown_pct == 5.61
    assert metrics.closed_trades_parsed == 0
