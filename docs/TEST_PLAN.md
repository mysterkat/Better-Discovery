# Test plan — v2.3.x (fidelity fixes, AI review, ONNX stage 1)

Work through the sections in order — each one depends on the previous.
Every check has an explicit PASS condition; anything that fails it is worth
reporting (copy the exact console line / screenshot).

---

## 0. Update the app

- [ ] Open the installed app → it should offer the auto-update (or Help/About
      shows the new version after restart).
- **PASS:** app version shows **2.3.1** (2.3.0 only ships the fixes; 2.3.1
  adds the ONNX training data — wait for it if the updater still shows 2.3.0).
- [ ] After updating, fully quit and reopen once (backend restarts with the
      new code).

## 1. App sanity (2 min)

- [ ] Discovery tab → parameters accordion → a new group **"AI Review"**
      exists, with an `AI Review` toggle (off by default) and Base URL /
      Model / Timeout under "Show advanced".
- [ ] Set → MQL tab: there is a **.set file picker** and, after a convert, an
      input-completeness report.
- **PASS:** both visible. **FAIL:** you're still on the old version (check §0).

## 2. Backend test suite (optional, 1 min)

From the repo folder:

```powershell
& "src-tauri\binaries\python\python.exe" -m pytest backend/tests -q
```

- **PASS:** `42 passed` (1 may skip). Any FAILED = report the test name.

## 3. Fresh discovery run

Settings to confirm before running (Discovery tab):

- [ ] `MT5_SERVER_UTC_OFFSET` matches your broker (2 winter / 3 summer for
      most EU brokers).
- [ ] `SPREAD_PTS` ≈ your symbol's typical spread; `COMMISSION_R` /
      `SWAP_R_PER_BAR` set if your account charges them (else 0).
- [ ] Leave `GENE_SCORE_BOX_ONLY` / `GATE_BOX_ONLY` ON (defaults).

Run discovery, then check the output folder (`userdata\discovery\seed_*`):

- [ ] Console/report shows an **`EA-OOS`** line per pattern
      (`box-only (EA): WR=… PF=… n=… | inflation ×…`).
- [ ] `results_seed<seed>.json` exists (machine-readable summary).
- [ ] Per-pattern `cluster_*_seed*.csv` contains **`feat_…` columns** (27 of
      them) and rows with `split=test_ea` — open one in Excel to confirm.
      *(This is what makes the run ONNX-ready — needs 2.3.1.)*
- [ ] `.set` headers: the `; Test:` line says **"EA-faithful box-only OOS —
      compare THIS to your MT5 backtest"**; a separate `; TestGated:` line
      carries the old diagnostic numbers.

**What to expect — IMPORTANT:** honest numbers are LOWER than old runs.
Box-only OOS around WR 50–53% / PF 1.1–1.3 is normal and real. PF > 1.5 with
hundreds of OOS trades is suspicious — check `box_inflation` and trade count
before celebrating. **0 passers is a valid result**, not a bug: it means no
rule cleared the floors at your costs.

- **PASS:** run completes; artifacts above exist; ranking in the report
  follows `ea_test_pf` (EA-faithful), not train score.

## 4. Convert + compile one pattern

- [ ] Set → MQL tab → load the best pattern's `.set` → Convert.
- [ ] The report shows **0 missing inputs**.
- [ ] Copy the generated `.mq5` into your terminal's `MQL5\Experts\` and
      compile (F7).
- **PASS:** `0 errors, 0 warnings`. Any `undeclared identifier` = report it
  (that bug class is supposed to be dead).

## 5. MT5 per-trade verification — the test that matters most

Goal: discovery's `EA-OOS` numbers ≈ Strategy Tester results, trade by trade.

1. [ ] First confirm tester determinism: run the same EA + `.set` twice, same
       range — the two trade lists must be identical
       (`docs/reproducibility_check.md`).
2. [ ] Run the EA in Strategy Tester, **"Every tick based on real ticks"**,
       same symbol/timeframe, dates = the TEST window printed in the report
       header (`Test: <date> -> <date>`).
3. [ ] Keep the EA inputs exactly as the `.set` loads them — especially
       `MaxHoldBars=32`, `CooldownBars=4`, and **`Commission_R=0` /
       `Swap_R_PerBar=0`** (the broker already books real costs; non-zero
       double-charges).
4. [ ] Compare:
       - tester **trade count** vs the `.set` header's `Trades=` (EA-faithful)
         → should be in the same ballpark (±20% from spread spikes/news gaps
         is acceptable; 2–3× apart is a real divergence — report it),
       - tester **WR / PF** vs the header's `Test:` WR/PF → same direction,
         similar magnitude,
       - spot-check 10 trades: entry times exist in the discovery CSV's
         `test_ea` rows (`entry_time` column), exits at SL/TP/32-bar timeout.
- **PASS:** counts and WR/PF in the same ballpark, entry times line up.
  **FAIL worth reporting:** systematic one-sided drift (tester always trades
  more, or WR 10+ points lower) — copy both trade lists.

## 6. AI review (optional)

- [ ] Easiest local path: install Ollama, `ollama pull llama3.1`, then enable
      the `AI Review` toggle (or `setx BD_AI_REVIEW 1`) and run a discovery.
- **PASS:** `ai_review_seed<seed>.md` appears next to the report with
  verdicts + MT5 verification order. With no LLM reachable the run still
  completes and prints a single `[ai_review] unreachable… run continues`
  line — that is also a PASS (it must never break the run).

## 7. ONNX filter — stage 1 (after §3 succeeded)

```powershell
& "src-tauri\binaries\python\python.exe" backend\toolkit\onnx_filter.py "userdata\discovery\<seed folder>"
```

- [ ] `onnx_filter_report.md` appears in the folder; per pattern it prints
      `train` and `EA-OOS` lines: `baseline n/WR/PF -> filtered n/WR/PF`.
- **What to look for:** verdict **UPLIFT** = on the untouched EA-faithful OOS
  trades, filtering improved PF ≥ 5% without lowering WR and kept ≥ 30
  trades. **NO-UPLIFT is a fine outcome** — it means don't build that filter
  into the EA, nothing is lost.
- [ ] Patterns with < 120 train trades are skipped with a clear message
      (expected, not a bug).
- **Report back:** any UPLIFT verdicts → that's the trigger for stage 2
  (model inside the EA).

## Known behaviours that are NOT bugs

- Lower WR/PF than pre-2.3 runs — the old numbers were partly cluster-gate
  artifacts MT5 could never reproduce.
- `⚠ MARGINAL` patterns get `.set` files but skip the MC chain — don't trade
  them.
- Live/tester can skip entries the sim took when spread spikes past
  `MaxSpreadPoints=30` — protective by design, shows up at news times.
- `box_inflation ×1.0–1.5` is normal; `×inf` means the gated test count was
  0 for a selective rule (cosmetic).
