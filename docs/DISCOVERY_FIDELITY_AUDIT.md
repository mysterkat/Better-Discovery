# Discovery ↔ MT5 Fidelity Audit

**Re-verified against `v2.1.3` (commit `2ae3390`) on 2026-05-30.**

> **UPDATE 2026-06-10 (working tree):** a follow-up audit found and fixed a
> second family of divergences — the GA *fitness* simulated a different trade
> process than the gate sim / EA. All fixed and locked by tests
> (`backend/tests/test_sim_ga_parity.py` asserts the GA books the IDENTICAL
> trade stream as `_bt_worker_dir`):
>
> 1. **GA horizon** — fitness scanned `FORWARD_BARS=24` bars while the gate/EA
>    hold `MAX_HOLD_BARS=32`; timeout trades were dropped from fitness entirely.
>    Now: GA scans `MAX_HOLD_BARS` and books timeouts at the hold-window close.
> 2. **GA tie-break** — same-bar SL+TP was scored as *timeout* (dropped);
>    sim/EA book the pessimistic stop. Now: stop wins ties everywhere.
> 3. **GA overlap/cooldown** — fitness used a signal-anchored cooldown with no
>    open-position gate. Now: serialized with the EA's exit-anchored arithmetic.
> 4. **GA exit spread** — SL/TP trigger levels ignored exit-side spread. Now
>    spread-adjusted like `tp_v_eff`/`sl_v_eff` in the sim.
> 5. **GA PF** — was `wins*avg_rr/losses` (every loss priced −1R). Now true-R
>    sums over booked outcomes, matching `_calc_metrics`.
> 6. **Cooldown anchor (B refinement)** — the sim now reproduces the EA's bar
>    arithmetic exactly: anchor = exit bar for intrabar closes, exit bar **+1**
>    for MaxHold timeouts; comparison happens on the forming (entry) bar.
> 7. **Cost-classification bug** — with `COMMISSION_R`/`SWAP_R_PER_BAR` > 0 a
>    net-negative "WIN" inflated gross profit in PF. WIN/LOSS is now decided on
>    NET booked R in both sim and GA.
> 8. **Ranking/selection coherence (D/C follow-through)** — final ranking,
>    multi-seed combined ranking, and MC top-N selection all keyed on
>    cluster-gated `test_pf` (or TRAIN composite for MC) which MT5 cannot
>    reproduce. All three now rank via `_oos_rank_key` → EA-faithful box-only
>    OOS (`ea_test_pf`, `ea_test_wr`) first.
> 9. **.set header honesty** — the `; Test:` line now carries the EA-faithful
>    box-only OOS numbers (what an MT5 backtest can match); cluster-gated
>    figures moved to a `; TestGated:` diagnostic line.
> 10. **MC chain** — now simulates the EA-faithful OOS trade stream (new
>     `split=test_ea` rows in per-pattern CSVs) instead of cluster-gated test
>     trades, falling back to `test` for old CSVs.
>
> Also new: `results_seed{seed}.json` (machine-readable run summary) and an
> optional, strictly advisory AI reviewer (`backend/toolkit/ai_review.py`,
> OFF by default — see `docs/AI_REVIEW.md`).
>
> Verified 2026-06-10: 35 backend tests pass on the embedded runtime;
> template EA and a converter-generated EA both compile 0 errors / 0 warnings
> via MetaEditor. Part-5 per-trade MT5 diff still pending on a fresh export.

This restores the original audit deliverable, which was lost (never committed to the
worktree). It is **not** a verbatim copy — it is a fresh re-verification of all seven
root causes against the *current* code. That matters: the original audit was run
against a stale local checkout (~v2.1.0, then 4 commits behind `origin/main`). Between
that audit and now, **v2.1.2 ("correct PF units + tighten sim<->EA parity") and v2.1.3
("FTMO-oriented defaults") already fixed several of the findings.** Blindly applying the
original Part-4 action plan would re-implement merged fixes and risk regressions.

Files audited:
- `backend/toolkit/pattern_discovery_v6.py` (discovery engine + `.set` exporter)
- `backend/ea/PatternDiscoveryEA.mq5` (live/backtest EA)

---

## Part 1 — Corrected status of the 7 root causes

| # | Root cause (as originally filed) | Status @ v2.1.3 | Impact if open |
|---|---|---|---|
| A | EA has no time-exit; Python force-closes at `MAX_HOLD_BARS=32` | ✅ **FIXED** | — |
| B | Overlapping trades + cooldown value/anchor mismatch | ⚠️ **PARTIAL** — value synced, structure still divergent | High |
| C | Shape-cluster gate in discovery but not the EA → EA fires on a superset | ❌ **OPEN** (structural) | High |
| D | In-sample selection bias — ranking/headline/export all use TRAIN composite | ❌ **OPEN** (methodology) | High |
| E | Exit levels fit in-sample (SL/TP from cluster MAE/MFE quantiles) | ❌ **OPEN** (methodology) | Med–High |
| F | Optimistic TP-first tie-break + losses hard-coded −1R (analytic PF) | ✅ **FIXED** (now pessimistic + true-R) | — |
| G | No commission/swap in Python costs | ⚠️ **PARTIAL** — spread modeled, commission/swap absent | Medium |

Net: **2 fixed (A, F), 2 partial (B, G), 3 open (C, D, E).**

---

## Part 2 — Evidence per cause

### A — EA time-exit ✅ FIXED
- EA enforces a bar-count exit: `barsHeld >= MaxHoldBars` → force-close
  (`PatternDiscoveryEA.mq5:1467-1480`).
- The `.set` exporter emits `MaxHoldBars={int(MAX_HOLD_BARS)}` → `MaxHoldBars=32`
  (`pattern_discovery_v6.py:2964`), from the *same* constant the sim uses (`:189`).
  Exporter comment `:2948-2952` explicitly ties the two together.
- ⚠️ Caveat: the EA input default is `MaxHoldBars=0` (= disabled; `:120`). The fix is
  only active when the EA is run with a **freshly exported `.set`**. An old `.set`
  (pre-2.1.2) or a hand-run with defaults will silently disable the time-exit.

### B — Overlap + cooldown ⚠️ PARTIAL
- **Value: synced.** Exporter emits `CooldownBars={int(COOLDOWN_BARS)}` → `CooldownBars=4`
  (`:2957`); sim uses `COOLDOWN_BARS=4` (`:191`). The original claim that the exporter
  "hard-codes CooldownBars=3" is **stale** — it now derives from the constant.
- **Structure: still divergent.** Two independent mismatches remain:
  1. **Cooldown anchor.** Python gates on the *signal bar*: `if bi - last_sig < cooldown:
     continue` (`:1512`), with `last_sig` set to the entry bar `bi` (`:1567/1572`).
     The EA anchors on *position close*: `g_cooldownBar` is set in the close paths
     (`:692`, `:1456`, `:1477`). So Python's spacing is `cooldown` bars from entry;
     the EA's is `hold_duration + CooldownBars` from entry.
  2. **Overlap.** The Python sim has **no open-position gate** — it only spaces signals,
     so with `COOLDOWN_BARS=4` and `MAX_HOLD_BARS=32` a new trade can open while a prior
     one is still "held" → overlapping/concurrent trades. The EA serializes:
     `if(HasOpenPosition()) return;` (`:482`).
- Net effect: these two push the EA toward **fewer, differently-spaced** trades than the
  sim (opposite direction to Cause C, which inflates EA trades — see below).

### C — Shape-cluster gate missing in EA ❌ OPEN (structural)
- Discovery selects trades by cluster membership + shape matching
  (`USE_SHAPE_MATCHING=True`, `SHAPE_MATCH_THRESHOLD=0.75`, `:179-180`; clustering at
  `cluster_multi_algo` `:804+`; SL/TP from cluster MAE/MFE `:1017-1018`).
- The EA has **no cluster or shape logic** (grep of `PatternDiscoveryEA.mq5` for
  `cluster`/`shape` returns nothing). It fires on the exported feature **box** (column
  bounds) + discriminator only.
- The GA *does* try to express each cluster as a box rule (pass-2 refinement,
  `:2396-2453`), so the box approximates the cluster — but any looseness means the EA
  fires on a **superset** of the discovery trades. This is the principal trade-count
  inflation. Closing it fully requires either (a) porting the shape/cluster gate into the
  EA, or (b) constraining the GA to emit a box whose recall vs the cluster is bounded,
  and reporting that retention.

### D — In-sample selection bias ❌ OPEN (methodology)
- Ranking sorts by `composite_score` (TRAIN): `:1239` and final `:3571`.
- Test metrics *are* computed (`test_wr`, `test_pf`, `:3558-3559`) but only **displayed**
  (`:1299-1300`, `:2849`, `:3616`, `:3708`) — they never drive ranking, headline, or
  export selection. Best-of-`MULTI_SEED_COUNT` with no multiple-testing correction.

### E — In-sample exit-fit ❌ OPEN (methodology)
- SL/TP come from the cluster's own excursion quantiles:
  `sl_pct = percentile(mae_arr, SL_PCT_QUANTILE*100)`,
  `tp_pct = percentile(mfe_arr, TP_PCT_QUANTILE*100)` (`:1017-1018`).
- Current quantiles: `SL_PCT_QUANTILE=0.70`, `TP_PCT_QUANTILE=0.60` (`:193-194`).
  (Original audit cited 85th MAE — now **70th** after the FTMO tightening; mechanism
  unchanged, specific number stale.) The OOS/test stage re-fits its own stops
  (`:2396-2397`), so reported train and test stops differ.

### F — Tie-break + PF units ✅ FIXED
- Intrabar resolution is now **pessimistic**: when a bar spans both SL and TP, the stop is
  assumed to fill first (`:1534-1540`; for longs, `lo_ <= sl_v_eff` is checked before
  `h_ >= tp_v_eff`). The original "optimistic TP-first" is **stale**.
- PF is now money-true: every outcome booked as an R-multiple — clean TP `= reward/risk`,
  clean SL `= -1.0`, timeout `= realised move / risk` (`:1523-1530`, `:1551-1553`). Losses
  are no longer flat −1R, so PF/drawdown are in true R units (`:1586-1590`).

### G — Commission/swap ⚠️ PARTIAL
- Spread **is** modeled: `SPREAD_PTS=0.30` (`:187`), applied to entry and exit
  (`:1515-1520`). Per-trade **commission and overnight swap are still absent** — no such
  constants exist in the cost path. For FTMO-style accounts with round-turn commission,
  this understates costs on high-frequency rules.

---

## Part 3 — Corrections to prior notes

- **Original audit run against a stale checkout.** Findings A, F (and B's value) were
  reported "confirmed broken" because the local copy predated the `origin/main` v2.1.3
  fixes. They are fixed in this worktree (now at `2ae3390`).
- **`docs/VALIDATION.md` does not exist** in this worktree. The memory's instruction to
  "retract its shape-gate-resolved claim" is moot here — there is no such file to correct.
  If it exists on another branch, that retraction still stands (the EA has no shape gate).
- **`docs/reproducibility_check.md`** is unrelated to fidelity — it checks MT5 *determinism*
  (same `.set` run twice → identical trades). It is complementary to Part 5 below.

---

## Part 4 — Remaining action plan (re-prioritized for v2.1.3)

> **Status (2026-05-30, working tree — UNVERIFIED in MT5):** all four blocks below were
> implemented and Python `py_compile`-clean. **None are MT5-per-trade-verified yet** (Part 5
> pending). B: sim now serializes + anchors cooldown on exit bar. C: report-only
> `signal_retention` metric + `MAX_BOX_INFLATION=None` knob (full box-vs-cluster recall still
> TODO). E+D: rankings key on OOS `(test_pf,test_wr)`, test stage reuses train-fitted stops.
> G: `COMMISSION_R`/`SWAP_R_PER_BAR` (default 0) in sim + EA — ⚠️ the EA already books the
> broker's real swap/commission, so its `Commission_R`/`Swap_R_PerBar` inputs are an
> *additional synthetic* charge: keep them at 0.0 against a real broker; use only for
> sim-parity tests. EA not compiled here (outside MQL5 root) — compile in MT5 before use.

Only the still-open / partial items remain. Suggested order (highest value-per-risk first):

1. **B-structure (parity, tractable).** Decide the canonical model and make both sides
   agree. Cleanest: give the Python sim an open-position gate (no new entry while a trade
   is held) and anchor its cooldown on the *exit* bar, matching the EA. This makes the sim
   match the serial live engine without touching MQL5. Re-run and diff.
2. **C (structural, highest impact).** Quantify the inflation first: measure box-vs-cluster
   recall on a real run (how many box-hits are non-cluster). If large, constrain the GA to
   bound that recall and report retention; full fix = port the gate to the EA.
3. **E then D (methodology).** Report and rank on **test** (OOS) metrics, not train
   composite; fit exit levels on train and hold them fixed through test (no per-stage
   re-fit). These change headline numbers, so land them with eyes open.
4. **G (partial).** Add a `COMMISSION_PER_LOT` / `SWAP` term to the Python cost path and
   the EA, exported via `.set`.

After **each** block, run the Part 5 verification before moving on.

---

## Part 5 — Verification protocol (must do after each fix)

Summary WR/PF can match by luck while the trade lists differ. Always diff **per-trade**:

1. Re-export a fresh `.set` from the current code (critical — defaults like
   `MaxHoldBars=0`/`CooldownBars=3` only get overridden by a current export).
2. Run the EA in MT5 Strategy Tester ("Every tick based on real ticks"), same symbol /
   timeframe / date range as the discovery run.
3. Export the discovery per-trade list and the MT5 "Trades" CSV.
4. Diff on `(entry_time, direction)` first (set membership), then on
   `(exit_reason, bars_held, R)` for the matched intersection.
5. Confirm MT5 itself is deterministic first via `docs/reproducibility_check.md`, so any
   remaining diff is attributable to a real Python↔EA divergence, not tester noise.

Target: trade-set Jaccard → 1.0, then per-trade R agreement within spread tolerance.

---

## Part 6 — Speed hotspots (unchanged from original, not re-profiled)

Carried forward from the original audit; re-profile before acting:
1. `@njit` the first-touch SL/TP kernel (`:1531-1543`) — est. 10–50×.
2. Precompute GA TP/SL hit-bars once per cluster — est. 3–6×.
3. Closed-form slope vs `np.polyfit` on the trend feature — est. 50–100× on that op.

Fixes **B** and **C** also reduce metric-path work, so they speed up discovery as a side effect.
