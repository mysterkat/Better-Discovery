# BETTER DISCOVERY — Roadmap

> Living document. Updated 2026-05-20 (v0.7.1 shipped; Strategy Library + Compare on `claude/distracted-pascal-e99b0b`, not yet tagged).
> Items grouped by target version. Effort is rough; ranking inside each version is by priority.

---

## Shipped (unreleased — on `claude/distracted-pascal-e99b0b`)

A different-scope v0.8.0 candidate that landed before the originally-planned "trade-count gap fix" work below. Either renumber the gap-fix work to v0.9.0 and tag this as v0.8.0, or hold this branch and ship the original v0.8.0 first — decision pending.

**Strategy Library + Compare tab**
- New top-level **Strategy Compare** tab (5th in the sidebar).
- `⭐ Save` button on every Discovery result row → copies `.set` + auto-resolved `trades.csv` + `PatternSummary` JSON into `userdata/library/<pattern_id>/`.
- Side-by-side comparison canvas with horizontal scroll.
- **Diff mode** highlighting: indicators unique to one column (amber), shared but with different bounds (orange), best-in-row metric (green).
- MT5 Strategy Tester `.htm` report drop slot per column → sandboxed iframe preview.
- MT5 trades `.csv` drop slot → on-the-fly trade count / gross P/L / max DD summary.
- Backend: new `library.py` router (`save` / `list` / `attach` / `mt5_html` / `delete`), all 5 routes round-trip-tested.
- Extracted shared `IndicatorsTable` component reused by DiscoveryResults + Compare tab.

**Discovery settings cleanup (tier-based)**
- Added `tier: "core" | "advanced"` field to `ParamMeta`, surfaced via `/discovery/params`.
- 36 power-user params demoted behind a per-group `▸ Show advanced (N)` collapse with auto-reveal when edited.
- Audit run against all 79 PARAM_META keys: **zero dead params** — everything was already wired up. Cleanup was purely a presentation change.
- Group rename: **Scoring → "Scoring & Targets"**. Label rename: **"Overlap Threshold" → "Max Trade Overlap"** (no longer in a single-item Ensemble group).
- Description fixes on `SCORE_WILSON_CONFIDENCE` (drives wilson_wr display in both modes, not just legacy) and `SCORE_W_*` (active in both target and legacy modes with different semantics).

---

## v0.8.0 — "Trade what you discover" (the trade-count gap fix)

This release closes the gap between what Pattern Discovery predicts a strategy will do and what the EA actually does in MT5. Three categories of work.

### A. Verify the v0.7.x trade-count fix worked end-to-end

| # | Item | Effort | Notes |
|---|---|---|---|
| 1 | **Re-run the MT5 backtest vs Discovery comparison** that originally showed 500 trades vs ~150 expected. Confirm whether the v0.7.x indicator alignment + harness validation closed the gap, or whether the shape-cluster filter is still the dominant cause (item 4 below) | 1 hr | Use the same `.set` file + same dataset as the original test; record before/after ratio |
| 2 | Document the result in `docs/VALIDATION.md` with the actual trade-count ratio | 30 min | Living evidence for future regressions |

### B. Make Discovery aware of MT5-side reality

| # | Item | Effort | Notes |
|---|---|---|---|
| 3 | **Audit Discovery's use of drift-prone features** (`vol_ratio`, `vol_body_conf`, `vol_price_div`, `poc_dist`). Add tolerance margins to GA-found thresholds (e.g. never pick a `vol_ratio > 1.2` rule — round to 1.25 or 1.15 with a buffer zone) so tiny live-vs-CSV volume drift doesn't flip signals | half day | The drift is real but small; margin-aware GA is the clean fix |
| 4 | **Export shape-cluster centroids into `.set` file** + add `MatchesShape()` helper to EA template. This is the dominant root cause of MT5 firing 3-5× more trades than Discovery predicted: Discovery uses both shape-cluster membership AND range filters; the `.set` only carries range filters. ~80 lines MQL5 + 30 lines Python | 2 days | The single highest-impact fix in v0.8 |
| 5 | **Make Pattern Discovery emit indicator-equivalence metadata** in each `.set`: list which features were used, whether each is `exact` / `approx` (from validation harness), and the recommended threshold margin. EA can refuse to fire on `approx` features below margin | half day | Soft enforcement — UX warning, not a hard block |

### C. Indicator improvements that close known approx caveats

| # | Item | Effort | Notes |
|---|---|---|---|
| 6 | **`BD_Regime` rolling-quantile rewrite** to match Python's `detect_regimes()` instead of the fixed-threshold approximation | half day | Currently 39% match → target 95%+ |
| 7 | **`BD_PrevSessBias` intraday-session boundary version** instead of D1-prior-candle proxy | half day | Currently 52% match → target 95%+ |
| 8 | **`BD_MtfBullScore` re-validation** after the user has the proper signal-TF data (M15+H1) imported, not just M5+M10 | 1 hr | Probably already correct, need to retest |

### D. MC dashboard UX

| # | Item | Effort | Notes |
|---|---|---|---|
| 9 | **Replace the "account will eventually blow" warning** with a meaningful repeatability test: show **"X% of sims blow up before earning the average payout"** AND **"X% blow up before completing 1 full payout cycle"**. The current warning is trivially true at infinite time and not actionable. The new metric tells the user whether the strategy actually survives long enough to be profitable | half day | Lives in `MonteCarloDashboard.tsx` (verdict blocks section) |
| 10 | Add the Markov regime transition matrix UI tweak that was deferred from an earlier session | 1 day | Visualizes which regimes follow which |

### E. Infrastructure

| # | Item | Effort | Notes |
|---|---|---|---|
| 11 | **CI gate: validation harness runs on every PR.** Fails the build if any non-approx feature drifts past tolerance. Requires bundling MT5 in CI (Docker image) or running on a test fixture CSV with pre-computed expected outputs | 1 day | Best done with fixture approach to avoid the Docker MT5 install pain |
| 12 | **MT5 EA backtest reproducibility check.** Run the same `.set` twice in MT5's Strategy Tester, diff the trade lists | half day | Sanity check that MT5 itself is deterministic on our EA |

**v0.8.0 total effort:** ~7 days. Suggested batch: A → B → C → D → E.

---

## v0.8.x — Polish and known carryovers

| # | Item | Effort | Notes |
|---|---|---|---|
| ~~13~~ | ~~Gray out / collapse the 5 legacy `SCORE_W_*` + `SCORE_WILSON_CONFIDENCE` params when `ENABLE_TARGET_SCORING=true`~~ | ~~2 hrs~~ | ✅ **Done differently on `distracted-pascal-e99b0b`** — demoted to `tier="advanced"` (always behind "Show advanced") because the audit found `SCORE_W_*` are active in **both** target and legacy modes. The original "collapse when targets ON" would have hidden a knob the user still needs. |
| 14 | Default `MULTI_SEED_COUNT` 6 → 1, add a "6× multi-seed" toggle | 1 hr | UX carryover |
| ~~15~~ | ~~Merge `Ensemble` (1 param) into `Quality Filters` accordion~~ | ~~30 min~~ | ✅ **Done on `distracted-pascal-e99b0b`** — `ENSEMBLE_OVERLAP_THRESHOLD` now lives under Quality Filters with the new label "Max Trade Overlap". |
| 16 | Rename `MIN_DIST_RR` → "Min SL/TP Ratio (filter)"; rename `MIN_TRADES_PER_DAY_PASS2` → "Min Trades/Day (P2 entry)" | 30 min | UI carryover |
| 17 | Add `(?)` tooltips for `TARGET_WR_PCT` vs `MIN_WIN_RATE`, `MIN_DIST_RR` vs `TARGET_RR`, and the 5 WR fields in DiscoveryResults | 1.5 hrs | UI carryover |
| ~~18~~ | ~~Hide `INDICATOR_WARMUP_BARS`, `RECENT_BARS`, `OUTPUT_FOLDER` under "Show advanced"~~ | ~~1 hr~~ | ✅ **Done on `distracted-pascal-e99b0b`** — all three are in `_ADVANCED_KEYS`. The Advanced collapse pattern now applies to 36 params across the accordion, not just these three. |
| 19 | Per-sub-seed fractional progress emission for Discovery `[i/N]` parser (was deferred earlier) | 4 hrs | UX carryover |

**v0.8.x remaining effort:** ~7 hrs (items #14, #16, #17, #19).

---

## v0.9.0 — Genetic algorithm performance overhaul

Target: **10-15× speedup** on Pattern Discovery runs without quality loss.

| # | Item | Effort | Notes |
|---|---|---|---|
| 20 | **Cache rule-match masks across mutations** | 1 day | When GA mutates one column's range, only re-evaluate that column. Huge win on multi-column rules |
| 21 | **Replace pandas DataFrame in `_score_genetic` with NumPy histogram** | 4 hrs | Pandas overhead dominates; raw NumPy is 5-10× faster for the hot loop |
| 22 | **Vectorize trade sim across matched bars** using NumPy `searchsorted` for SL/TP hit detection | 1.5 days | Currently iterates bar-by-bar; vectorized = order-of-magnitude faster |
| 23 | **Coarse pass 1 (every-3rd bar) → full pass 2 polish** | half day | Pass-1 GA explores cheaply, pass-2 refines on full data |

**v0.9.0 total effort:** ~3.5 days. All independent — can be done in parallel by separate Claude sessions on worktrees.

---

## v0.9.x — Optional GA experiments

| # | Item | Effort | Notes |
|---|---|---|---|
| 24 | Drop island model in pass 1, replace with single 200-pop crowding selection. A/B test against current. | 2 days | May or may not be better; needs head-to-head benchmark |
| 25 | Vectorize `run_eval_phase` (Phase 1/2 MC loops) — same treatment as `run_mc_longterm` got in v0.5.0 | 1 day | Opportunistic perf win |

---

## v1.0 — Research experiments

| # | Item | Effort | Notes |
|---|---|---|---|
| 26 | **Optuna / TPE sampler** instead of GA. Head-to-head vs GA on same dataset | 2-3 days | Bayesian optimization may outperform GA on this objective shape |
| 27 | **Surrogate fitness model** — fast NN/GBM predicts rule fitness; GA queries it 90% of the time, real eval 10%. Could 10-20× speed on top of v0.9.0 wins | 3 days | High R&D risk, high reward |

---

## Out of scope / explicitly declined

- **Live tick-volume CSV refresh** — user accepted small drift as natural noise (2026-05-17). Robust patterns should tolerate ±5% volume noise; if they don't, they're overfit.
- **Switching to "MT5 is the source of truth at runtime"** — too heavy a refactor; CSV-based pipeline is fast and reproducible.
- **Source repo open-sourcing** — staying private, dual-repo release pipeline stays.

---

## Validation harness scoreboard (as of v0.7.1)

15 PASS · 3 approx (known) · 9 differ (categorized — not bugs)

| Bucket | Features | Why they differ | Fix path |
|---|---|---|---|
| ✅ PASS exact | rsi14, atr_pct, bb_width, trend, body_pct, bb_expanding, bull, uwk_pct, lwk_pct, inside_bar, outside_bar, vwap_dist, stoch_k, stoch_d, pin_bar (15) | Identical math, IEEE float precision | None needed |
| 🟡 approx (documented) | mtf_bull_score, prev_sess_bias, regime (3) | Cheaper-but-different math in MT5 | v0.8 items #6, #7, #8 |
| 🟠 Wilder seeding convergence | macd_norm, rng_atr, rolling_sharpe (3) | MT5 has fewer warmup bars than Python's full-year CSV | Resolves itself with more MT5 history; not a bug |
| 🟠 Tick volume snapshot drift | vol_ratio, vol_body_conf, vol_price_div, poc_dist (4) | CSV froze volume at import; MT5 has live values | Out of scope (user decision); margin-aware GA in v0.8 item #3 |
| 🟠 Minor alignment/threshold | htf_div (79%), sd_zone (95%) (2) | merge_asof vs iBarShift edge timing; ±1 ATR borderline cases | v0.8 if needed, otherwise within noise |
