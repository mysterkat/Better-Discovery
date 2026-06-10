"""Optional AI-in-the-loop reviewer for Pattern Discovery results.

STRICTLY ADVISORY and STRICTLY OPTIONAL:
  - The discovery pipeline is deterministic and complete without this module.
    Nothing here ever gates, drops, re-ranks, or mutates a pattern.
  - Zero new dependencies — stdlib urllib only, so the embedded runtime
    (src-tauri/binaries/python) needs nothing installed.
  - Any failure (no server, bad key, timeout, malformed reply) degrades to a
    one-line console note; the run's artifacts are unaffected.

Works with ANY OpenAI-compatible chat-completions endpoint:
  - DeepSeek cloud:  set DEEPSEEK_API_KEY (or BD_AI_API_KEY) →
                     defaults to https://api.deepseek.com/v1, model deepseek-chat
  - Local Ollama:    no key → defaults to http://localhost:11434/v1
                     (set BD_AI_MODEL to a model you have pulled, e.g. llama3.1)
  - LM Studio / llama.cpp / vLLM: set BD_AI_BASE_URL + BD_AI_MODEL.

Enable per run with env var BD_AI_REVIEW=1 (or the AI_REVIEW_ENABLED toggle in
pattern_discovery_v6.py, which the app's override mechanism can set).

Standalone usage on a finished run folder (reads results_seed*.json):

    python ai_review.py path/to/discovery/seed_folder
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_TIMEOUT_S = 120.0
MAX_PATTERNS_SENT = 12   # keep the prompt compact; top-ranked patterns only

_SYSTEM_PROMPT = """\
You are a skeptical senior quantitative researcher reviewing machine-discovered
intraday trading patterns before they are verified in a MetaTrader 5 backtest.

You will receive JSON with:
  - run_config: the discovery engine's key knobs (targets, filters, costs)
  - patterns: per-pattern metrics. IMPORTANT semantics:
      * train_*  = in-sample, box-only (what the EA's entry box fires on)
      * ea_oos_* = OUT-OF-SAMPLE, box-only — the only numbers an MT5 backtest
        of the exported .set can reproduce
      * gated_oos_* = out-of-sample but cluster-gated (diagnostic only; MT5
        cannot reproduce these)
      * box_inflation = ea_oos_trades / gated_oos_trades
      * marginal = passed the gate only via soft-filter tolerance

Your job, in priority order:
1. For each pattern give a verdict: PRIORITIZE / VERIFY-WITH-CAUTION / DISTRUST,
   with one or two concrete reasons (train→OOS decay, small N, PF/WR
   inconsistency, implausible RR, contradictory or razor-thin rule bounds,
   session/regime concentration, marginal soft-fails).
2. Rank the 3 best candidates to verify in MT5 first, and say what to check in
   the per-trade diff (e.g. trade count vs ea_oos_trades, timeout exits).
3. Flag any systemic issue you infer across patterns (e.g. all rules collapse
   onto one feature; OOS uniformly far below train → selection bias).
4. Suggest at most 3 concrete config-knob changes (use exact knob names from
   run_config) with one-line justifications. Be conservative; do not suggest
   loosening quality floors to manufacture passers.

Be terse and specific. No praise, no hedging boilerplate. Output GitHub
markdown with sections: ## Verdicts (a table), ## MT5 verification order,
## Systemic observations, ## Suggested knob changes. If the patterns list is
empty, review run_config instead and suggest why the run found nothing.
"""


# ── config resolution ─────────────────────────────────────────────────────────

def _resolve_config(base_url: str | None = None,
                    model: str | None = None,
                    api_key: str | None = None,
                    timeout_s: float | None = None) -> dict:
    key = (api_key
           or os.environ.get("BD_AI_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY")
           or os.environ.get("OPENAI_API_KEY")
           or "")
    url = (base_url or os.environ.get("BD_AI_BASE_URL") or "").rstrip("/")
    if not url:
        # Key present → assume DeepSeek cloud; otherwise local Ollama.
        url = "https://api.deepseek.com/v1" if key else "http://localhost:11434/v1"
    mdl = model or os.environ.get("BD_AI_MODEL") or ""
    if not mdl:
        mdl = "deepseek-chat" if "deepseek" in url else "llama3.1"
    try:
        t = float(timeout_s if timeout_s is not None
                  else os.environ.get("BD_AI_TIMEOUT", DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        t = DEFAULT_TIMEOUT_S
    return {"base_url": url, "model": mdl, "api_key": key, "timeout_s": t}


def is_enabled() -> bool:
    return os.environ.get("BD_AI_REVIEW", "").strip().lower() in ("1", "true", "yes", "on")


# ── transport ─────────────────────────────────────────────────────────────────

def _chat(cfg: dict, system: str, user: str) -> str:
    """One chat-completions call. Raises on failure; caller handles."""
    payload = json.dumps({
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg["timeout_s"]) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
    content = body["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty completion")
    return content.strip()


# ── payload construction ──────────────────────────────────────────────────────

def _compact_pattern(r: dict) -> dict:
    """Reduce one results_seed JSON entry to the fields the reviewer needs."""
    rule = r.get("genetic_rule") or {}
    return {
        "id": r.get("pattern_id") or f"C{r.get('cluster','?')}_{r.get('direction','?')}",
        "direction": r.get("direction"),
        "bidir_mode": r.get("bidir_mode"),
        "marginal": bool(r.get("marginal")),
        "soft_fail": (r.get("soft_fail") or {}).get("name"),
        "train_wr": r.get("win_rate_"), "train_pf": r.get("profit_factor"),
        "train_trades": r.get("total_trades"), "train_per_day": r.get("per_day"),
        "max_dd_r": r.get("max_drawdown_r"),
        "ea_oos_wr": r.get("ea_test_wr"), "ea_oos_pf": r.get("ea_test_pf"),
        "ea_oos_trades": r.get("ea_test_trades"),
        "gated_oos_wr": r.get("test_wr"), "gated_oos_pf": r.get("test_pf"),
        "gated_oos_trades": r.get("test_trades"),
        "box_inflation": r.get("box_inflation"),
        "sl_pct": r.get("sl_pct"), "tp_pct": r.get("tp_pct"),
        "implied_rr": r.get("implied_rr"),
        "consistency": r.get("consistency"),
        "degrading": r.get("degrading"),
        "rule": {c: [round(float(lo), 4), round(float(hi), 4)]
                 for c, (lo, hi) in rule.items()},
        "seed": r.get("seed"),
    }


def build_user_payload(results: list[dict], run_config: dict) -> str:
    pats = [_compact_pattern(r) for r in results[:MAX_PATTERNS_SENT]]
    omitted = max(0, len(results) - len(pats))
    doc = {
        "run_config": run_config,
        "patterns": pats,
        "patterns_omitted_lower_ranked": omitted,
    }
    return json.dumps(doc, indent=1, default=str)


# ── public API ────────────────────────────────────────────────────────────────

def review_run(results: list[dict], run_config: dict, out_dir, seed,
               base_url: str | None = None, model: str | None = None,
               api_key: str | None = None,
               timeout_s: float | None = None) -> str | None:
    """Review a finished run; write ai_review_seed{seed}.md. Returns the path,
    or None on any failure (after printing a one-line note)."""
    cfg = _resolve_config(base_url, model, api_key, timeout_s)
    try:
        user = build_user_payload(results, run_config)
        text = _chat(cfg, _SYSTEM_PROMPT, user)
    except urllib.error.URLError as e:
        print(f"  [ai_review] unreachable ({cfg['base_url']}): {getattr(e, 'reason', e)} "
              f"— run continues without AI review")
        return None
    except Exception as e:
        print(f"  [ai_review] failed ({type(e).__name__}: {e}) — run continues without AI review")
        return None

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ai_review_seed{seed}.md"
    header = (
        f"# AI review — seed {seed}\n\n"
        f"> Advisory only. Generated by `{cfg['model']}` via `{cfg['base_url']}`.\n"
        f"> The deterministic gate/rank pipeline is unaffected by this content.\n\n"
    )
    out_path.write_text(header + text + "\n", encoding="utf-8")
    return str(out_path)


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python ai_review.py <discovery_seed_output_dir>")
        return 2
    run_dir = Path(argv[1])
    candidates = sorted(run_dir.glob("results_seed*.json"))
    if not candidates:
        print(f"no results_seed*.json found in {run_dir} "
              f"(re-run discovery with the current engine to produce one)")
        return 1
    rc = 0
    for f in candidates:
        doc = json.loads(f.read_text(encoding="utf-8"))
        seed = doc.get("seed", f.stem.replace("results_seed", ""))
        path = review_run(doc.get("patterns", []), doc.get("run_config", {}),
                          run_dir, seed)
        if path:
            print(f"AI review -> {path}")
        else:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
