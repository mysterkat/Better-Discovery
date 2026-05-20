# MT5 EA Backtest Reproducibility Check

Verifies that MetaTrader 5's Strategy Tester produces identical trade lists
when the same `.set` file is run twice under identical conditions.

## Why this matters

If MT5 is not deterministic (e.g. due to floating-point non-determinism,
price feed variation, or EA state leakage), validation comparisons between
Python discovery output and MT5 are meaningless. This one-time sanity check
confirms MT5 is deterministic on our EA before trusting any validation numbers.

## Prerequisites

- MT5 installed and connected to a broker with history data
- `PatternDiscoveryEA` compiled and present in MT5's Expert Advisors list
- A `.set` file from a recent discovery run (any will do)
- Python 3.x with `pandas` installed

## Steps

### 1. Run the backtest twice

In MT5 → View → Strategy Tester:

1. Load `PatternDiscoveryEA` and your `.set` file.
2. Set the test range (e.g. 2023-01-01 to 2024-01-01), timeframe M15.
3. Run 1 — save the trade report: **right-click the "Trades" tab → Save as CSV** → `run_1.csv`
4. Close the tester, reopen it with the same settings.
5. Run 2 — save the trade report: `run_2.csv`

> **Important:** Both runs must use **"Every tick based on real ticks"** or
> **"Every tick"** mode. "Open prices only" mode is inherently non-deterministic
> across broker data refreshes.

### 2. Diff the trade lists

Run the script below (also at `scripts/check_reproducibility.py`):

```
python scripts/check_reproducibility.py run_1.csv run_2.csv
```

Expected output on a deterministic EA:

```
Comparing run_1.csv vs run_2.csv
  Run 1 trades: 142
  Run 2 trades: 142
  Trade count match: OK
  Column diffs: none
  All trades identical: OK
PASS — EA backtest is deterministic.
```

### 3. Interpreting failures

| Symptom | Likely cause |
|---|---|
| Trade count differs by 1–3 | Off-by-one at the date boundary — tighten the date range by 1 day on both ends |
| Prices differ by < 1 pip | Broker quote revision between runs — use a fixed offline history file |
| SL/TP hit on different bars | Wilder-style smoothing in an indicator with different initialisation — check EA `OnInit()` resets |
| Random-looking differences | EA or indicator uses `rand()` or time-seeded RNG — fix by seeding with a constant |

## Script: `scripts/check_reproducibility.py`

Run with: `python scripts/check_reproducibility.py <run1.csv> <run2.csv>`
