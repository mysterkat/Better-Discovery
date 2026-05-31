"""
Anti-Overfit / Validation Extensions
=====================================
Compatible with Python 3.14 — pure numpy / pandas.

The discovery engine runs a multi-seed x multi-cluster search. That is a large
multiple-testing experiment: with enough trials, some configuration *will* look
profitable in-sample purely by chance. These helpers quantify and defend against
that selection bias.

Implemented (with the canonical reference for each):

  walk_forward_splits
      Rolling / anchored out-of-sample partitioning. Standard time-series
      cross-validation (no shuffling), the baseline against which the leakage-
      aware variants below are compared.

  purged_kfold
      K-fold with PURGING + EMBARGO. From Marcos Lopez de Prado,
      "Advances in Financial Machine Learning" (2018), Ch. 7
      ("Cross-Validation in Finance"), Snippets 7.1-7.4. Removes train
      observations whose labels overlap the test window (purging) and drops a
      band of bars immediately after the test window (embargo) to stop
      serial-correlation / overlapping-label leakage.

  deflated_sharpe_ratio
      Deflated Sharpe Ratio (DSR). Bailey & Lopez de Prado,
      "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
      Overfitting and Non-Normality", Journal of Portfolio Management (2014).
      Adjusts an observed Sharpe for the number of trials, skew and kurtosis.

  probability_of_backtest_overfitting
      Probability of Backtest Overfitting (PBO) via Combinatorially Symmetric
      Cross-Validation (CSCV). Bailey, Borwein, Lopez de Prado & Zhu,
      "The Probability of Backtest Overfitting", Journal of Computational
      Finance (2016). Estimates P(the in-sample best strategy underperforms
      the median out-of-sample).

All functions are deterministic, side-effect free, and operate on plain
ndarrays / DataFrames so they can be unit-tested in isolation.
"""

from __future__ import annotations

from itertools import combinations
from math import comb, erf, sqrt
from typing import List, Tuple

import numpy as np
import pandas as pd

__all__ = [
    "walk_forward_splits",
    "purged_kfold",
    "deflated_sharpe_ratio",
    "probability_of_backtest_overfitting",
]


# --------------------------------------------------------------------------- #
# Pure-numpy statistics helpers (no scipy dependency)
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via the error function (math.erf)."""
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Standard-normal inverse CDF (quantile).

    Acklam's rational approximation; |error| < ~1.15e-9 over (0, 1).
    """
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in the open interval (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = sqrt(-2.0 * np.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > p_high:
        q = sqrt(-2.0 * np.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def _sample_skew(x: np.ndarray) -> float:
    """Bias-corrected (G1) sample skewness, matching scipy ``bias=False``."""
    n = x.size
    if n < 3:
        return 0.0
    m = x.mean()
    d = x - m
    m2 = np.mean(d**2)
    m3 = np.mean(d**3)
    if m2 == 0:
        return 0.0
    g1 = m3 / m2**1.5
    return float(np.sqrt(n * (n - 1)) / (n - 2) * g1)


def _sample_kurtosis(x: np.ndarray) -> float:
    """Bias-corrected excess (Fisher) kurtosis, matching scipy ``bias=False``."""
    n = x.size
    if n < 4:
        return 0.0
    m = x.mean()
    d = x - m
    m2 = np.mean(d**2)
    m4 = np.mean(d**4)
    if m2 == 0:
        return 0.0
    g2 = m4 / m2**2 - 3.0
    return float(((n - 1) / ((n - 2) * (n - 3))) * ((n + 1) * g2 + 6.0))


def _rankdata_average(x: np.ndarray) -> np.ndarray:
    """Rank an array, averaging ties (equivalent to scipy ``method='average'``)."""
    n = x.size
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    # Average ranks within tie groups.
    sx = x[order]
    i = 0
    while i < n:
        j = i + 1
        while j < n and sx[j] == sx[i]:
            j += 1
        if j - i > 1:
            avg = (i + 1 + j) / 2.0  # mean of ranks (i+1 .. j)
            ranks[order[i:j]] = avg
        i = j
    return ranks


# --------------------------------------------------------------------------- #
# (a) Walk-forward splits
# --------------------------------------------------------------------------- #
def walk_forward_splits(
    n_bars: int,
    n_folds: int,
    train_frac: float = 0.6,
    embargo: int = 0,
    anchored: bool = False,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Rolling or anchored walk-forward train/test index windows.

    Time-ordered out-of-sample validation: the data are sliced into ``n_folds``
    contiguous test windows that march forward in time, each preceded by a
    training window. No shuffling is ever performed (that would leak the
    future into the past).

    Parameters
    ----------
    n_bars : int
        Total number of observations (rows / bars), indexed ``0 .. n_bars-1``.
    n_folds : int
        Number of walk-forward steps (test windows).
    train_frac : float, default 0.6
        Fraction of each rolling block used for training; the remaining
        ``1 - train_frac`` is the test window. Ignored when ``anchored=True``
        (training then always starts at bar 0).
    embargo : int, default 0
        Number of bars dropped between the end of the train window and the
        start of the test window, to neutralise serial-correlation leakage
        across the boundary.
    anchored : bool, default False
        If True, every training window is anchored at bar 0 and grows
        ("expanding window"). If False, the training window has fixed length
        and slides forward ("rolling window").

    Returns
    -------
    list of (train_idx, test_idx)
        Each element is a tuple of integer ``np.ndarray`` positional indices.
        Folds whose train or test side would be empty are skipped.

    Raises
    ------
    ValueError
        If arguments are out of range.
    """
    if n_bars <= 0:
        raise ValueError("n_bars must be positive")
    if n_folds <= 0:
        raise ValueError("n_folds must be positive")
    if not 0.0 < train_frac < 1.0:
        raise ValueError("train_frac must be in (0, 1)")
    if embargo < 0:
        raise ValueError("embargo must be non-negative")

    # One test window per fold, laid end-to-end across the series.
    test_size = n_bars // n_folds
    if test_size < 1:
        raise ValueError("n_folds too large for n_bars (empty test windows)")

    splits: List[Tuple[np.ndarray, np.ndarray]] = []
    all_idx = np.arange(n_bars)

    for k in range(n_folds):
        test_start = k * test_size
        # Absorb the remainder into the final fold so no bars are dropped.
        test_end = n_bars if k == n_folds - 1 else (k + 1) * test_size

        if anchored:
            train_start = 0
            train_end = test_start - embargo
        else:
            # Rolling block: [train | embargo | test] of proportional sizes.
            block = test_end - test_start
            # Derive a train length from the requested ratio relative to test.
            train_len = int(round(block * (train_frac / (1.0 - train_frac))))
            train_end = test_start - embargo
            train_start = max(0, train_end - train_len)

        if train_end <= train_start:
            # Not enough history yet (typical for the very first fold) — skip.
            continue

        train_idx = all_idx[train_start:train_end]
        test_idx = all_idx[test_start:test_end]
        if train_idx.size == 0 or test_idx.size == 0:
            continue
        splits.append((train_idx, test_idx))

    return splits


# --------------------------------------------------------------------------- #
# (b) Purged K-Fold with embargo
# --------------------------------------------------------------------------- #
def purged_kfold(
    n_bars: int,
    n_folds: int,
    embargo: int = 0,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """K-fold cross-validation with purging and embargo.

    Implements the leakage-controlled scheme from Lopez de Prado, "Advances in
    Financial Machine Learning" (2018), Ch. 7, Snippets 7.1-7.4. Two corrections
    are applied to ordinary K-fold:

    * **Purging** — training observations that fall inside the test window are
      removed. (Here labels are assumed to be associated with a single bar; if
      a strategy's labels span multiple bars, widen the test window before
      calling, or purge the overlap externally.)
    * **Embargo** — an additional ``embargo`` bars immediately *after* each test
      window are removed from the training set, because their features may be
      serially correlated with the (just-seen) test labels.

    Parameters
    ----------
    n_bars : int
        Total number of observations, indexed ``0 .. n_bars-1``.
    n_folds : int
        Number of folds.
    embargo : int, default 0
        Number of post-test bars to exclude from training. Lopez de Prado
        suggests ~1% of ``n_bars`` as a starting point.

    Returns
    -------
    list of (train_idx, test_idx)
        ``n_folds`` tuples of integer ``np.ndarray`` positional indices. The
        union of all test windows is the full series; each train set is the
        complement minus the embargo band.

    Raises
    ------
    ValueError
        If arguments are out of range.
    """
    if n_bars <= 0:
        raise ValueError("n_bars must be positive")
    if n_folds <= 1:
        raise ValueError("n_folds must be >= 2")
    if embargo < 0:
        raise ValueError("embargo must be non-negative")
    if n_folds > n_bars:
        raise ValueError("n_folds cannot exceed n_bars")

    indices = np.arange(n_bars)
    # Contiguous test windows (np.array_split tolerates uneven division).
    fold_bounds = np.array_split(indices, n_folds)

    splits: List[Tuple[np.ndarray, np.ndarray]] = []
    for test_idx in fold_bounds:
        test_start = int(test_idx[0])
        test_end = int(test_idx[-1])  # inclusive

        mask = np.ones(n_bars, dtype=bool)
        # Purge the test window itself.
        mask[test_start : test_end + 1] = False
        # Embargo: drop the band immediately following the test window.
        if embargo > 0:
            emb_end = min(n_bars, test_end + 1 + embargo)
            mask[test_end + 1 : emb_end] = False

        train_idx = indices[mask]
        splits.append((train_idx, np.asarray(test_idx)))

    return splits


# --------------------------------------------------------------------------- #
# (c) Deflated Sharpe Ratio
# --------------------------------------------------------------------------- #
def deflated_sharpe_ratio(
    returns: np.ndarray | pd.Series,
    n_trials: int,
    benchmark_sr: float | None = None,
) -> dict:
    """Deflated Sharpe Ratio (DSR) and its probabilistic Sharpe component.

    From Bailey & Lopez de Prado, "The Deflated Sharpe Ratio" (J. Portfolio
    Management, 2014). The DSR is the probability that the *true* Sharpe is
    positive, after deflating the observed Sharpe for (i) the number of
    independent trials run during the search, and (ii) the non-normality
    (skew / kurtosis) of the return stream.

    Steps:
      1. Estimate the observed (per-period) Sharpe ``SR_hat`` and the higher
         moments of ``returns``.
      2. Compute the expected maximum Sharpe under the null across ``n_trials``
         independent strategies of zero true Sharpe (``SR0``); this becomes the
         benchmark the observed Sharpe must beat.
      3. Plug ``SR0`` into the Probabilistic Sharpe Ratio (PSR) formula to get
         the deflated probability.

    Parameters
    ----------
    returns : np.ndarray or pd.Series
        Per-period strategy returns (e.g. per-trade or per-bar). Length ``T``.
    n_trials : int
        Number of strategy configurations evaluated during the search
        (multi-seed x multi-cluster count). Larger ``n_trials`` -> higher bar.
    benchmark_sr : float, optional
        Override the deflation benchmark ``SR0`` directly (per-period units).
        If None (default), ``SR0`` is derived from ``n_trials`` via the
        expected-maximum formula.

    Returns
    -------
    dict
        ``observed_sr``    : float, per-period observed Sharpe.
        ``benchmark_sr``   : float, ``SR0`` used for deflation (per-period).
        ``psr``            : float, Probabilistic Sharpe Ratio vs SR0 == DSR.
        ``deflated_sr``    : float, alias of ``psr`` (the headline number in
                             [0, 1]); > 0.95 is the usual "passes" threshold.
        ``skew``           : float, sample skewness of returns.
        ``kurtosis``       : float, sample kurtosis (Fisher; normal == 0).
        ``n_obs``          : int, number of observations ``T``.

    Raises
    ------
    ValueError
        If fewer than 2 observations, zero variance, or n_trials < 1.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    T = r.size
    if T < 2:
        raise ValueError("returns must contain at least 2 finite observations")
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")

    sd = r.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        raise ValueError("returns have zero / undefined variance")

    sr_hat = r.mean() / sd                       # observed per-period Sharpe
    skew = _sample_skew(r)
    # Fisher kurtosis (excess); add 3 for the non-normal PSR denominator.
    kurt = _sample_kurtosis(r)

    # --- benchmark SR0: expected max of n_trials N(0,1) Sharpes -------------- #
    if benchmark_sr is None:
        # Variance of the cross-trial Sharpe estimates is unknown a-priori;
        # the canonical DSR uses the dispersion of the trials. With only the
        # count available we fall back to the standard error of a zero-Sharpe
        # estimate, sqrt(1/T) per period, as the cross-sectional sigma scale.
        sr_trials_sigma = np.sqrt(1.0 / T)
        euler_mascheroni = 0.5772156649015329
        e_max_z = _expected_max_gaussian(n_trials, euler_mascheroni)
        sr0 = sr_trials_sigma * e_max_z
    else:
        sr0 = float(benchmark_sr)

    # --- Probabilistic Sharpe Ratio vs SR0 (== DSR) -------------------------- #
    # PSR = Phi( (SR_hat - SR0) * sqrt(T-1) /
    #            sqrt(1 - skew*SR_hat + (kurt-1)/4 * SR_hat^2) )
    denom = 1.0 - skew * sr_hat + ((kurt + 3.0 - 1.0) / 4.0) * sr_hat**2
    denom = max(denom, 1e-12)  # guard against pathological moments
    z = (sr_hat - sr0) * np.sqrt(T - 1) / np.sqrt(denom)
    psr = _norm_cdf(float(z))

    return {
        "observed_sr": float(sr_hat),
        "benchmark_sr": float(sr0),
        "psr": psr,
        "deflated_sr": psr,
        "skew": skew,
        "kurtosis": kurt,
        "n_obs": int(T),
    }


def _expected_max_gaussian(n_trials: int, gamma: float) -> float:
    """Expected maximum of ``n_trials`` i.i.d. standard normals.

    Uses the standard extreme-value approximation employed by Bailey &
    Lopez de Prado:

        E[max] ~ (1 - gamma) * Phi^-1(1 - 1/N)
                 +     gamma  * Phi^-1(1 - 1/(N*e))

    with ``gamma`` the Euler-Mascheroni constant. Returns 0 for ``n_trials == 1``.
    """
    if n_trials <= 1:
        return 0.0
    return (1.0 - gamma) * _norm_ppf(1.0 - 1.0 / n_trials) + gamma * _norm_ppf(
        1.0 - 1.0 / (n_trials * np.e)
    )


# --------------------------------------------------------------------------- #
# (d) Probability of Backtest Overfitting (PBO via CSCV)
# --------------------------------------------------------------------------- #
def probability_of_backtest_overfitting(
    is_perf_matrix: np.ndarray | pd.DataFrame,
    n_partitions: int = 16,
) -> dict:
    """Probability of Backtest Overfitting (PBO) via CSCV (simplified).

    Implements Combinatorially Symmetric Cross-Validation from Bailey,
    Borwein, Lopez de Prado & Zhu, "The Probability of Backtest Overfitting"
    (J. Computational Finance, 2016).

    Procedure:
      1. Take a ``T x N`` matrix of per-period performance (T time slices,
         N strategy configurations) — typically per-fold or per-bar returns
         for every configuration explored by the search.
      2. Split the T rows into ``S`` disjoint, contiguous sub-matrices.
      3. For every way of choosing S/2 of those sub-matrices as the in-sample
         (IS) set (the remainder being out-of-sample, OS):
             - rank configs by an IS performance statistic (mean here),
             - pick the IS-best config ``n*``,
             - find its OS rank, map to relative rank ``w in (0, 1]``,
             - logit ``lambda = ln(w / (1 - w))``.
      4. PBO = fraction of combinations whose IS-best config lands in the
         *bottom half* OS (``lambda <= 0``) — i.e. how often the apparent
         winner is, out-of-sample, no better than a coin flip.

    A high PBO (> ~0.5) means the selection process is overfitting: the
    in-sample champion routinely degrades to mediocre out-of-sample.

    Parameters
    ----------
    is_perf_matrix : np.ndarray or pd.DataFrame, shape (T, N)
        Performance series per configuration. Rows = time slices, columns =
        configurations. Must have N >= 2 columns and enough rows to form
        ``n_partitions`` groups.
    n_partitions : int, default 16
        Number ``S`` of CSCV sub-matrices (must be even). The number of
        IS/OS combinations evaluated is ``C(S, S/2)``.

    Returns
    -------
    dict
        ``pbo``           : float in [0, 1], the overfitting probability.
        ``logits``        : np.ndarray of per-combination logit values.
        ``n_combinations``: int, number of IS/OS splits evaluated.
        ``n_partitions``  : int, ``S`` actually used.

    Raises
    ------
    ValueError
        If the matrix is too small or ``n_partitions`` is not a usable even
        number.
    """
    M = np.asarray(
        is_perf_matrix.values if isinstance(is_perf_matrix, pd.DataFrame)
        else is_perf_matrix,
        dtype=float,
    )
    if M.ndim != 2:
        raise ValueError("is_perf_matrix must be 2-D (T x N)")
    T, N = M.shape
    if N < 2:
        raise ValueError("need at least 2 strategy configurations (columns)")
    if n_partitions < 2 or n_partitions % 2 != 0:
        raise ValueError("n_partitions must be an even integer >= 2")
    if n_partitions > T:
        raise ValueError("n_partitions cannot exceed number of rows T")

    # Drop columns that are all-NaN; replace remaining NaNs with column means
    # so a single missing slice does not nuke an otherwise valid config.
    col_ok = ~np.all(np.isnan(M), axis=0)
    if col_ok.sum() < 2:
        raise ValueError("fewer than 2 non-empty configurations after NaN drop")
    M = M[:, col_ok]
    col_mean = np.nanmean(M, axis=0)
    nan_loc = np.isnan(M)
    if nan_loc.any():
        M[nan_loc] = np.take(col_mean, np.where(nan_loc)[1])

    N = M.shape[1]

    # Partition rows into S contiguous, (near-)equal sub-matrices.
    groups = np.array_split(np.arange(T), n_partitions)
    s_idx = list(range(n_partitions))
    half = n_partitions // 2

    logits: List[float] = []
    for is_groups in combinations(s_idx, half):
        is_set = set(is_groups)
        is_rows = np.concatenate([groups[g] for g in s_idx if g in is_set])
        os_rows = np.concatenate([groups[g] for g in s_idx if g not in is_set])

        is_perf = M[is_rows, :].mean(axis=0)   # IS statistic per config
        os_perf = M[os_rows, :].mean(axis=0)   # OS statistic per config

        n_star = int(np.argmax(is_perf))        # IS champion

        # Relative OS rank of the IS champion among the N configs.
        # rank 1 == worst, N == best (ties averaged).
        os_rank = _rankdata_average(os_perf)[n_star]
        w = os_rank / (N + 1.0)                 # in (0, 1)
        w = min(max(w, 1e-9), 1.0 - 1e-9)
        logits.append(float(np.log(w / (1.0 - w))))

    logits_arr = np.asarray(logits, dtype=float)
    # PBO = P(logit <= 0) = champion lands in bottom half OS.
    pbo = float(np.mean(logits_arr <= 0.0))

    return {
        "pbo": pbo,
        "logits": logits_arr,
        "n_combinations": int(comb(n_partitions, half)),
        "n_partitions": int(n_partitions),
    }
