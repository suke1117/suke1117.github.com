"""The planner has to be right about the direction of the tradeoff, or it is worse than nothing."""
import numpy as np
import pandas as pd
import pytest

from src.backtest.target_planner import full_kelly, growth_per_bet, simulate, summarise


@pytest.fixture
def edge():
    """A field of bets with a genuine 30% edge at 6.0 odds."""
    rng = np.random.default_rng(3)
    n = 4000
    odds = np.full(n, 6.0)
    prob = np.full(n, 0.216)                    # EV = 1.296
    won = rng.random(n) < prob
    return prob, odds, won


def test_full_kelly_matches_the_closed_form():
    f = full_kelly(np.array([0.2]), np.array([10.0]))
    assert f[0] == pytest.approx(0.2 - 0.8 / 9)
    assert full_kelly(np.array([0.05]), np.array([10.0]))[0] == 0.0   # negative edge -> no bet


def test_growth_peaks_at_full_kelly(edge):
    prob, odds, _ = edge
    fk = full_kelly(prob, odds)
    at_full = growth_per_bet(prob, odds, fk)
    for m in (0.25, 0.5, 0.8, 1.2, 2.0):
        assert growth_per_bet(prob, odds, fk * m) <= at_full + 1e-12, m
    # doubling Kelly gives back nearly all of the growth: that is the whole
    # reason the system caps alpha well below 1.0
    assert growth_per_bet(prob, odds, fk * 2.0) < at_full * 0.25
    assert growth_per_bet(prob, odds, fk * 2.6) < 0, "past ~2x Kelly the edge is spent and growth turns negative"


def test_overbetting_raises_the_mean_while_lowering_the_median(edge):
    prob, odds, won = edge
    rows = [summarise(simulate(prob, odds, won, 80, m, 20000, 11), 4.0) for m in (0.25, 1.0, 3.0)]
    means = [r["mean"] for r in rows]
    medians = [r["median"] for r in rows]
    assert means[2] > means[0], "the mean should keep rising with aggression"
    assert medians[2] < medians[1], "the median should fall once past full Kelly"
    assert rows[2]["p_ruin"] > rows[0]["p_ruin"]


def test_a_bigger_target_is_never_more_likely(edge):
    prob, odds, won = edge
    row = simulate(prob, odds, won, 80, 1.0, 20000, 5)
    probs = [summarise(dict(row), t)["p_target"] for t in (1.5, 2.0, 4.0, 8.0)]
    assert probs == sorted(probs, reverse=True)


def test_no_edge_means_no_target_is_reachable_by_sizing(edge):
    """With EV = 1.0 the Kelly fraction is zero, so aggression cannot manufacture growth."""
    _, odds, won = edge
    fair = np.full(odds.shape, 1.0 / 6.0)        # EV exactly 1.0
    assert full_kelly(fair, odds).max() == 0.0
    row = summarise(simulate(fair, odds, won, 80, 4.0, 5000, 2), 4.0)
    assert row["median"] == pytest.approx(1.0)
    assert row["p_target"] == 0.0


def test_the_horizon_scales_the_ceiling(edge):
    prob, odds, won = edge
    fk = full_kelly(prob, odds)
    g = growth_per_bet(prob, odds, fk)
    short = simulate(prob, odds, won, 40, 1.0, 20000, 9)
    long_ = simulate(prob, odds, won, 160, 1.0, 20000, 9)
    # compounding is additive in logs, so four times the bets is about four
    # times the log growth. This is why bet count, not stake size, is the lever.
    ratio = np.log(long_["median"]) / np.log(short["median"])
    assert 2.5 < ratio < 5.5, ratio
    assert np.log(short["median"]) == pytest.approx(g * 40, rel=0.5)
