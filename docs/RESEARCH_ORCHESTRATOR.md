# Research Orchestrator

The research orchestrator connects provider market data, local bid/ask replay,
BETTER DISCOVERY, MetaEditor, the MT5 Strategy Tester, an experiment database,
and Codex through optional MCP. It is deliberately
research-only: it has no order-placement or live-deployment operation.

## Workflow

1. `import_market_data` creates an immutable provider dataset containing
   partitioned bid/ask ticks, enriched bars, integrity metadata, and validated
   compatibility CSVs for the existing discovery algorithm.
2. `run_discovery` runs the existing discovery engine and records only the
   candidates produced by that run.
3. `import_strategy` converts a `.set` into a canonical JSON strategy spec and
   SHA-256 fingerprint.
4. `run_local_replay` applies that exact strategy to provider bid/ask ticks,
   models spread, commission and slippage, and exports CSV/Parquet closed-trade
   ledgers for Monte Carlo.
5. Local validation proceeds through permutation, walk-forward, perturbation,
   cross-provider, and untouched lockbox gates before EA generation.
6. `run_mt5_pipeline` generates the EA, installs it under
   `MQL5/Experts/BetterDiscoveryResearch`, compiles it, runs one tester window,
   parses the immutable HTML report, and applies the fixed promotion gate.
7. Report diagnostics include aggregate metrics plus regime, direction, month,
   and entry-hour segments.
8. `compare_local_mt5_monte_carlo` runs identical settings and random seed on
   the local ledger and native MT5 HTML trades. Material parity discrepancies
   block demo promotion.
9. `create_strategy_variant` requires a written hypothesis, changes only
   parameters already present in the parent `.set`, and records lineage.
10. Every request, result, artifact path, error, dataset role, and strategy
   fingerprint is written to `userdata/research/experiments.sqlite3`.

Backtests must declare one of these dataset roles:

- `validation`: initial independent MT5 screening.
- `walk_forward`: chronological robustness windows.
- `lockbox`: final untouched evidence. One completed use freezes that exact
  strategy fingerprint and prevents further variants from it.

## Codex MCP Setup

MCP is optional. The desktop UI calls the same Python services directly. MCP
is retained as a thin structured control layer for autonomous Codex campaigns;
it contains no separate trading or backtest implementation.

The repository contains `.codex/config.toml`, which starts the server through
`scripts/start-research-mcp.ps1`. Open a new Codex thread after trusting this
repository, then confirm that the `better_discovery` MCP tools are available.

Start the dedicated research thread with the prompt in
`docs/AUTONOMOUS_RESEARCH_PROMPT.md`. The thread performs reasoning and repeated
tool calls; the MCP service performs deterministic actions and stores state, so
a later thread can continue from `list_experiments`.

The MCP server can also be tested directly:

```powershell
'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' |
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-research-mcp.ps1
```

## HTTP API

The same service is exposed by the FastAPI sidecar:

- `GET /research/status`
- `POST /research/mt5/setup-portable`
- `POST /research/discovery`
- `POST /research/local-replay`
- `GET /research/candidates`
- `POST /research/strategies/import`
- `POST /research/strategies/variant`
- `POST /research/reports/parse`
- `POST /research/pipeline`
- `POST /mc/compare`
- `GET /research/experiments`
- `GET /research/experiments/{id}`

Interactive API schemas are available at `http://127.0.0.1:8765/docs` while the
backend is running.

## MT5 Safety

Run `setup_portable_mt5` once before the first autonomous campaign. It creates a
writable research-only terminal under
`%LOCALAPPDATA%/BetterDiscoveryResearch/mt5_portable`, allowing MT5 itself to
save native tester reports. Broker history is reused through a directory
junction, so the multi-gigabyte history database is not duplicated. The worker
then selects this terminal automatically. It refuses to attach to or shut down
the interactive terminal. MT5 writes each `.htm`; the orchestrator only copies
the completed file byte-for-byte into `userdata/research/reports` for immutable
experiment storage and parsing.

Default executable paths are:

```text
C:\Program Files\MetaTrader 5\terminal64.exe
C:\Program Files\MetaTrader 5\MetaEditor64.exe
```

They can be overridden per pipeline request with `environment.terminal_path`,
`environment.metaeditor_path`, and `environment.data_path`.

Passing a promotion gate is not proof of future profitability. A candidate is
eligible for demo forward testing only after validation, walk-forward tests,
parameter perturbation, realistic cost stress, and one untouched lockbox pass.

Planned strategy-development improvements, including in-sample and walk-forward
permutation testing, are tracked in
[`STRATEGY_DEVELOPMENT_BACKLOG.md`](STRATEGY_DEVELOPMENT_BACKLOG.md).
That backlog also requires Monte Carlo-compatible exports from the future local
tick-replay backtester and a like-for-like comparison against Monte Carlo run on
the native MT5 report trade ledger.
