# BETTER DISCOVERY — MT5 Indicator Stack (v0.7.0)

This folder ships native MetaTrader 5 indicators that mirror the 12
custom features Pattern Discovery uses internally, plus a small helper EA
that lets the host app preconfigure your MT5 charts automatically.

```
backend/mt5/
├── indicators/   12× BD_*.mq5  — drop-in chart indicators
└── services/
    ├── BD_AutoSetup.mq5     — JSON-driven chart opener / template applier
    └── BD_FeatureDump.mq5   — one-shot CSV dumper for the validation harness
```

## Indicators (drop on chart, use like any custom indicator)

| File | What it shows |
|---|---|
| `BD_PinBar.mq5`         | Pin-bar score `[0..1]` — dominant wick / range |
| `BD_RollingSharpe.mq5`  | Rolling Sharpe of last 20 close-to-close returns, clipped ±3 |
| `BD_MacdNorm.mq5`       | MACD histogram normalised by ATR — `(MACD − Signal) / ATR` |
| `BD_VwapDist.mq5`       | Distance from rolling 96-bar VWAP, % of close, clipped ±5 |
| `BD_SDZone.mq5`         | Supply / Demand proximity via 25-bar swing + 1 ATR threshold |
| `BD_VolPriceDiv.mq5`    | High-vol price divergence: +1 accumulation, −1 distribution |
| `BD_BBExpanding.mq5`    | 1 if Bollinger width is greater than 3 bars ago |
| `BD_PrevSessBias.mq5`   | Previous D1 candle bias as session proxy |
| `BD_POCdist.mq5`        | Distance from a 100-bar / 20-bin volume-profile POC |
| `BD_Regime.mq5`         | Categorical regime `[0..4]` — TrUp / TrDn / Squeeze / WideVol / Choppy |
| `BD_HtfDiv.mq5`         | LTF vs HTF (default M15) RSI slope divergence |
| `BD_MtfBullScore.mq5`   | Additive multi-TF bull score (chart + 4 signal slots) |

Standard features (`RSI14`, `ATR%`, `BB width`, `EMA trend`, `Stoch K/D`,
`body_pct`, `rng_atr`, `vol_ratio`, …) are computed by built-in MT5
indicators — no custom file needed.

## First-run UX (BETTER DISCOVERY app)

1. Open BETTER DISCOVERY → **Data Import** tab.
2. Click **Test Connection** — the app installs the BD indicators + helper
   EA into your live MT5 install (idempotent; safe to re-click).
3. If the app shows _"MetaEditor not found"_, open MT5 once (F4) so it
   compiles the freshly-installed `.mq5` files. Then return to step 2.
4. In MT5: drag **Experts → BetterDiscovery → BD_AutoSetup** onto any
   chart, tick **Allow algorithmic trading**, click OK. Leave it
   attached — the app drives it from now on.
5. Back in the app, click **Open Charts in MT5** — the helper EA opens a
   chart for every (symbol, timeframe) row you've configured and
   attaches the full BD indicator stack to each.

You only do step 4 once per MT5 install. Re-clicking _Open Charts_ later
applies a new symbol/TF set without any further interaction.

## What the helper EA reads

`<terminal_common>\Files\bd_setup.json` — schema:

```json
{
  "version": 7,
  "symbol": "XAUUSD",
  "timeframes": ["M5", "M15", "H1"],
  "indicators": ["BD_PinBar", "BD_RollingSharpe", "..."],
  "htf_for_div": "M15"
}
```

The host increments `version` each time it wants the EA to act. The EA
writes back `bd_setup_ack.json` containing the chart IDs it opened so the
host can confirm.

## Validation harness (drift detector)

If you ever suspect MT5 indicator output drifts from Python:

```bash
# 1. Python ground truth
python -m backend.tools.validate_ea_features python-dump \
    --hist userdata/hist_data/xauusd_m5.csv \
    --out  userdata/validation/python_features.csv

# 2. In MT5: drag BD_FeatureDump.mq5 onto the same chart, wait for
#    "DONE — wrote bd_feature_dump.csv", detach. Copy that CSV from
#    <terminal_common>\Files\ to userdata/validation/

# 3. Diff
python -m backend.tools.validate_ea_features diff \
    --python userdata/validation/python_features.csv \
    --mt5    userdata/validation/bd_feature_dump.csv \
    --report userdata/validation/diff_report.txt
```

The diff prints per-feature `max_abs`, `mean_abs`, `p99_abs`, and `% of
bars that match within tolerance` (default 1e-3 abs OR 1% rel). Columns
tagged `approx` (`regime`, `prev_sess_bias`, `mtf_bull_score`) don't fail
the run because they depend on rolling quantiles or session boundaries
that don't align perfectly across the two implementations.
