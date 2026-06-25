from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

from backend.app.bridge import mc as mc_bridge


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLKIT = REPO_ROOT / "backend" / "toolkit"
if str(TOOLKIT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT))

import mc_funded_test as mc_toolkit  # noqa: E402


def test_get_daily_pnl_includes_zero_calendar_days():
    frame = pd.DataFrame(
        {
            "trade_date": [date(2026, 1, 1), date(2026, 1, 3)],
            "net_profit": [100.0, 50.0],
        }
    )
    frame.attrs["calendar_start"] = date(2026, 1, 1)
    frame.attrs["calendar_end"] = date(2026, 1, 4)

    assert mc_toolkit.get_daily_pnl(frame, 1.0).tolist() == [100.0, 0.0, 50.0, 0.0]


def test_local_ledger_loader_includes_zero_calendar_days(tmp_path):
    ledger = tmp_path / "ledger.csv"
    pd.DataFrame(
        {
            "exit_time": ["2026-01-01T12:00:00Z", "2026-01-03T12:00:00Z"],
            "net_pnl": [100.0, 50.0],
        }
    ).to_csv(ledger, index=False)

    assert mc_bridge.load_daily_pnl("local_ledger", str(ledger)).tolist() == [
        100.0,
        0.0,
        50.0,
    ]


def test_combined_days_to_funded_uses_paired_pass_distribution():
    p1 = {
        "days_p50": 10,
        "results_df": {
            "records": [
                {"passed": True, "days": 1},
                {"passed": True, "days": 100},
                {"passed": False, "days": 365},
                {"passed": True, "days": 200},
            ]
        },
    }
    p2 = {
        "days_p50": 20,
        "results_df": {
            "records": [
                {"passed": True, "days": 1000},
                {"passed": False, "days": 365},
                {"passed": True, "days": 1},
            ]
        },
    }

    assert mc_bridge._combined_days_to_funded(p1, p2) == 601.0
