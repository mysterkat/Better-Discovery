"""GA-fitness ↔ gate-sim parity.

The GA (`_score_genetic`) must score the EXACT trade process the gate sim
(`_bt_worker_dir`) measures and the exported EA executes:

  - same hold window (MAX_HOLD_BARS, not FORWARD_BARS)
  - pessimistic same-bar SL/TP tie-break (stop fills first)
  - timeout exits booked at the hold-window close (realised move / risk)
  - spread charged on entry AND on the effective exit trigger levels
  - serialized entries with the EA's exit-anchored cooldown arithmetic
    (anchor = exit bar for intrabar closes, exit bar + 1 for timeouts)
  - commission / per-bar swap subtracted before WIN/LOSS classification

If these drift apart again, the GA optimizes a different objective than the
gate reports and MT5 reproduces — the root cause of discovery↔MT5 mismatch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "toolkit"))

import pattern_discovery_v6 as pd6


def _make_market(n: int = 3000, seed: int = 7):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 2.0, n).cumsum()
    cl = 2000.0 + steps
    op = np.empty(n)
    op[0] = cl[0]
    op[1:] = cl[:-1]
    spread_noise = rng.uniform(0.5, 4.0, n)
    hi = np.maximum(op, cl) + spread_noise
    lo = np.minimum(op, cl) - spread_noise
    rsi = rng.uniform(0.0, 100.0, n)
    return op, hi, lo, cl, rsi


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("commission,swap", [(0.0, 0.0), (0.05, 0.002)])
def test_ga_trade_stream_matches_gate_sim(monkeypatch, direction, commission, swap):
    op, hi, lo, cl, rsi = _make_market()
    n = len(cl)
    spread = 0.30
    sl_pct, tp_pct = 0.004, 0.005
    max_hold, cooldown = 32, 4

    monkeypatch.setattr(pd6, "MAX_HOLD_BARS", max_hold)
    monkeypatch.setattr(pd6, "COOLDOWN_BARS", cooldown)
    monkeypatch.setattr(pd6, "COMMISSION_R", commission)
    monkeypatch.setattr(pd6, "SWAP_R_PER_BAR", swap)
    # Disable the box-only looseness guard: this test matches every bar on
    # purpose (the guard is a performance gate, not part of trade semantics).
    monkeypatch.setattr(pd6, "GENE_SCORE_BOX_ONLY", False)

    member_bi = list(range(0, n - 1))

    # ── gate sim ────────────────────────────────────────────────────────────
    zeros = np.zeros(n)
    pd6._init_bt(
        hi, lo, cl, op, zeros.astype(np.int8), pd6.FORWARD_BARS, n,
        spread, True, max_hold, [], cooldown,
        zeros, zeros, zeros, zeros, zeros, zeros,
    )
    _, _, sim_trades = pd6._bt_worker_dir(
        (0, member_bi, sl_pct, tp_pct, direction))

    # ── GA fitness trade stream ──────────────────────────────────────────────
    arrays = {"rsi14": rsi}
    pd6._init_genetic(arrays, hi, lo, cl, op, pd6.FORWARD_BARS, n, spread)
    rule = {"rsi14": (-1.0, 101.0)}   # matches every bar
    ga_trades = pd6._score_genetic(
        np.asarray(member_bi, dtype=np.int32), rule, sl_pct, tp_pct,
        direction, len(member_bi), train_days=500, _cache={},
        _return_trades=True)

    assert isinstance(ga_trades, list) and ga_trades, "GA returned no trades"

    sim_bars = [t[0] for t in sim_trades]
    ga_bars = [t[0] for t in ga_trades]
    assert ga_bars == sim_bars, (
        f"trade-bar streams diverge: sim={len(sim_bars)} ga={len(ga_bars)} "
        f"first-diff={next((i for i, (a, b) in enumerate(zip(sim_bars, ga_bars)) if a != b), 'len')}"
    )

    sim_by_bar = {t[0]: t for t in sim_trades}
    for bi, booked, bars_held, _timeout in ga_trades:
        st = sim_by_bar[bi]
        sim_r, sim_held = st[2], st[7]
        assert bars_held == sim_held, f"bar {bi}: bars_held {bars_held} != {sim_held}"
        # sim rounds booked R to 2dp in the trade tuple
        assert booked == pytest.approx(sim_r, abs=0.006), (
            f"bar {bi}: booked R {booked} != sim {sim_r}")
        # WIN/LOSS classification must agree post-cost
        assert (booked > 0) == (st[1] == "WIN"), (
            f"bar {bi}: GA sign {booked} vs sim label {st[1]}")


def test_same_bar_sl_tp_tie_is_a_loss(monkeypatch):
    """One engineered bar spans both SL and TP — both sides must book the stop."""
    n = 64
    op = np.full(n, 100.0)
    cl = np.full(n, 100.0)
    hi = np.full(n, 100.2)
    lo = np.full(n, 99.8)
    # signal bar 5 → entry at open of bar 6; bar 6 spans both triggers
    hi[6] = 103.0
    lo[6] = 97.0
    rsi = np.full(n, 50.0)

    monkeypatch.setattr(pd6, "MAX_HOLD_BARS", 8)
    monkeypatch.setattr(pd6, "COOLDOWN_BARS", 0)
    monkeypatch.setattr(pd6, "COMMISSION_R", 0.0)
    monkeypatch.setattr(pd6, "SWAP_R_PER_BAR", 0.0)
    monkeypatch.setattr(pd6, "GENE_SCORE_BOX_ONLY", False)

    pd6._init_genetic({"rsi14": rsi}, hi, lo, cl, op, pd6.FORWARD_BARS, n, 0.0)
    ga_trades = pd6._score_genetic(
        np.asarray([5], dtype=np.int32), {"rsi14": (0.0, 100.0)},
        0.01, 0.01, "LONG", 1, train_days=10, _cache={}, _return_trades=True)

    assert len(ga_trades) == 1
    bi, booked, bars_held, timeout = ga_trades[0]
    assert bi == 5
    assert not timeout
    assert booked == pytest.approx(-1.0), "same-bar SL+TP tie must book the stop"
    assert bars_held == 1
