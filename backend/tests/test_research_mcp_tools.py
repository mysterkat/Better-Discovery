from __future__ import annotations

import importlib.util
from pathlib import Path


def test_mcp_exposes_current_research_funnel() -> None:
    path = Path(__file__).resolve().parents[1] / "tools" / "research_mcp.py"
    spec = importlib.util.spec_from_file_location("research_mcp", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = {tool["name"] for tool in module.TOOLS}
    assert {
        "import_market_data", "run_discovery", "run_local_replay",
        "run_mt5_pipeline", "compare_local_mt5_monte_carlo",
        "list_hypothesis_families", "run_hypothesis_discovery",
        "export_hypothesis_ea",
    }.issubset(names)
