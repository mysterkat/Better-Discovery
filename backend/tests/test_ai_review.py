"""ai_review must be strictly optional: graceful no-op without a server/key."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "toolkit"))

import ai_review
import pattern_discovery_v6 as pd6


def _fake_result() -> dict:
    return {
        "pattern_id": "C1_LONG_seed1", "cluster": 1, "direction": "LONG",
        "bidir_mode": "LONG_ONLY", "marginal": False, "soft_fail": None,
        "win_rate_": 53.1, "profit_factor": 1.31, "total_trades": 412,
        "per_day": 0.8, "max_drawdown_r": 7.5,
        "test_wr": 51.0, "test_pf": 1.12, "test_trades": 88,
        "ea_test_wr": 50.2, "ea_test_pf": 1.09, "ea_test_trades": 130,
        "box_inflation": 1.48, "sl_pct": 0.004, "tp_pct": 0.005,
        "implied_rr": 1.25, "consistency": 0.6, "degrading": False,
        "genetic_rule": {"rsi14": (35.0, 62.0), "trend": (0.0, 1.0)},
        "seed": 1,
    }


def test_payload_is_valid_json_and_compact():
    results = [_fake_result()] * 20
    payload = ai_review.build_user_payload(results, {"TARGET_PF": 1.5})
    doc = json.loads(payload)
    assert len(doc["patterns"]) == ai_review.MAX_PATTERNS_SENT
    assert doc["patterns_omitted_lower_ranked"] == 20 - ai_review.MAX_PATTERNS_SENT
    assert doc["run_config"]["TARGET_PF"] == 1.5
    p = doc["patterns"][0]
    assert p["ea_oos_pf"] == 1.09 and p["gated_oos_pf"] == 1.12
    assert p["rule"]["rsi14"] == [35.0, 62.0]


def test_config_resolution_defaults(monkeypatch):
    for var in ("BD_AI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                "BD_AI_BASE_URL", "BD_AI_MODEL", "BD_AI_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    cfg = ai_review._resolve_config()
    assert cfg["base_url"] == "http://localhost:11434/v1"   # local, no key
    assert cfg["api_key"] == ""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = ai_review._resolve_config()
    assert cfg["base_url"] == "https://api.deepseek.com/v1"
    assert cfg["model"] == "deepseek-chat"


def test_review_run_degrades_gracefully(tmp_path, monkeypatch):
    """Unreachable endpoint → returns None, writes nothing, raises nothing."""
    for var in ("BD_AI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    out = ai_review.review_run(
        [_fake_result()], {"TARGET_PF": 1.5}, tmp_path, seed=1,
        base_url="http://127.0.0.1:9",   # discard port — guaranteed refused
        model="none", timeout_s=2)
    assert out is None
    assert list(tmp_path.iterdir()) == []


def test_results_summary_entry_is_json_safe():
    entry = pd6._results_summary_entry(_fake_result())
    json.dumps(entry)   # must not raise
    assert entry["genetic_rule"]["rsi14"] == [35.0, 62.0]
    snap = pd6._run_config_snapshot()
    json.dumps(snap)    # must not raise
    assert "HARD_FILTERS" in snap and "pf_floor" in snap
