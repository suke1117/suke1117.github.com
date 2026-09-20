import numpy as np
import pytest

from src.models.plackett_luce import (
    PLTemperature,
    exacta_probs,
    place_probs,
    quinella_probs,
    race_win_probs_from_scores,
    trifecta_probs,
    trio_probs,
    win_probs,
)


@pytest.fixture
def p():
    return win_probs(np.array([1.2, 0.3, -0.4, 0.8, -1.0, 0.0]), temperature=0.8)


def test_win_probs_sum_to_one(p):
    assert np.isclose(p.sum(), 1.0)
    assert (p > 0).all()


def test_exacta_consistency(p):
    m = exacta_probs(p)
    assert np.isclose(m.sum(), 1.0)
    assert np.allclose(np.diag(m), 0.0)
    assert np.allclose(m.sum(axis=1), p)  # marginal over second place = win prob


def test_trifecta_consistency(p):
    t = trifecta_probs(p)
    assert np.isclose(t.sum(), 1.0)
    assert np.allclose(t.sum(axis=2), exacta_probs(p))
    n = len(p)
    for i in range(n):
        assert t[i, i, :].sum() == 0 and t[i, :, i].sum() == 0 and t[:, i, i].sum() == 0


def test_quinella_and_trio(p):
    q = quinella_probs(p)
    assert np.isclose(np.triu(q, 1).sum(), 1.0)
    s = trio_probs(p)
    n = len(p)
    total = sum(s[i, j, k] for i in range(n) for j in range(i + 1, n) for k in range(j + 1, n))
    assert np.isclose(total, 1.0)


def test_place_probs(p):
    pl = place_probs(p, k=3)
    assert np.isclose(pl.sum(), 3.0)
    assert (pl >= p - 1e-12).all() and (pl <= 1.0 + 1e-12).all()
    mc = place_probs(p, k=4)
    assert np.isclose(mc.sum(), 4.0, atol=1e-6)


def test_temperature_fit_recovers_scale():
    rng = np.random.default_rng(3)
    true_t = 2.0
    groups, winners = [], []
    for _ in range(3000):
        s = rng.normal(0, 1.5, 12)
        pw = win_probs(s, true_t)
        groups.append(s)
        winners.append(int(rng.choice(12, p=pw)))
    fit = PLTemperature.fit(groups, winners)
    assert abs(fit.temperature - true_t) < 0.25


def test_vectorised_race_softmax_matches_loop():
    import pandas as pd

    df = pd.DataFrame({"race_id": ["b", "a", "a", "b", "b"], "score": [0.1, 0.5, -0.2, 1.0, 0.0]})
    v = race_win_probs_from_scores(df, "score", 1.0)
    for rid in ("a", "b"):
        m = (df["race_id"] == rid).to_numpy()
        assert np.allclose(v[m], win_probs(df.loc[m, "score"].to_numpy()))
