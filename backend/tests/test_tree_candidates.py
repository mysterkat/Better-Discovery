"""LightGBM clustering regression tests."""

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("lightgbm")

from toolkit.tree_candidates import cluster_by_lightgbm_leaves


def test_cluster_accepts_python_list_indices():
    """build_features_parallel historically returned list indices; clustering
    must not fail with 'can only concatenate list (not int) to list'."""
    n = 400
    w = 10
    df = pd.DataFrame(
        {
            "high": np.random.rand(n) + 1,
            "low": np.random.rand(n),
            "close": np.random.rand(n) + 0.5,
        }
    )
    X = np.random.randn(n - w, 32).astype(np.float32)
    idx = list(range(w, n))

    labels, n_cl, model = cluster_by_lightgbm_leaves(
        X,
        idx,
        df,
        forward_bars=12,
        num_leaves=16,
        n_estimators=2,
        min_samples_leaf=20,
        random_seed=1,
    )

    assert model is not None
    assert n_cl >= 2
    assert labels.shape == (len(idx),)
    assert labels.dtype == np.int32
