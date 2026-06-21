# Strategy Development Backlog

## Robustness Sequence

Preserve this sequence for a future improvement to the strategy-development
phase workflow:

1. In-sample excellence.
2. In-sample permutation test.
3. Walk-forward test.
4. Walk-forward permutation test.

Implementation intent:

- Treat in-sample excellence only as candidate generation, never validation.
- Use permutation tests to estimate whether observed performance is materially
  better than results obtainable from randomized structure or luck.
- Run chronological walk-forward folds with all fitting confined to each fold's
  training segment.
- Apply permutation analysis to the complete walk-forward procedure, not only
  to the final combined return series, so selection and refitting bias are part
  of the null distribution.
- Preserve time-series dependence where required through block, session, or
  regime-aware permutations rather than blindly shuffling individual bars.
- Predefine statistics, permutation count, significance threshold, folds, costs,
  and rejection rules before viewing results.
- Keep the existing untouched lockbox after these four stages. Passing this
  sequence permits lockbox evaluation; it does not establish profitability.

This is a recorded future requirement. It is not yet implemented or an active
campaign gate.

## Local Replay Monte Carlo Export

The future local chart/tick-replay backtester must export a canonical closed-
trade ledger that can be consumed directly by the existing Monte Carlo engine.
The export must include at least:

- strategy and dataset fingerprints;
- provider, venue, symbol and timeframe;
- entry and exit timestamps and prices;
- direction, size, SL, TP and exit reason;
- gross PnL, spread, commission, swap, slippage and net PnL;
- initial risk and realized R multiple;
- holding bars/time, session and regime;
- validation fold and dataset role;
- deterministic replay configuration and engine version.

Monte Carlo must run separately on:

1. the local replay trade ledger; and
2. the native MT5 HTML report's parsed trade ledger.

The two simulations must then be compared using identical Monte Carlo settings,
including seed, resampling method, path count, horizon and account constraints.
Compare trade count, expectancy, variance, drawdown distributions, ruin/failure
probability and funded-account pass probability. Never pool the two ledgers into
one sample: disagreement is parity evidence that must be explained before demo
forward testing.
