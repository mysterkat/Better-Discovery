"""Multi-instrument discovery + cross-instrument transfer gate.

The single strongest defence against overfitting in pattern discovery is not a
fancier in-sample statistic — it is asking whether the same rule *also* makes
money on a DIFFERENT instrument it was never fitted to.

A rule discovered on gold (XAUUSD) that is merely a curve-fit will, by
construction, evaporate when applied bar-for-bar to silver (XAGUSD): its
feature thresholds were tuned to gold's idiosyncratic noise. A rule that
encodes a *real* market mechanism (e.g. "buy the London-session mean-reversion
when RSI is washed out and the higher-TF trend is up") tends to survive the
jump, because the mechanism is not gold-specific. Gold and silver share macro
drivers (real yields, USD, risk appetite) yet have independent microstructure
and noise, so agreement across both is far likelier to be signal than luck.
This is a held-out test in the *instrument* dimension, complementary to the
train/test split discovery already does in the *time* dimension.

This module provides three pieces:

  (a) ``run_multi``            — run pattern_discovery_v6 once per symbol,
                                 reusing the FastAPI bridge's monkeypatch-globals
                                 machinery to inject per-symbol config.
  (b) ``evaluate_rule_on_symbol`` — apply ONE discovered rule (its feature-box +
                                 direction + SL/TP) to ANOTHER instrument's bars
                                 and backtest expectancy (WR / PF / trades / R).
  (c) ``cross_instrument_gate`` — keep only rules with positive expectancy on
                                 >= ``min_instruments`` symbols, attaching a
                                 ``generalization_score``.

Everything here is pure pandas / numpy. The only coupling to the discovery
engine is a lazy, ImportError-guarded import of ``pattern_discovery_v6`` (for
its feature-computation helpers and simulation constants) and of the bridge's
``run_discovery`` (for the override plumbing). Nothing under the read-only
``MONTE CARLO/`` tree is touched.

A "rule" here is the same dict shape pattern_discovery_v6 emits as
``genetic_rule``::

    {
        "genetic_rule": {col_name: (low, high), ...},  # inclusive feature box
        "direction":    "LONG" | "SHORT",
        "sl_pct":       float,   # stop distance as a fraction of entry price
        "tp_pct":       float,   # target distance as a fraction of entry price
        ...                      # any other keys are carried through untouched
    }

The backtest deliberately reproduces ``pattern_discovery_v6._bt_worker_dir``'s
semantics — next-open realistic entry, spread on both legs, pessimistic
"stop-fills-first" intrabar resolution, cooldown anchored on the prior exit,
and max-hold timeout booked as a partial R — so a rule's transfer number is
directly comparable to its native discovery metrics rather than measured on a
subtly different simulator.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Lazy engine access (ImportError-guarded)
# ─────────────────────────────────────────────────────────────────────────────
def _get_engine():
    """Import pattern_discovery_v6 lazily.

    It is a ~3k-line module that pulls heavy deps, so we only touch it when a
    caller actually needs feature computation or the simulation constants.
    Raises ImportError (re-wrapped with guidance) if it cannot be imported,
    so callers can degrade gracefully instead of crashing at import time.
    """
    try:
        import importlib

        return importlib.import_module("pattern_discovery_v6")
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "pattern_discovery_v6 is not importable; multi-instrument transfer "
            "needs it for feature computation and simulation constants. Ensure "
            "the backend/toolkit folder is on sys.path."
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# (a) Per-symbol discovery
# ─────────────────────────────────────────────────────────────────────────────
def run_multi(
    symbols: Sequence[str],
    data_folder: str,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run discovery once per instrument and collect per-symbol results.

    Each ``symbols`` entry is a *primary-TF CSV filename* living inside
    ``data_folder`` (e.g. ``"xauusd_m5.csv"``, ``"xagusd_m5.csv"``). For every
    symbol we drive ``pattern_discovery_v6.main()`` with that file pinned to
    slot 1 / ``PRIMARY_TF=1`` and signal slots cleared, so the run is a clean
    single-instrument discovery. Shared ``overrides`` (e.g. search budget,
    targets) are applied to every symbol; per-symbol file/TF keys are forced
    here and may not be overridden.

    The actual override injection is delegated to the FastAPI bridge's
    ``run_discovery`` — that is the canonical implementation of the
    monkeypatch-globals pattern (snapshot module attrs, ``setattr`` the
    overrides, write ``_app_override.json`` for spawn workers, restore in a
    ``finally``). Reusing it keeps multi-instrument runs byte-identical to
    single runs and avoids duplicating the spawn-worker override plumbing.

    Returns ``{"results": {symbol: discovery_summary | {"error": str}},
    "ok": bool, "data_folder": str, "symbols": [...]}``. One symbol failing
    does not abort the batch; its slot carries an ``error`` string instead.
    """
    overrides = dict(overrides or {})

    # File/TF layout is owned by this function — forbid callers from smuggling
    # it in via the shared overrides, which would silently break the
    # one-symbol-per-run contract.
    _reserved = {
        "DATA_FOLDER",
        "TF1_FILE", "TF2_FILE", "TF3_FILE", "TF4_FILE", "TF5_FILE",
        "PRIMARY_TF",
    }
    clash = _reserved & set(overrides)
    if clash:
        raise KeyError(
            f"these keys are managed per-symbol by run_multi and may not be "
            f"passed in overrides: {sorted(clash)}"
        )

    try:
        from app.bridge.discovery import run_discovery  # type: ignore
    except ImportError:
        try:
            # Fallback for callers whose sys.path is rooted at backend/.
            from backend.app.bridge.discovery import run_discovery  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "app.bridge.discovery.run_discovery is required to run "
                "multi-instrument discovery (it owns the monkeypatch-globals "
                "override plumbing)."
            ) from exc

    results: dict[str, Any] = {}
    for symbol in symbols:
        per_symbol = dict(overrides)
        per_symbol["DATA_FOLDER"] = str(data_folder)
        per_symbol["TF1_FILE"] = symbol
        # Clear the other slots so this is a pure single-instrument run.
        per_symbol["TF2_FILE"] = ""
        per_symbol["TF3_FILE"] = ""
        per_symbol["TF4_FILE"] = ""
        per_symbol["TF5_FILE"] = ""
        per_symbol["PRIMARY_TF"] = 1
        try:
            results[symbol] = run_discovery(per_symbol)
        except Exception as exc:  # noqa: BLE001 - isolate one symbol's failure
            results[symbol] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    ok = any(isinstance(v, dict) and v.get("ok") for v in results.values())
    return {
        "ok": ok,
        "results": results,
        "data_folder": str(data_folder),
        "symbols": list(symbols),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Feature preparation — replicate the discovery pipeline so rule columns resolve
# ─────────────────────────────────────────────────────────────────────────────
def prepare_features(
    df: pd.DataFrame,
    *,
    extra_features: bool | None = None,
    regimes: bool | None = None,
) -> pd.DataFrame:
    """Compute the same indicator/feature columns discovery fits its rules on.

    A discovered rule's box keys are feature column names (``rsi14``,
    ``body_pct``, ``stoch_k``, ``htf_div``, ``regime`` …). To evaluate the rule
    on another instrument we must regenerate those exact columns from raw OHLC,
    using pattern_discovery_v6's own helpers so the maths matches bar-for-bar.

    Pipeline order mirrors ``load_raw_data`` + the post-split stage in
    ``main()``: ``_add_indicators`` → ``add_extended_features`` →
    ``add_v5_features`` (+ optional ``detect_regimes``). Signal-TF columns
    (``tfN_*``) are intentionally NOT produced — single-instrument transfer
    evaluates the primary stream only; any rule column we can't supply is
    simply skipped by the matcher (documented in ``evaluate_rule_on_symbol``).

    ``df`` must have lower-case ``open/high/low/close`` columns (``volume``
    optional); pass the frame straight from ``_load_raw``. Toggles default to
    the engine's ``USE_EXTRA_FEATURES`` / ``REGIME_MODE`` constants so output
    matches whatever config discovery ran under.
    """
    eng = _get_engine()
    if extra_features is None:
        extra_features = bool(getattr(eng, "USE_EXTRA_FEATURES", True))
    if regimes is None:
        regimes = bool(getattr(eng, "REGIME_MODE", False))

    out = eng._add_indicators(df)
    if extra_features:
        out = eng.add_extended_features(out)
        out = eng.add_v5_features(out)
    if regimes:
        out = eng.detect_regimes(out)
    return out.fillna(0)


def load_symbol_features(path: str, **kwargs: Any) -> pd.DataFrame:
    """Convenience: read a primary-TF CSV and compute its feature columns.

    Thin wrapper over ``pattern_discovery_v6._load_raw`` + ``prepare_features``.
    Useful when building the ``dfs_by_symbol`` map for ``cross_instrument_gate``
    from filenames rather than pre-loaded frames.
    """
    eng = _get_engine()
    raw = eng._load_raw(path)
    return prepare_features(raw, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# (b) Backtest one rule on one instrument
# ─────────────────────────────────────────────────────────────────────────────
def _rule_box_mask(df: pd.DataFrame, rule_box: Mapping[str, Any]) -> tuple[np.ndarray, list[str]]:
    """Boolean mask over ``df`` rows where every available box condition holds.

    Mirrors ``pattern_discovery_v6._rule_match_mask``: each condition is an
    inclusive band ``low <= value <= high``. Columns present in the box but
    absent from ``df`` (e.g. a signal-TF feature we didn't recreate) are
    skipped and reported in the returned ``missing`` list so the caller can
    judge how faithfully the box transferred.
    """
    n = len(df)
    mask = np.ones(n, dtype=bool)
    missing: list[str] = []
    for col, band in rule_box.items():
        if col not in df.columns:
            missing.append(str(col))
            continue
        lo, hi = float(band[0]), float(band[1])
        vals = df[col].to_numpy()
        mask &= (vals >= lo) & (vals <= hi)
    return mask, missing


def evaluate_rule_on_symbol(
    rule: Mapping[str, Any],
    df_symbol: pd.DataFrame,
    *,
    prepared: bool = False,
    spread_pts: float | None = None,
    realistic_entry: bool | None = None,
    max_hold_bars: int | None = None,
    cooldown_bars: int | None = None,
    commission_r: float | None = None,
    swap_r_per_bar: float | None = None,
) -> dict[str, Any]:
    """Apply ``rule``'s feature-box + direction + SL/TP to ``df_symbol`` and
    backtest expectancy.

    This is the cross-instrument workhorse: ``rule`` was discovered on symbol A;
    ``df_symbol`` is symbol B's bars. We locate every bar on B where A's feature
    box fires, open a trade in A's direction with A's SL/TP fractions, and
    resolve each trade with the same fill model as native discovery.

    Backtest semantics (copied from ``_bt_worker_dir`` for parity):
      * Entry on the NEXT bar's open when ``realistic_entry`` (default from the
        engine's ``REALISTIC_ENTRY``), else this bar's close.
      * Spread charged on entry and on the exit leg.
      * SL/TP are fractions of the spread-adjusted entry; ``risk = |entry-SL|``,
        every outcome booked as an R-multiple so WIN and LOSS share a unit.
      * Pessimistic intrabar resolution: if a bar spans both SL and TP, assume
        the STOP filled first (OHLC can't tell tick order; favourable
        assumptions inflate WR).
      * Max-hold timeout exits at close, booked as a signed partial R.
      * Per-trade commission (once) + per-bar swap subtracted in R.
      * Serial execution: no overlapping positions; cooldown counted from the
        prior trade's EXIT bar.

    Returns a metrics dict::

        {
          "trades": int, "wins": int, "losses": int,
          "win_rate": float (%),        # win_rate=0.0 when trades==0
          "profit_factor": float,       # inf if no losing R; 0.0 if no trades
          "expectancy_r": float,        # mean R per trade (THE transfer signal)
          "total_r": float, "avg_win_r": float, "avg_loss_r": float,
          "missing_cols": [str],        # box columns absent from df_symbol
          "matched_bars": int,          # bars where the box fired
          "direction": "LONG"|"SHORT",
        }

    ``expectancy_r > 0`` is the bar a rule must clear to "hold" on this
    instrument. ``prepared=True`` skips feature recomputation when ``df_symbol``
    already has the indicator columns (e.g. came from ``prepare_features``).
    """
    eng = _get_engine()

    direction = str(rule.get("direction", "LONG")).upper()
    rule_box = rule.get("genetic_rule") or rule.get("rule") or {}
    sl_pct = float(rule.get("sl_pct", 0.0) or 0.0)
    tp_pct = float(rule.get("tp_pct", 0.0) or 0.0)

    # Simulation constants: caller override → engine default.
    spread = float(spread_pts if spread_pts is not None else getattr(eng, "SPREAD_PTS", 0.0))
    realistic = bool(
        realistic_entry if realistic_entry is not None else getattr(eng, "REALISTIC_ENTRY", True)
    )
    max_hold = int(max_hold_bars if max_hold_bars is not None else getattr(eng, "MAX_HOLD_BARS", 32))
    cooldown = int(cooldown_bars if cooldown_bars is not None else getattr(eng, "COOLDOWN_BARS", 0))
    commission_r = float(
        commission_r if commission_r is not None else getattr(eng, "COMMISSION_R", 0.0)
    )
    swap_r = float(
        swap_r_per_bar if swap_r_per_bar is not None else getattr(eng, "SWAP_R_PER_BAR", 0.0)
    )

    empty = {
        "trades": 0, "wins": 0, "losses": 0,
        "win_rate": 0.0, "profit_factor": 0.0, "expectancy_r": 0.0,
        "total_r": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0,
        "missing_cols": [], "matched_bars": 0, "direction": direction,
    }
    if not rule_box or sl_pct <= 0.0 or tp_pct <= 0.0 or len(df_symbol) < 2:
        return empty

    df = df_symbol if prepared else prepare_features(df_symbol)

    mask, missing = _rule_box_mask(df, rule_box)
    # If NONE of the box columns exist on this symbol the "match" would be every
    # bar — meaningless. Require at least one real condition to have applied.
    if len(missing) == len(rule_box):
        out = dict(empty)
        out["missing_cols"] = missing
        return out

    member_bi = np.flatnonzero(mask)
    out = dict(empty)
    out["missing_cols"] = missing
    out["matched_bars"] = int(member_bi.size)
    if member_bi.size == 0:
        return out

    hi = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    cl = df["close"].to_numpy(dtype=float)
    op = df["open"].to_numpy(dtype=float)
    n = len(df)
    long = direction == "LONG"

    trades_r: list[float] = []
    wins = losses = 0
    last_exit = -cooldown - 1

    for bi in member_bi:
        if bi + 1 >= n:
            continue
        if bi - last_exit < cooldown:
            continue
        entry = op[bi + 1] if realistic else cl[bi]
        if entry == 0:
            continue
        entry_ws = entry + spread if long else entry - spread
        sl_v = entry_ws * (1 - sl_pct) if long else entry_ws * (1 + sl_pct)
        tp_v = entry_ws * (1 + tp_pct) if long else entry_ws * (1 - tp_pct)
        tp_v_eff = tp_v - spread if long else tp_v + spread
        sl_v_eff = sl_v - spread if long else sl_v + spread
        risk = abs(entry_ws - sl_v)
        reward = abs(tp_v - entry_ws)
        if risk == 0:
            continue
        win_r = reward / risk
        loss_r = -1.0
        ht = hs = False
        bars_held = 0
        for j in range(bi + 1, min(bi + max_hold + 1, n)):
            h_ = hi[j]; lo_ = lo[j]; bars_held += 1
            if long:
                if lo_ <= sl_v_eff:
                    hs = True; break
                if h_ >= tp_v_eff:
                    ht = True; break
            else:
                if h_ >= sl_v_eff:
                    hs = True; break
                if lo_ <= tp_v_eff:
                    ht = True; break
        if not ht and not hs:
            exit_p = cl[min(bi + max_hold, n - 1)]
            exit_p_eff = exit_p - spread if long else exit_p + spread
            pnl = exit_p_eff - entry_ws if long else entry_ws - exit_p_eff
            if pnl > 0:
                ht = True
                win_r = pnl / risk
            else:
                hs = True
                loss_r = pnl / risk

        cost_r = commission_r + swap_r * bars_held
        win_r -= cost_r
        loss_r -= cost_r
        last_exit = bi + bars_held
        if ht:
            wins += 1
            trades_r.append(win_r)
        else:
            losses += 1
            trades_r.append(loss_r)

    total = wins + losses
    if total == 0:
        return out

    arr = np.asarray(trades_r, dtype=float)
    pos = arr[arr > 0]
    neg = arr[arr < 0]
    gross_win = float(pos.sum()) if pos.size else 0.0
    gross_loss = float(-neg.sum()) if neg.size else 0.0
    out.update(
        trades=int(total),
        wins=int(wins),
        losses=int(losses),
        win_rate=round(wins / total * 100.0, 2),
        profit_factor=(round(gross_win / gross_loss, 4) if gross_loss > 0 else float("inf")),
        expectancy_r=round(float(arr.mean()), 4),
        total_r=round(float(arr.sum()), 4),
        avg_win_r=round(float(pos.mean()), 4) if pos.size else 0.0,
        avg_loss_r=round(float(neg.mean()), 4) if neg.size else 0.0,
    )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# (c) Cross-instrument transfer gate
# ─────────────────────────────────────────────────────────────────────────────
def _generalization_score(per_symbol: Mapping[str, dict[str, Any]], min_trades: int) -> float:
    """Score how robustly a rule transfers across instruments (0..~1+).

    A rule that holds on gold AND silver is far likelier to be real than one
    that only fires on its home instrument — so the score rewards BREADTH
    (how many instruments stay positive) and PENALISES inconsistency (a rule
    that is wildly profitable on one symbol and barely positive on another is
    less trustworthy than one steadily positive on both).

    score = passing_fraction × mean_positive_expectancy × consistency

      * passing_fraction  — share of evaluated symbols with expectancy_r > 0 and
                            >= ``min_trades`` trades. Pure breadth term.
      * mean_positive_exp — average expectancy_r over the passing symbols
                            (clipped at 1.0 R so one outlier can't dominate).
      * consistency       — 1 / (1 + CoV) of the passing expectancies, where CoV
                            is the coefficient of variation. Even spread → ~1;
                            lopsided → toward 0. Single passing symbol → 1.0.
    """
    exps = [
        m["expectancy_r"]
        for m in per_symbol.values()
        if m.get("trades", 0) >= min_trades and m.get("expectancy_r", 0.0) > 0.0
    ]
    n_eval = len(per_symbol)
    if not exps or n_eval == 0:
        return 0.0
    passing_fraction = len(exps) / n_eval
    arr = np.asarray(exps, dtype=float)
    mean_pos = float(np.clip(arr.mean(), 0.0, 1.0))
    if arr.size > 1 and arr.mean() > 0:
        cov = float(arr.std(ddof=0) / arr.mean())
        consistency = 1.0 / (1.0 + cov)
    else:
        consistency = 1.0
    return round(passing_fraction * mean_pos * consistency, 4)


def cross_instrument_gate(
    rules: Iterable[Mapping[str, Any]],
    dfs_by_symbol: Mapping[str, pd.DataFrame],
    min_instruments: int = 2,
    *,
    min_trades: int = 5,
    prepared: bool = False,
    eval_kwargs: Mapping[str, Any] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Keep only rules that TRANSFER — positive expectancy on enough instruments.

    For each rule we backtest its feature-box + direction + SL/TP against EVERY
    symbol in ``dfs_by_symbol`` (including, harmlessly, its home symbol if
    present) via :func:`evaluate_rule_on_symbol`. A symbol "passes" when it
    produces ``>= min_trades`` trades and ``expectancy_r > 0``. A rule survives
    the gate when at least ``min_instruments`` symbols pass.

    This is the strongest anti-overfit filter in the toolkit. A rule that looks
    superb on gold but cannot clear breakeven on silver was almost certainly
    fitting gold's noise; a rule that stays positive on both is far likelier to
    encode a genuine, transferable market mechanism. Cross-instrument agreement
    is a held-out test in the instrument dimension and is much harder to game
    than any in-sample statistic.

    Parameters
    ----------
    rules
        Iterable of rule dicts (``genetic_rule`` / ``direction`` / ``sl_pct`` /
        ``tp_pct``); extra keys such as ``pattern_id`` are carried through.
    dfs_by_symbol
        ``{symbol: bars}``. If ``prepared`` is False (default) each frame is run
        through :func:`prepare_features` once and cached for all rules.
    min_instruments
        Minimum number of passing symbols for a rule to survive.
    min_trades
        Per-symbol trade floor for that symbol to count as a pass (a handful of
        trades is too noisy to call "transfer").
    eval_kwargs
        Extra keyword args forwarded to :func:`evaluate_rule_on_symbol`
        (e.g. ``spread_pts``), applied uniformly to every evaluation.
    progress
        Optional ``callback(done, total)`` invoked after each rule.

    Returns
    -------
    list[dict]
        Surviving rules, each a shallow copy of the input rule plus::

            "per_symbol":           {symbol: metrics_dict},
            "n_instruments_passed": int,
            "passed_symbols":       [symbol, ...],
            "generalization_score": float,   # see _generalization_score

        sorted by ``generalization_score`` descending. Rules failing the gate
        are omitted (inspect ``per_symbol`` upstream if you need the rejects).
    """
    eval_kwargs = dict(eval_kwargs or {})

    # Prepare features once per symbol (shared across all rules).
    if prepared:
        prepped: dict[str, pd.DataFrame] = dict(dfs_by_symbol)
    else:
        prepped = {sym: prepare_features(df) for sym, df in dfs_by_symbol.items()}

    rules_list = list(rules)
    total = len(rules_list)
    survivors: list[dict[str, Any]] = []

    for idx, rule in enumerate(rules_list, start=1):
        per_symbol: dict[str, dict[str, Any]] = {}
        for sym, df in prepped.items():
            per_symbol[sym] = evaluate_rule_on_symbol(
                rule, df, prepared=True, **eval_kwargs
            )

        passed = [
            sym
            for sym, m in per_symbol.items()
            if m.get("trades", 0) >= min_trades and m.get("expectancy_r", 0.0) > 0.0
        ]
        if len(passed) >= min_instruments:
            enriched = dict(rule)
            enriched["per_symbol"] = per_symbol
            enriched["n_instruments_passed"] = len(passed)
            enriched["passed_symbols"] = passed
            enriched["generalization_score"] = _generalization_score(
                per_symbol, min_trades
            )
            survivors.append(enriched)

        if progress is not None:
            progress(idx, total)

    survivors.sort(key=lambda r: r["generalization_score"], reverse=True)
    return survivors


# ─────────────────────────────────────────────────────────────────────────────
# (d) End-to-end orchestrator: per-symbol discovery -> pool rules -> gate
# ─────────────────────────────────────────────────────────────────────────────
def discover_multi(
    symbols: Sequence[str],
    data_folder: str,
    *,
    overrides: Mapping[str, Any] | None = None,
    min_instruments: int = 2,
    min_trades: int = 5,
    top_n_per_symbol: int | None = None,
    eval_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Full multi-instrument run: discover per symbol, pool every rule those runs
    produced, then keep only the ones that TRANSFER across instruments.

    ``symbols`` are primary-TF CSV filenames inside ``data_folder`` (e.g.
    ``["xauusd_m15.csv", "xagusd_m15.csv", "dxy_m15.csv"]``). ``overrides`` are
    engine config overrides applied to every symbol's discovery (file/TF keys are
    managed internally by run_multi and must not be passed here).

    Returns ``{ok, symbols, per_symbol_patterns:{sym:n}, n_rules, n_survivors,
    survivors:[...]}`` — survivors ranked by ``generalization_score``.

    IMPORTANT: this GATES the rules discovery already produced per symbol. If a
    symbol yields 0 patterns (strict FTMO filters / no edge), there is nothing to
    transfer-test. Feed candidates through by enabling the research profile and/or
    loosening per-symbol filters via ``overrides``, e.g.::

        {"USE_BETA_NEUTRAL_LABELS": True, "USE_SOFT_FILTER": True, "FILTER_EDGE_K": 0.30}

    Cross-instrument agreement is itself a strong filter, so looser per-symbol
    gates are fine here.
    """
    import os

    overrides = dict(overrides or {})
    multi = run_multi(symbols, data_folder, overrides)

    pooled: list[dict[str, Any]] = []
    per_symbol_counts: dict[str, int] = {}
    for sym in symbols:
        res = multi["results"].get(sym, {})
        pats = res.get("patterns", []) if isinstance(res, dict) else []
        per_symbol_counts[sym] = len(pats)
        kept = 0
        for i, p in enumerate(pats):
            box = p.get("genetic_rule") or {}
            if not box or not p.get("sl_pct") or not p.get("tp_pct"):
                continue
            pooled.append({
                "genetic_rule": box,
                "direction": str(p.get("direction", "LONG")).upper(),
                "sl_pct": float(p["sl_pct"]),
                "tp_pct": float(p["tp_pct"]),
                "home_symbol": sym,
                "pattern_id": p.get("pattern_id", f"{sym}#{i}"),
            })
            kept += 1
            if top_n_per_symbol and kept >= top_n_per_symbol:
                break

    if not pooled:
        return {
            "ok": False,
            "reason": "no rules produced by per-symbol discovery — nothing to "
                      "transfer-test (enable research profile / loosen filters via overrides)",
            "symbols": list(symbols),
            "per_symbol_patterns": per_symbol_counts,
            "n_rules": 0, "n_survivors": 0, "survivors": [],
        }

    dfs: dict[str, pd.DataFrame] = {}
    load_errors: dict[str, str] = {}
    for sym in symbols:
        try:
            dfs[sym] = load_symbol_features(os.path.join(data_folder, sym))
        except Exception as exc:  # noqa: BLE001 - isolate one symbol's load failure
            load_errors[sym] = f"{type(exc).__name__}: {exc}"

    if len(dfs) < min_instruments:
        return {
            "ok": False,
            "reason": f"only {len(dfs)} symbol frame(s) loaded; need >= {min_instruments}",
            "symbols": list(symbols),
            "per_symbol_patterns": per_symbol_counts,
            "load_errors": load_errors,
            "n_rules": len(pooled), "n_survivors": 0, "survivors": [],
        }

    survivors = cross_instrument_gate(
        pooled, dfs,
        min_instruments=min_instruments,
        min_trades=min_trades,
        prepared=True,
        eval_kwargs=eval_kwargs,
    )
    return {
        "ok": True,
        "symbols": list(symbols),
        "per_symbol_patterns": per_symbol_counts,
        "load_errors": load_errors,
        "n_rules": len(pooled),
        "n_survivors": len(survivors),
        "survivors": survivors,
    }


def _main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    """CLI: run multi-instrument discovery + transfer gate end-to-end.

    Example::

        python multi_instrument.py --folder /path/to/hist_data \\
            --symbols xauusd_m15.csv xagusd_m15.csv dxy_m15.csv \\
            --min-instruments 2 \\
            --override USE_BETA_NEUTRAL_LABELS=true --override FILTER_EDGE_K=0.30 \\
            --out transfer_report.json
    """
    import argparse
    import json
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(
        description="Multi-instrument discovery + cross-instrument transfer gate")
    ap.add_argument("--folder", required=True,
                    help="folder containing the per-symbol primary-TF CSVs")
    ap.add_argument("--symbols", nargs="+", required=True,
                    help="primary-TF CSV filenames, e.g. xauusd_m15.csv xagusd_m15.csv")
    ap.add_argument("--min-instruments", type=int, default=2)
    ap.add_argument("--min-trades", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=None,
                    help="cap rules per symbol fed to the gate (already discovery-ranked)")
    ap.add_argument("--override", action="append", default=[],
                    help="KEY=VALUE engine override, JSON-typed value (repeatable)")
    ap.add_argument("--out", default=None, help="write the full JSON report here")
    args = ap.parse_args(argv)

    ov: dict[str, Any] = {}
    for kv in args.override:
        k, _, v = kv.partition("=")
        try:
            ov[k.strip()] = json.loads(v)
        except Exception:
            ov[k.strip()] = v

    res = discover_multi(
        args.symbols, args.folder, overrides=ov,
        min_instruments=args.min_instruments, min_trades=args.min_trades,
        top_n_per_symbol=args.top_n,
    )
    print(json.dumps({k: v for k, v in res.items() if k != "survivors"},
                     indent=2, default=str))
    print(f"\n=== {res.get('n_survivors', 0)} rule(s) transferred across "
          f">= {args.min_instruments} instruments ===")
    for s in res.get("survivors", [])[:20]:
        print(f"  [{s.get('home_symbol', '?')}] {s.get('pattern_id', '?')}  "
              f"score={s.get('generalization_score')}  passes={s.get('passed_symbols')}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, default=str)
        print(f"\nReport -> {args.out}")
    return res


if __name__ == "__main__":
    _main()
