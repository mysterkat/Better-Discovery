# MT5 Setup Guide for the Pattern Discovery EA

This doc explains exactly what you need to prepare in MetaTrader 5 so the EA generated from a discovered pattern (`.set` → `.mq5`) runs correctly.

## TL;DR

1. **Download historical data** in MT5 for every timeframe used in your discovery run (primary + signal TFs).
2. **Attach the compiled EA** to a chart of the **primary timeframe** — the same TF you set as `PRIMARY_TF` in the discovery run.
3. **Set the `SignalTF1..SignalTF4` inputs** in the EA to match the signal TFs you trained on. Unused slots stay at `PERIOD_CURRENT`.

The EA creates its own indicator handles internally — you do **not** need to manually attach RSI, MACD, EMA, etc. to any chart.

## Indicators the EA computes for you

These are wired up automatically via `iRSI` / `iMA` / `iATR` / etc. in `OnInit()`:

### On the primary timeframe (chart's current TF)
| Indicator | Settings | Used for |
|-----------|----------|----------|
| RSI | period 14, close | `rsi14`, `htf_div` (compared to signal RSI) |
| MACD | 12, 26, 9, close | `macd_norm` |
| ATR | period 14 | `atr_pct`, `rng_atr` |
| Bollinger Bands | 20, 2.0σ, close | `bb_width`, `bb_expanding` |
| EMA | 20, 50, 200 (close) | `trend` (EMA20 > EMA50 > EMA200 = up) |
| Stochastic | %K=14, %D=3, slowing=1, SMA, low/high | `stoch_k`, `stoch_d` |

### On each active Signal TF (slot 1..4 if not `PERIOD_CURRENT`)
| Indicator | Settings | Used for |
|-----------|----------|----------|
| RSI | period 14, close | `htf_div` (only slot 1 is used) |
| EMA | 20, 50, 200 (close) | contributes to `mtf_bull_score` |

### On the D1 timeframe (always)
| Source | Used for |
|--------|----------|
| Yesterday's OHLC | `prev_sess_bias` (daily candle direction) |

No indicators on D1 — just price.

## Historical data — the silent failure mode

If MT5 doesn't have history loaded for one of your signal TFs, the indicator handle returns `EMPTY_VALUE` and that slot contributes 0 to `mtf_bull_score`. **The EA won't error — it'll just silently underweight that signal.**

To make sure history is loaded:

1. Open MT5 → `View → Symbols` → make sure your symbol is in Market Watch.
2. For every TF you'll use (primary + each signal), open a chart on that TF once. MT5 fetches history on demand.
3. Optionally `Tools → Options → Charts → Max bars in history = 100000+` to keep enough.

A quick sanity check: open the EA's journal after starting it. The first `Print` shows `Signal TFs active: N`. If N is less than what you configured, one of the slots failed to create handles (usually missing history).

## Matching discovery → EA

The pattern discovery run prints something like:

```
TF1 (PRIMARY): xauusd_m5.csv
TF2 (signal): xauusd_m15.csv
TF3 (signal): xauusd_h1.csv
```

Translate to EA inputs:
- Attach to a chart of the primary TF (M5 in this example).
- `SignalTF1 = PERIOD_M15`
- `SignalTF2 = PERIOD_H1`
- `SignalTF3 = PERIOD_CURRENT`
- `SignalTF4 = PERIOD_CURRENT`

If you discovered with 5 TFs, fill all 4 signal slots accordingly.

## `mtf_bull_score` range — heads up

In v4 of the EA, the score is **additive**: it sums the primary trend with every active signal trend. Range is `0 .. (1 + number of active signals)`.

- 1 primary + 2 signals → range 0..3
- 1 primary + 4 signals → range 0..5

The `.set` file generated from discovery already uses the matching range, so the `mtf_bull_score_lo / hi` filter thresholds line up automatically. **Don't edit those manually unless you understand the new range** — a v3 threshold of `>= 1.5` means something different now.

## Common mistakes

| Symptom | Cause |
|---------|-------|
| EA never opens trades | Missing history on a signal TF → most filters fail silently. Open each TF chart once. |
| `mtf_bull_score` filter is too strict | Old `.set` file from a v3 (range 0-2) build run on a v4 EA with 4 signals. Re-export the `.set` from current discovery. |
| Direction discriminator picks wrong dir | `Discrim_Col` index in `.set` doesn't match v6 column table — re-export. |
| Trades open during banned hours | `HoursBan` is **local time** by design. Use `EODCloseHour` if you want UTC-aligned cutoffs. |

---
*This doc covers v4.00 of the EA, which ships bundled at `backend/ea/PatternDiscoveryEA.mq5` and is generated into `userdata/mql/` per export.*
