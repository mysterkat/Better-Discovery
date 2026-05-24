"""v1.3.0 — LightGBM tree-based candidate generation (alternative to KMeans).

The default clustering pipeline (KMeans + Bidirectional analysis + initial
backtest) finds STATISTICAL groupings of bars, then post-hoc checks whether
each cluster predicts a tradeable price move.  Lots of wasted work on
clusters that turn out to have no edge.

The tree approach inverts this: train a LightGBM regressor on
(features → forward signed return), then use the model's LEAF PARTITION as
the cluster labels.  Each leaf is, by construction, a feature-space region
whose member bars share a similar (predicted) forward return.

Why this should work better:
  * Each leaf is an indicator-conjunction rule found by an algorithm whose
    *literal job* is to maximise forward-return predictive power.  KMeans
    optimises within-cluster variance in feature space, which has nothing
    to do with profitability.
  * Trees naturally encode FEATURE INTERACTIONS.  KMeans cannot — every
    feature contributes equally to the Euclidean distance.
  * Train + leaf-assignment is O(N log N) per tree; LightGBM on 100k bars
    × 27 features finishes in seconds.
  * The leaf's path through the tree IS the candidate rule — no separate
    optimisation step needed to discover the rule (the optimiser then
    just polishes the bounds).

The downstream pipeline (price distributions, backtest, GA refine,
quality filters) treats `labels` agnostically — swapping clustering for
leaves requires no other changes.
"""

from __future__ import annotations

import numpy as np


def _signed_best_move(
    indices: np.ndarray,
    hi: np.ndarray,
    lo: np.ndarray,
    cl: np.ndarray,
    forward_bars: int,
) -> np.ndarray:
    """Per-bar label = signed magnitude of the best forward price move.

    For bar i:
      up_move = (max(high[i+1 .. i+fwd]) - close[i]) / close[i]
      dn_move = (close[i] - min(low[i+1 .. i+fwd])) / close[i]
      label   = +up_move if up_move >= dn_move else -dn_move

    Positive labels = bars where LONG would have captured the dominant move.
    Negative labels = bars where SHORT would have captured the dominant move.
    Magnitude        = how big the opportunity was (so the tree weights big
                       moves more than marginal ones in its loss).

    Bars too close to the end (i + fwd >= n) get label 0 and should be
    excluded from training.
    """
    n = len(cl)
    y = np.zeros(len(indices), dtype=np.float64)
    for j, i in enumerate(indices):
        end = i + forward_bars
        if end >= n or cl[i] == 0:
            continue
        future_hi = hi[i + 1 : end + 1].max()
        future_lo = lo[i + 1 : end + 1].min()
        up_move = (future_hi - cl[i]) / cl[i]
        dn_move = (cl[i] - future_lo) / cl[i]
        y[j] = up_move if up_move >= dn_move else -dn_move
    return y


def cluster_by_lightgbm_leaves(
    X_tr: np.ndarray,
    idx_tr: np.ndarray,
    df_train,
    forward_bars: int,
    num_leaves: int,
    n_estimators: int,
    min_samples_leaf: int,
    random_seed: int,
    learning_rate: float = 0.05,
) -> tuple[np.ndarray, int, object]:
    """Train a LightGBM regressor on (features → signed best move) and return
    leaf-id labels in the same shape as the KMeans pipeline produces.

    Parameters
    ----------
    X_tr : (n_bars, n_features) — feature matrix for train bars.
    idx_tr : (n_bars,) — original bar indices into df_train.
    df_train : the full train DataFrame (used for high/low/close arrays).
    forward_bars : look-ahead window for label calculation.
    num_leaves : per-tree leaf count cap.
    n_estimators : number of trees in the ensemble.  Final cluster count
        is the number of UNIQUE leaf-path tuples across all trees.
    min_samples_leaf : minimum bars required to form a leaf (LightGBM
        `min_child_samples`).  Acts like KMeans's minimum-cluster-size.
    random_seed : reproducibility.
    learning_rate : shrinkage applied to each tree's contribution.

    Returns
    -------
    labels : (n_bars,) int32 — leaf-cluster id per train bar.
    n_clusters : number of unique leaf-clusters produced.
    model : the trained LightGBM model (kept around for test-set assignment).
    """
    import lightgbm as lgb  # imported lazily so missing dep doesn't break import

    hi = df_train["high"].values
    lo = df_train["low"].values
    cl = df_train["close"].values

    y = _signed_best_move(idx_tr, hi, lo, cl, forward_bars)

    # Drop bars too close to the end (y == 0 and bar is in the trailing window).
    n_total_bars = len(cl)
    valid_mask = (idx_tr + forward_bars) < n_total_bars
    if not valid_mask.all():
        # Train only on bars with a full forward window.
        X_fit = X_tr[valid_mask]
        y_fit = y[valid_mask]
    else:
        X_fit = X_tr
        y_fit = y

    if len(X_fit) < max(min_samples_leaf * 4, 200):
        # Not enough labelled bars for a useful tree — return a single
        # cluster so the rest of the pipeline still runs.
        return np.zeros(len(idx_tr), dtype=np.int32), 1, None

    model = lgb.LGBMRegressor(
        n_estimators=n_estimators,
        num_leaves=num_leaves,
        min_child_samples=min_samples_leaf,
        learning_rate=learning_rate,
        random_state=int(random_seed) % (2 ** 31),
        verbose=-1,
        n_jobs=-1,
    )
    model.fit(X_fit, y_fit)

    # Predict leaf indices per (bar, tree).  Shape: (n_bars, n_trees).
    leaf_indices = model.predict(X_tr, pred_leaf=True)
    if leaf_indices.ndim == 1:
        leaf_indices = leaf_indices.reshape(-1, 1)

    # Combine the per-tree leaf path into a single hashable cluster id.
    # Two bars are in the same cluster iff they fall in the same leaf of
    # EVERY tree.  This is the conjunction of all leaf rules — the most
    # discriminative grouping the ensemble can produce.
    paths = [tuple(row) for row in leaf_indices]
    path_to_id: dict[tuple, int] = {}
    labels = np.zeros(len(idx_tr), dtype=np.int32)
    for j, p in enumerate(paths):
        if p not in path_to_id:
            path_to_id[p] = len(path_to_id)
        labels[j] = path_to_id[p]

    n_clusters = len(path_to_id)
    return labels, n_clusters, model


def extract_leaf_rules(
    model: object,
    feature_names: list[str],
) -> dict[int, dict[str, tuple[float, float]]]:
    """Extract each leaf's path through the tree as a {col: (lo, hi)} rule.

    Walks the tree structure depth-first; for each leaf, the path from root
    encodes a conjunction of feature splits.  Example path:
      rsi14 <= 40  AND  atr_pct > 0.005  AND  bb_width <= 0.012
    -> rule: { rsi14: (-inf, 40), atr_pct: (0.005, +inf), bb_width: (-inf, 0.012) }

    For features that don't appear in the path, the bounds stay at ±inf.
    These rules are returned PER TREE — caller is responsible for aggregating
    across trees if `n_estimators > 1`.

    Returns dict[leaf_index → {feature_name: (lo, hi)}].  Leaf indices match
    LightGBM's `predict(pred_leaf=True)` output.
    """
    if model is None:
        return {}

    booster = model.booster_
    leaf_rules: dict[int, dict[str, tuple[float, float]]] = {}

    # Walk the first tree (we default to n_estimators=1; multi-tree handled below)
    dump = booster.dump_model()
    for tree_meta in dump.get("tree_info", []):
        tree_idx = tree_meta.get("tree_index", 0)
        root = tree_meta.get("tree_structure", {})
        _walk_tree(root, {}, feature_names, leaf_rules, tree_idx)

    return leaf_rules


def _walk_tree(
    node: dict,
    current_bounds: dict[str, tuple[float, float]],
    feature_names: list[str],
    leaf_rules: dict[int, dict[str, tuple[float, float]]],
    tree_idx: int,
) -> None:
    """Depth-first walk; accumulates {col: (lo, hi)} along the path; emits at leaves."""
    if "leaf_index" in node:
        # Leaf: record the accumulated bounds, keyed by (tree_idx, leaf_index)
        # caller can flatten if needed.  For single-tree default the leaf_index
        # alone uniquely identifies the cluster.
        leaf_rules[node["leaf_index"]] = {
            k: v for k, v in current_bounds.items()
            if not (v[0] == float("-inf") and v[1] == float("inf"))
        }
        return

    split_feature = node.get("split_feature")
    threshold = node.get("threshold")
    if split_feature is None or threshold is None:
        return

    # split_feature is an int index into the original feature_names list
    if isinstance(split_feature, int) and 0 <= split_feature < len(feature_names):
        feat_name = feature_names[split_feature]
    else:
        feat_name = str(split_feature)

    # LightGBM split rule: left child = (x <= threshold); right = (x > threshold)
    # decision_type defaults to "<=" for numerical features.
    left = node.get("left_child", {})
    right = node.get("right_child", {})

    # LEFT branch: feat <= threshold → upper bound tightens
    left_bounds = dict(current_bounds)
    lo, hi = left_bounds.get(feat_name, (float("-inf"), float("inf")))
    left_bounds[feat_name] = (lo, min(hi, threshold))
    _walk_tree(left, left_bounds, feature_names, leaf_rules, tree_idx)

    # RIGHT branch: feat > threshold → lower bound tightens
    right_bounds = dict(current_bounds)
    lo, hi = right_bounds.get(feat_name, (float("-inf"), float("inf")))
    right_bounds[feat_name] = (max(lo, threshold), hi)
    _walk_tree(right, right_bounds, feature_names, leaf_rules, tree_idx)


def assign_test_bars_to_leaves(
    model: object, X_te: np.ndarray, train_labels_by_path: dict
) -> np.ndarray:
    """Use the trained LightGBM model to assign test bars to the same
    leaf-clusters as the training step produced.

    The model returns leaf indices per (bar, tree); we map each test bar's
    full leaf-path tuple back to the cluster id used in `train_labels_by_path`.
    Test bars whose path never appeared in training get assigned to the
    nearest existing cluster (Hamming distance on the leaf-path).
    """
    if model is None:
        return np.zeros(len(X_te), dtype=np.int32)

    leaf_indices = model.predict(X_te, pred_leaf=True)
    if leaf_indices.ndim == 1:
        leaf_indices = leaf_indices.reshape(-1, 1)

    # Pre-compute the train path list for nearest-neighbour fallback.
    train_paths = list(train_labels_by_path.keys())
    train_paths_arr = np.asarray(train_paths) if train_paths else None

    labels_te = np.zeros(len(X_te), dtype=np.int32)
    for j, row in enumerate(leaf_indices):
        p = tuple(row)
        if p in train_labels_by_path:
            labels_te[j] = train_labels_by_path[p]
        elif train_paths_arr is not None:
            # Hamming distance on the leaf-path tuple
            dists = (train_paths_arr != np.asarray(row)).sum(axis=1)
            labels_te[j] = train_labels_by_path[train_paths[int(np.argmin(dists))]]
    return labels_te
