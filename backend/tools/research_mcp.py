"""MCP stdio server for autonomous BETTER DISCOVERY research.

The server intentionally exposes no live trading or deployment operation.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.research.models import BacktestSpec, MT5Environment, PromotionPolicy  # noqa: E402
from app.research.service import RESEARCH  # noqa: E402
from app.bridge import hypothesis_to_mql  # noqa: E402
from app.external_data import EXTERNAL_DATA, ExternalDataImportRequest  # noqa: E402
from app.hypothesis.grammar import BUILDERS as HYPOTHESIS_BUILDERS  # noqa: E402
from app.hypothesis.models import HypothesisDiscoveryRequest, HypothesisSpec  # noqa: E402
from app.hypothesis.service import HypothesisResearchService  # noqa: E402
from app.market_data.models import MarketDataImportRequest  # noqa: E402
from app.local_replay.models import ReplayRequest  # noqa: E402
from app.local_replay.robustness import RobustnessRequest  # noqa: E402
from app.schemas.mc import MCCompareRequest  # noqa: E402


HYPOTHESIS = HypothesisResearchService()


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        value["required"] = required
    return value


TOOLS: list[dict[str, Any]] = [
    {"name": "research_status", "description": "Check MT5 paths and research guardrails.",
     "inputSchema": _schema({})},
    {"name": "setup_portable_mt5", "description": "Create a writable, research-only portable MT5 runner for native report generation.",
     "inputSchema": _schema({"destination": {"type": "string"}})},
    {"name": "run_discovery", "description": "Run BETTER DISCOVERY with whitelisted overrides and record the candidates.",
     "inputSchema": _schema({"overrides": {"type": "object"}})},
    {"name": "list_hypothesis_families", "description": "List deterministic FTMO hypothesis families supported by the bar-based XAUUSD research engine.",
     "inputSchema": _schema({})},
    {"name": "run_hypothesis_discovery", "description": "Run the deterministic XAUUSD hypothesis-family discovery engine on a manifest-backed bar dataset.",
     "inputSchema": _schema({"request": {"type": "object", "description": "HypothesisDiscoveryRequest fields, including optional parallel_workers."}}, ["request"])},
    {"name": "list_market_datasets", "description": "List local manifest-backed market datasets available to Better Discovery.",
     "inputSchema": _schema({})},
    {"name": "inspect_market_dataset", "description": "Inspect one local market dataset manifest, including symbols, timeframes, file ranges, and quality metadata.",
     "inputSchema": _schema({"dataset_id": {"type": "string"}}, ["dataset_id"])},
    {"name": "sample_market_bars", "description": "Return a bounded sample of local MT5/imported bars for Codex-driven strategy research.",
     "inputSchema": _schema({
         "dataset_id": {"type": "string"},
         "symbol": {"type": "string", "default": "XAUUSD"},
         "timeframe": {"type": "string", "default": "m5"},
         "date_from": {"type": "string"},
         "date_to": {"type": "string"},
         "limit": {"type": "integer", "default": 200},
     }, ["dataset_id", "date_from", "date_to"])},
    {"name": "analyze_market_mind", "description": "Analyze years of local bar data and return the 3.4 Market Mind regime plan used to bias grammar discovery.",
     "inputSchema": _schema({
         "dataset_id": {"type": "string"},
         "symbol": {"type": "string", "default": "XAUUSD"},
         "timeframe": {"type": "string", "default": "m5"},
         "date_from": {"type": "string"},
         "date_to": {"type": "string"},
         "bias_pct": {"type": "number", "default": 0.70},
     }, ["dataset_id", "date_from", "date_to"])},
    {"name": "list_external_data", "description": "List imported COT/VIX/GVZ/gamma context datasets available to Market Mind.",
     "inputSchema": _schema({})},
    {"name": "import_external_data", "description": "Import a COT, VIX, GVZ, or gamma CSV/URL into the local Market Mind context store.",
     "inputSchema": _schema({"request": {"type": "object", "description": "ExternalDataImportRequest fields."}}, ["request"])},
    {"name": "external_context", "description": "Return the latest no-lookahead external context snapshot for a symbol as of a timestamp.",
     "inputSchema": _schema({
         "symbol": {"type": "string", "default": "XAUUSD"},
         "as_of": {"type": "string"},
     }, ["as_of"])},
    {"name": "export_hypothesis_ea", "description": "Export one HypothesisSpec to a standalone MQL5 EA, .set file, and hypothesis JSON.",
     "inputSchema": _schema({
         "strategy": {"type": "object", "description": "HypothesisSpec fields."},
         "output_name": {"type": "string"},
         "risk_fraction": {"type": "number", "default": 0.01},
         "daily_loss_pct": {"type": "number", "default": 4.0},
         "max_loss_pct": {"type": "number", "default": 8.0},
         "max_trades_per_day": {"type": "integer", "default": 4},
         "max_spread_points": {"type": "number", "default": 80.0},
     }, ["strategy"])},
    {"name": "import_market_data", "description": "Import canonical provider ticks/bars and publish validated discovery CSVs.",
     "inputSchema": _schema({"request": {"type": "object", "description": "MarketDataImportRequest fields."}}, ["request"])},
    {"name": "list_candidates", "description": "List discovered .set candidates, newest first.",
     "inputSchema": _schema({"root": {"type": "string"}, "limit": {"type": "integer", "default": 50}})},
    {"name": "import_strategy", "description": "Create the canonical immutable JSON spec for a .set strategy.",
     "inputSchema": _schema({"set_path": {"type": "string"}}, ["set_path"])},
    {"name": "create_strategy_variant", "description": "Create a traceable variant. A concrete market hypothesis is mandatory.",
     "inputSchema": _schema({
         "set_path": {"type": "string"},
         "parameter_overrides": {"type": "object"},
         "hypothesis": {"type": "string", "minLength": 20},
     }, ["set_path", "parameter_overrides", "hypothesis"])},
    {"name": "run_mt5_pipeline", "description": "Generate, compile, backtest, parse, and gate one strategy in MT5.",
     "inputSchema": _schema({
         "set_path": {"type": "string"},
         "backtest": {"type": "object", "description": "BacktestSpec fields including symbol, timeframe, date_from, date_to."},
         "environment": {"type": "object"},
         "policy": {"type": "object"},
     }, ["set_path", "backtest"])},
    {"name": "run_local_replay", "description": "Run deterministic bid/ask tick replay and export a canonical closed-trade ledger.",
     "inputSchema": _schema({"request": {"type": "object", "description": "ReplayRequest fields."}}, ["request"])},
    {"name": "run_local_robustness", "description": "Run block permutation and chronological walk-forward permutation gates on a local ledger.",
     "inputSchema": _schema({"request": {"type": "object", "description": "RobustnessRequest fields."}}, ["request"])},
    {"name": "compare_local_mt5_monte_carlo", "description": "Run identical Monte Carlo settings on local and MT5 trades and enforce parity gates.",
     "inputSchema": _schema({"request": {"type": "object", "description": "MCCompareRequest fields."}}, ["request"])},
    {"name": "parse_mt5_report", "description": "Parse and mechanically gate an existing MT5 HTML report.",
     "inputSchema": _schema({"report_path": {"type": "string"}, "policy": {"type": "object"}}, ["report_path"])},
    {"name": "list_experiments", "description": "List recorded research experiments.",
     "inputSchema": _schema({"limit": {"type": "integer", "default": 50}})},
    {"name": "get_experiment", "description": "Retrieve one experiment, including request, metrics, artifacts, and errors.",
     "inputSchema": _schema({"experiment_id": {"type": "string"}}, ["experiment_id"])},
]


def _call(name: str, args: dict[str, Any]) -> Any:
    calls: dict[str, Callable[[], Any]] = {
        "research_status": lambda: RESEARCH.status(
            MT5Environment(**args["environment"]) if args.get("environment") else None
        ),
        "setup_portable_mt5": lambda: RESEARCH.setup_portable_mt5(args.get("destination")),
        "run_discovery": lambda: RESEARCH.run_discovery(args.get("overrides", {})),
        "list_hypothesis_families": lambda: {
            "engine": "xauusd_bar_hypothesis_discovery",
            "families": list(HYPOTHESIS_BUILDERS.keys()),
            "execution": "closed-bar signal generation with next-bar bid/ask bar replay",
            "parallelism": "run_hypothesis_discovery accepts parallel_workers from 1 to 32",
            "export": "use export_hypothesis_ea to write a standalone MQL5 EA plus .set file from a HypothesisSpec",
            "tick_note": "tick data is supported by run_local_replay for later confirmation, not by this discovery loop",
        },
        "run_hypothesis_discovery": lambda: HYPOTHESIS.run_discovery(
            HypothesisDiscoveryRequest(**args["request"])
        ),
        "list_market_datasets": lambda: HYPOTHESIS.list_market_datasets(),
        "inspect_market_dataset": lambda: HYPOTHESIS.inspect_market_dataset(args["dataset_id"]),
        "sample_market_bars": lambda: HYPOTHESIS.sample_market_bars(
            args["dataset_id"],
            args.get("symbol", "XAUUSD"),
            args.get("timeframe", "m5"),
            args["date_from"],
            args["date_to"],
            limit=int(args.get("limit", 200)),
        ),
        "analyze_market_mind": lambda: HYPOTHESIS.analyze_market_dataset(
            args["dataset_id"],
            args.get("symbol", "XAUUSD"),
            args.get("timeframe", "m5"),
            args["date_from"],
            args["date_to"],
            bias_pct=float(args.get("bias_pct", 0.70)),
        ),
        "list_external_data": lambda: EXTERNAL_DATA.list_data(),
        "import_external_data": lambda: EXTERNAL_DATA.import_data(
            ExternalDataImportRequest(**args["request"])
        ),
        "external_context": lambda: EXTERNAL_DATA.context(
            args.get("symbol", "XAUUSD"),
            args["as_of"],
        ),
        "export_hypothesis_ea": lambda: hypothesis_to_mql.export(
            HypothesisSpec(**args["strategy"]),
            output_name=args.get("output_name"),
            risk_fraction=float(args.get("risk_fraction", 0.01)),
            daily_loss_pct=float(args.get("daily_loss_pct", 4.0)),
            max_loss_pct=float(args.get("max_loss_pct", 8.0)),
            max_trades_per_day=int(args.get("max_trades_per_day", 4)),
            max_spread_points=float(args.get("max_spread_points", 80.0)),
        ),
        "import_market_data": lambda: RESEARCH.import_market_data(
            MarketDataImportRequest(**args["request"])
        ),
        "list_candidates": lambda: RESEARCH.list_candidates(args.get("root"))[: int(args.get("limit", 50))],
        "import_strategy": lambda: RESEARCH.import_strategy(args["set_path"]),
        "create_strategy_variant": lambda: RESEARCH.create_variant(
            args["set_path"], args["parameter_overrides"], args["hypothesis"]
        ),
        "run_mt5_pipeline": lambda: RESEARCH.run_pipeline(
            args["set_path"],
            BacktestSpec(**args["backtest"]),
            MT5Environment(**args["environment"]) if args.get("environment") else None,
            PromotionPolicy(**args.get("policy", {})),
        ),
        "run_local_replay": lambda: RESEARCH.run_local_replay(
            ReplayRequest(**args["request"])
        ),
        "run_local_robustness": lambda: RESEARCH.run_local_robustness(
            RobustnessRequest(**args["request"])
        ),
        "compare_local_mt5_monte_carlo": lambda: RESEARCH.compare_monte_carlo(
            MCCompareRequest(**args["request"])
        ),
        "parse_mt5_report": lambda: RESEARCH.parse_report(
            args["report_path"], PromotionPolicy(**args.get("policy", {}))
        ),
        "list_experiments": lambda: RESEARCH.store.list(int(args.get("limit", 50))),
        "get_experiment": lambda: RESEARCH.store.get(args["experiment_id"]),
    }
    if name not in calls:
        raise ValueError(f"unknown tool: {name}")
    return calls[name]()


def _respond(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "better-discovery-research", "version": "0.1.0"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params", {})
        try:
            result = _call(params.get("name", ""), params.get("arguments", {}))
            content = json.dumps(result, indent=2, default=str)
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": content}], "isError": False,
            }}
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            }}
    return {"jsonrpc": "2.0", "id": request_id, "error": {
        "code": -32601, "message": f"method not found: {method}",
    }}


def main() -> None:
    for raw in sys.stdin:
        try:
            response = _respond(json.loads(raw))
            if response is not None:
                sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            sys.stderr.write(f"invalid MCP message: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
