# BETTER DISCOVERY — Roadmap

> Living document. Updated 2026-05-23 (v2.0.0 shipped — Optuna removed; GA is the sole optimizer. First final, no patches planned).
> Items grouped by target version. Effort is rough; ranking inside each version is by priority.
> ✅ markers indicate items that have shipped.

---

## Shipped — v2.0.0

Final working release. **Optuna removed entirely; the Genetic Algorithm is now the sole optimizer.**

After the full four-wave optimizer overhaul (v1.1–v1.4) and head-to-head benchmarking, Optuna lost to the GA on **both** axes — even after tuning both sides:

- **Wall time:** ~530s (Optuna) vs ~300s (GA) on the same dataset.
- **Quality:** Optuna runs produced **no patterns passing the quality filters**; the GA reliably did.

So the Optuna path (TPE/CMA-ES samplers, multivariate/group tuning, parallel-trial knobs, `study.enqueue_trial()` warm-start) and its `optuna>=4.0` dependency were dropped. The shared scaffolding the GA also relies on stays intact: the NumPy fitness scorer (`_score_genetic`), rule-match mask cache, LightGBM leaf-rule warm-start (`leaf_rules`), the surrogate model (`SURROGATE_*`), and LightGBM clustering (`CLUSTERING_METHOD`). The `GENE_OPTIMIZER` selector and all `OPTUNA_*` params were removed from the Discovery tab; the "Search Budget" group is now plain GA generations/population.

Historical Optuna entries below (v1.0–v1.4) are kept as-is for the record.

---

## Shipped — v0.7.2

Strategy Library + Compare tab. Shipped 2026-05-21 alongside v0.7.1 hotfix base. The originally-planned "trade-count gap fix" work is the next milestone (v0.8.0 below).

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

Target: **10-15× speedup** on Pattern Discovery runs without quality loss. **All shipped.**

| # | Item | Effort | Notes |
|---|---|---|---|
| 20 | ✅ **Cache rule-match masks across mutations** | 1 day | When GA mutates one column's range, only re-evaluate that column. Huge win on multi-column rules. **Shipped v0.9.0.** |
| 21 | ✅ **Replace pandas DataFrame in `_score_genetic` with NumPy histogram** | 4 hrs | Pandas overhead dominates; raw NumPy is 5-10× faster for the hot loop. **Shipped v0.9.0.** |
| 22 | ✅ **Vectorize trade sim across matched bars** using NumPy `searchsorted` for SL/TP hit detection | 1.5 days | Currently iterates bar-by-bar; vectorized = order-of-magnitude faster. **Shipped v0.9.0.** |
| 23 | ✅ **Coarse pass 1 (every-3rd bar) → full pass 2 polish** | half day | Pass-1 GA explores cheaply, pass-2 refines on full data. **Shipped v0.9.0.** |

---

## v0.9.x — Optional GA experiments

| # | Item | Effort | Notes |
|---|---|---|---|
| 24 | ✅ Drop island model in pass 1, replace with single 200-pop crowding selection. A/B test against current. | 2 days | **Shipped v0.9.0** as `GENE_USE_CROWDING=True` (default). Island model preserved as opt-out. |
| 25 | ✅ Vectorize `run_eval_phase` (Phase 1/2 MC loops) — same treatment as `run_mc_longterm` got in v0.5.0 | 1 day | **Shipped v0.9.0.** |

---

## v1.0 — Research experiments

| # | Item | Effort | Notes |
|---|---|---|---|
| 26 | ✅ **Optuna / TPE sampler** instead of GA. Head-to-head vs GA on same dataset | 2-3 days | Bayesian optimization may outperform GA on this objective shape. **Shipped v1.0.0-dev.** |
| 27 | ✅ **Surrogate fitness model** — fast NN/GBM predicts rule fitness; GA queries it 90% of the time, real eval 10%. Could 10-20× speed on top of v0.9.0 wins | 3 days | High R&D risk, high reward. **Shipped v1.0.0-dev** (opt-in via `SURROGATE_ENABLED`). |

---

## Shipped — v1.1 through v1.4 (optimizer overhaul)

After initial benchmarks showed Optuna underperforming GA on both wall time (530s vs 300s) AND quality (no patterns passing filters), four waves of optimizer work landed:

- **v1.1.x** — Stability fixes that unblocked benchmarking: backend `fd` limit raised to 8192 (`msvcrt._setmaxstdio` via ctypes); MCP `wait_for_job` reuses one `httpx.Client` for entire poll loop (1800 sockets → 1); `/discovery/set-file` and `clear-cache` honor custom `OUTPUT_FOLDER`; cache-clear stops silently lying about deletion failures.
- **v1.2.0** — Optuna tuning suite: `multivariate=True` + `group=True` on TPE (joint-distribution sampling, +15-25% quality on correlated bounds); CMA-ES alternative sampler; `OPTUNA_TPE_N_STARTUP=20` warmup; `OPTUNA_PARALLEL_TRIALS` for thread-based intra-study parallelism.
- **v1.3.0** — LightGBM tree-based clustering (`CLUSTERING_METHOD=lightgbm`): replaces statistical KMeans with profit-aware leaf partitioning. Each leaf is a feature-conjunction rule whose member bars share predicted forward returns. Optimizer never wastes work on unprofitable clusters.
- **v1.4.0** — Leaf-rule warm-start: extracts the LightGBM leaf's path through the tree as a `{col: (lo, hi)}` rule. GA seeds initial population with `[seed_rule, mutate(seed_rule) × N-1]`; Optuna uses `study.enqueue_trial()` to evaluate the leaf rule as the first trial. Optimizer now polishes a known-good rule instead of searching from scratch.

**Recommended benchmark protocol going forward:** four-config grid `(GA | Optuna) × (KMeans | LightGBM)`. v1.4.0 hypothesis: LightGBM rows substantially beat KMeans rows on quality; Optuna+LightGBM closes the wall-time gap with GA because warm-start cuts convergence time.

---

## v1.5 — Optimizer polish (post-benchmark)

Run the v1.4.0 benchmark first. If quality plateaus, these are the next-best low-risk wins on top. All independent; ~half day combined.

| # | Item | Effort | Notes |
|---|---|---|---|
| 46 | **Multi-tree leaf warm-start.** v1.4.0 only warm-starts when `LIGHTGBM_N_ESTIMATORS=1`. For multi-tree ensembles each cluster's path is a tuple of leaves — conjunction those rules into one richer seed. | 3 hrs | Unlocks the warm-start benefit when users want more granular clusters (n_estimators > 1). +5-10% on the lightgbm path. |
| 47 | **Pass-2 leaf-aware narrowing.** Pass 2 currently narrows quantile ranges blindly (`PASS2_QUANTILE_LO/HI`). Narrow to the leaf's actual bound range instead — Pass 2 converges in fewer generations because the search space is much smaller. | 2 hrs | Faster pass 2 + better convergence; only active when lightgbm clustering is on. |
| 48 | **Early stopping for the GA.** It currently runs for a fixed budget. Add "stop if no improvement for N generations." When warm-start finds a near-optimum quickly, you'd save 30-50% of optimizer wall time. | 1 hr | Cleanest unshipped speed win. Default `EARLY_STOP_PATIENCE=0` (off) for backward compatibility. |
| 49 | **Cross-cluster HOF sharing.** If cluster A's best rule scores well on cluster B's bars too, add it to B's hall-of-fame. Helps discover patterns that generalize across leaves. | 2 hrs | Modest quality bump; mostly useful when many clusters share underlying market structure. |

---

## v1.6 / Large-dataset items — pay off as bar count grows

These are higher-effort and have meaningful tradeoffs, but become increasingly valuable as datasets grow. Document them now so they're ready when you start running on weekly-resolution multi-year datasets.

| # | Item | Effort | When it matters | Notes |
|---|---|---|---|---|
| 50 | **Walk-forward validation in the optimization loop.** Replace the fixed train/test split with a rolling 5-fold walk-forward. Rules scored by their *cross-fold consistency* generalize much better to live MT5 trading. | 1-2 days | Most valuable on datasets spanning **multiple market regimes** (≥18 months of multi-TF data). Less impactful on short datasets where there isn't much regime variation. | 2-3× compute cost per run, but the rules that survive are dramatically more robust. Biggest unaddressed "real-world quality" lever. |
| 51 | **Custom fitness function with regime weighting + recency boost.** Current target-driven scoring weights all bars equally. Add (a) recency boost so newer bars matter more, (b) per-regime consistency term so rules that work in only one regime get penalised. | half day to wire + benchmark | Pays off on **large datasets where regime composition varies** (e.g. half bull + half ranging). On uniform datasets the term collapses to constant and adds no value. | Changes WHICH rules win, not just search efficiency — requires its own benchmark to verify new winners actually do better in MT5. |
| 52 | **Pre-filter candidates with the surrogate.** Train a fast classifier on `(rule_features → composite_score)` from past runs, predict scores for new candidates BEFORE optimizing, only optimize promising ones. Skips compute on cluster/direction combos that historical data says won't work. | 1 day | Becomes valuable once you have **>10 past discovery runs** in the library — needs training data. Especially helpful for parameter sweeps where you're re-running with small tweaks. | The surrogate from v1.0.0-dev was per-run; this would be cross-run with persistent state in `userdata/`. |
| 53 | **Quantile-bucket feature pre-computation.** Replace per-call `(vals >= lb) & (vals <= hb)` float comparisons with int8 quantile-bucket lookups. Pre-compute the bucket index once per bar at discovery start. Inner-loop comparison becomes integer-only. | half day | On **large datasets (>500k bars)** the float-compare loop starts to dominate. At <100k bars it's negligible. | Estimated 1.5-2× on the GA fitness path at scale. Adds a one-time precompute step (~5s for 500k bars). |
| 54 | **Persistent column-mask cache with LRU eviction.** Move the per-worker mask cache to module-level with size-bounded LRU. Survives across all candidates in a run, not just one worker. | 4 hrs | Helps **multi-cluster runs (>30 clusters)** where many rules share column bounds. Marginal benefit at small cluster counts. | GA benefits scale with cluster count. |
| 55 | **Adaptive multi-tree warm-start fusion.** When `LIGHTGBM_N_ESTIMATORS>1`, instead of just conjuncting all tree-leaf rules, weight by each tree's contribution to the leaf's prediction. Tighter, more confident bounds. | 1 day | Companion to #46. Becomes useful as users tune up `N_ESTIMATORS` for finer cluster counts on large datasets. | Modest quality bump on top of #46. |

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
