# AI-in-the-loop review (optional)

Discovery can send each finished run's ranked patterns + config snapshot to an
LLM that critiques them like a skeptical quant reviewer, writing
`ai_review_seed{seed}.md` next to the run report. It flags overfit risk
(train→OOS decay, tiny samples, razor-thin rule bounds), tells you which
patterns to verify in MT5 first and what to check in the per-trade diff, and
suggests concrete config-knob changes.

**It is strictly advisory and strictly optional.**

- OFF by default. With it off, a run is byte-identical to the engine without it.
- It never gates, drops, or re-ranks a pattern. The deterministic pipeline
  (gate → rank → export → MC) is unaffected by anything the model says.
- Any failure (no server, bad key, timeout) prints one console line and the run
  continues normally.
- Zero extra dependencies — stdlib HTTP only, works in the embedded runtime.

## Enabling

Pick ONE backend:

### Local LLM (free, private — Ollama)

```powershell
# one-time: install Ollama (https://ollama.com), then pull a model
ollama pull llama3.1            # or qwen2.5:14b, deepseek-r1:14b, etc.

$env:BD_AI_REVIEW = "1"
$env:BD_AI_MODEL  = "llama3.1"  # the model you pulled
# BD_AI_BASE_URL defaults to http://localhost:11434/v1 when no API key is set
```

LM Studio / llama.cpp / vLLM also work — set `BD_AI_BASE_URL` to their
OpenAI-compatible endpoint (e.g. `http://localhost:1234/v1`).

### DeepSeek cloud

```powershell
$env:BD_AI_REVIEW     = "1"
$env:DEEPSEEK_API_KEY = "sk-..."   # base URL + model default to
                                   # api.deepseek.com / deepseek-chat
```

Then run discovery as usual. Alternatively set `AI_REVIEW_ENABLED = True` (and
optionally `AI_REVIEW_BASE_URL` / `AI_REVIEW_MODEL`) in
`backend/toolkit/pattern_discovery_v6.py` — these are app-overridable globals.
The API key is only ever read from the environment, never from config files.

## Reviewing an already-finished run

Every run now writes a machine-readable `results_seed{seed}.json`. To review
one after the fact:

```powershell
python backend\toolkit\ai_review.py "userdata\discovery\<seed folder>"
```

## Reading the output

The reviewer sees three metric families per pattern and is told their exact
semantics:

| field | meaning |
|---|---|
| `train_*` | in-sample, box-only (what the EA's entry box fires on) |
| `ea_oos_*` | out-of-sample, box-only — **the only numbers an MT5 backtest can reproduce** |
| `gated_oos_*` | out-of-sample but cluster-gated — diagnostic, MT5 cannot match it |

Treat its verdicts as a second opinion, not a gate: the ground truth remains
the MT5 Strategy Tester per-trade diff (`docs/DISCOVERY_FIDELITY_AUDIT.md`,
Part 5).
