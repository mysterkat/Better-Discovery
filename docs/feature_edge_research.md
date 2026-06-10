# Feature / Information Sources for Real Edge (XAUUSD)

Research catalog for expanding the discovery feature set beyond the current 27
price-derived indicators, which showed ~no box-only edge (all boxes land at the
~43% breakeven win-rate for the ~1.3 RR — i.e. statistically indistinguishable
from random entries). The reason: every current feature is derived from the same
price stream, so it carries no information the market hasn't already arbitraged.

**Real edge comes from information that is (a) orthogonal to price, (b) not fully
priced in, and (c) expressible as a box condition the EA can compute.**

---

## Hard constraints (the filter every idea must pass)

Any feature must be computable in **THREE** places identically, or it's useless:
1. **Python discovery** — to find the box.
2. **MQL5 EA** — to fire the box live.
3. **MT5 Strategy Tester** — to validate, and (critically) to reproduce discovery.

This kills several otherwise-great ideas and shapes the architecture:
- **WebRequest is DISABLED in the Strategy Tester.** No live API calls in backtest.
  → All external data must be **pre-downloaded into time-aligned CSVs** and read via
  `FileOpen` (which DOES work in the tester). This "file-injection" pattern is the
  key enabler for ALL non-price data (COT, calendar, sentiment, macro…).
- **Market depth / DOM is not available in the tester.** No real order book backtest.
- **Real (exchange) volume** is broker-dependent and often absent in the tester; tick
  volume is always available.
- **Multi-symbol works in the tester** (CopyRates/iClose on other symbols) *if* the
  broker offers the symbol and its history — so cross-asset is feasible.
- **No look-ahead.** External series must be stamped at **release time**, not event
  time (COT for Tue data releases Fri; economic figures release at the announced
  minute). Forward-fill from the moment it was *knowable*.
- **Box-expressible.** The feature must reduce to a number the GA can threshold
  (distance, ratio, regime code, minutes-to-event, percentile).

## Two architectural enablers to build

1. **Multi-symbol pipeline** — load aligned M10 (or chosen TF) history for a basket
   of related symbols; expose cross-asset features in both Python and MQL5.
2. **External-data file-injection pipeline** — a generic `(timestamp, value...)` CSV
   loader, forward-filled onto the bar timeline, read identically by Python and by
   MQL5 `FileOpen` in the tester. This single mechanism unlocks COT, calendar,
   sentiment, ETF flows, macro — anything.

## The reframing that matters most

The current features are all **fast price**. The highest-edge information (macro,
positioning, real yields) is **slow** (daily/weekly). The win is to **combine**:
use slow orthogonal info as a **regime / directional bias filter**, and fast price
structure for **entry timing**. A box can express exactly this conjunction:
`(DXY_daily_trend < 0) AND (real_yield_falling) AND (price swept PDL) AND (London hour)`.
That's a fundamentally different, more defensible edge than any single-bar oscillator.

---

## TIER 1 — highest edge / effort, build first

### 1. USD strength (DXY) — gold's #1 driver
Gold is strongly inversely linked to the US dollar. Most moves are a *reaction* to
USD.
- **Features:** DXY trend (multi-TF), DXY momentum, DXY distance from MA, gold–DXY
  divergence (gold up while DXY up = anomaly), rolling gold/DXY correlation & its
  regime, DXY at-extreme (percentile).
- **MQL5:** `iClose("USDX"/"DXY", tf, shift)` or proxy from `EURUSD`/`USDJPY` basket.
- **Tester:** works if the symbol/proxy has history. **Parity:** add the symbol's
  aligned CSV to discovery.
- **Edge:** HIGH. Probably the single most valuable addition.

### 2. Time / session / event-schedule features (no external API needed)
Gold has strong, persistent intraday and calendar structure.
- **Features:** hour-of-day (finer than current `session`), minutes since London/NY
  open, day-of-week, day-of-month, turn-of-month flag, pre-holiday flag, month
  (seasonality), London-fix windows (10:30 & 15:00 London), rollover window.
- **Event-schedule (huge for gold):** ship a static CSV of known recurring
  high-impact events — **NFP** (1st Fri 13:30 UTC), **FOMC** (8 known dates/yr),
  **CPI** (monthly), ECB, etc. Compute **minutes-to-next-event**, **minutes-since**,
  **is-event-day**, **in-blackout-window**. Gold typically ranges before and breaks
  after these.
- **MQL5:** `TimeToStruct` for time; `FileOpen` the schedule CSV for events.
- **Tester:** 100% reproducible (no live calendar API needed). **Edge:** MED-HIGH,
  cheap to build, fully honest.

### 3. Price *structure* (richer than oscillators, still price-only)
Structural levels are where real orders cluster → genuine reaction points.
- **Round numbers:** distance to nearest $X00 / $X0 level. Gold respects whole/half
  levels strongly (psychological). **Easy + real.**
- **Prior session levels:** distance to PDH / PDL / PDC, prior-week H/L. Heavily
  traded reference points.
- **Opening range:** Asian-session range, first-hour range; position within it;
  breakout/failure flags.
- **Swing structure:** distance to last swing high/low; liquidity-sweep flag (price
  ran a prior high then reversed — ICT "stop run").
- **Anchored VWAP** from session/week open + bands (have `vwap_dist`; anchor it).
- **MQL5/Tester:** all pure price → trivial everywhere. **Edge:** MED-HIGH; round
  numbers + PDH/PDL are classic robust gold edges.

### 4. US real yields / rates (gold's other primary driver)
Gold competes with real-yielding assets; falling real yields → gold up.
- **Features:** 10Y yield trend/level, real-yield proxy (yield − inflation breakeven),
  bond (TLT/ZN) trend, yield momentum, gold–yield divergence.
- **MQL5/Tester:** if the broker offers a yield/bond symbol → multi-symbol; else
  **file-inject** daily yields (FRED: DGS10, DFII10 real yield, T10YIE breakeven —
  all free, downloadable). **Edge:** HIGH (macro regime).

---

## TIER 2 — high edge, more data/plumbing

### 5. COT positioning (CFTC, weekly) — strong swing edge
Net positioning of commercials vs large specs in COMEX gold. Extreme spec longs →
exhaustion/reversion; commercial accumulation → trend support.
- **Source:** CFTC weekly (free). Released Fri 15:30 ET for Tue data → **lag it**.
- **Features:** net-spec % of OI, z-score of positioning, week-over-week change,
  extreme flag.
- **Plumbing:** file-injection, forward-filled. **Edge:** HIGH at swing scale → use
  as a **bias filter**, not a timing signal.

### 6. Cross-asset ratios & risk regime
- **Gold/Silver ratio** (XAUUSD/XAGUSD) — mean-reverting; regime.
- **Risk sentiment:** SPX/US500 trend, **VIX** level/regime (risk-off → gold bid).
- **Oil** (inflation/commodity proxy), **BTC** (occasional risk proxy).
- **Cross-currency gold** (XAUEUR/XAUJPY) divergences.
- **MQL5/Tester:** multi-symbol. **Edge:** MED-HIGH.

### 7. Volatility regime / term structure
- Realized-vol percentile (ATR vs long-run), short/long ATR ratio (vol term
  structure), intraday vol-by-hour seasonality, gap size (weekend/overnight),
  **GVZ** (gold implied vol) if available/injected.
- **Edge:** MED — best as a *conditioner* (trade trend vs reversion by regime).

### 8. Cross-asset lead–lag (microstructure-ish, still feasible)
Does DXY/yields/SPX movement at bar *t* predict gold at *t+1..t+k*? Rolling lead-lag
/ "DXY just broke, gold hasn't reacted yet" features. **Edge:** MED-HIGH if a real
lag exists; pure multi-symbol price.

---

## TIER 3 — real but harder / external sourcing

- **Retail sentiment (contrarian):** broker long/short ratio, IG/Myfxbook, Dukascopy
  SWFX sentiment. Crowd-long → fade. File-inject. **Edge:** MED-HIGH, well-documented
  contrarian signal.
- **ETF flows:** GLD tonnes held (daily) — demand proxy. File-inject.
- **Options skew:** 25-delta risk reversal, put/call — sentiment/tail pricing.
- **Geopolitical Risk Index** (Caldara–Iacoviello, free monthly) — spikes → gold bid.
- **Macro surprises:** CPI/NFP actual-vs-consensus surprise index. File-inject at
  release.
- **Central-bank gold buying** (WGC quarterly), **money supply / real rates / DXY
  positioning**, **COMEX warehouse stocks**, **mining cost curve** — slow structural
  demand/supply. The literal "apples sold" data: any series plausibly tied to gold,
  handled by the same file-injection + forward-fill + box-threshold pattern.
- **Google Trends** ("gold price" search interest) — attention proxy; file-inject.

Explicitly NOT pursuing: astrology/lunar cycles and similar — no plausible mechanism,
pure overfit bait.

---

## Recommended first batch (cheapest path to a real test)

1. **Time + event-schedule features** (Tier 1.2) — zero external symbols, file-only,
   100% tester-reproducible, real edge. Lowest risk, do first.
2. **Price structure: round numbers + PDH/PDL/session-range** (Tier 1.3) — pure
   price, trivial parity, classic gold edges.
3. **DXY** (Tier 1.1) — first cross-asset; biggest single macro lever.
4. Then **real yields** (file-inject) and **COT** (file-inject) as regime/bias filters.

Each must be added in **both** the Python feature builder (`add_*_features`) **and**
the EA template (MQL5), wired into `GENE_COLS` + the `.set` exporter, and validated
box-only. Keep box-only honest scoring on throughout — more features without it just
manufactures overfit.

## Honest caveats
- The most predictive info (macro, COT, yields) is **slow** — it sharpens *bias/regime*,
  not M10 timing. Expect it to work as a filter that makes the fast-structure entries
  selective, not as a standalone signal.
- Each external series adds **look-ahead risk**; align to release time religiously.
- Sourcing + aligning external data is the real cost. Cross-asset (symbols already at
  the broker) is cheaper than CFTC/FRED/sentiment CSVs.
- Even with all this, a single static box may be the wrong *model* for some edges
  (sequence/timing/regime-switching). The macro-regime + price-structure conjunction
  is the box-expressible sweet spot.
