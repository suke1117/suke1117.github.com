"""Exotic tickets are only sound if the running-down model is. Both are tested here."""
import numpy as np
import pandas as pd
import pytest

from src.betting.strategy import (
    BetPolicy,
    Bet,
    bet_wins,
    market_win_probs,
    place_payout_depth,
    quinella_odds_from_win_pool,
    select_bets,
    settle_bet,
)
from src.models.plackett_luce import (
    RunDownDiscount,
    exacta_probs,
    place_probs,
    quinella_probs,
    trifecta_probs,
    win_probs,
)


@pytest.fixture
def p():
    return win_probs(np.array([1.6, 0.9, 0.4, 0.0, -0.5, -1.0, -1.4]))


# --------------------------------------------------------------------------
# the discount must stay a probability model at every power
# --------------------------------------------------------------------------
@pytest.mark.parametrize("lam", [0.4, 0.7, 1.0, 1.3])
def test_discounted_running_down_stays_normalised(p, lam):
    ex = exacta_probs(p, lam)
    assert ex.sum() == pytest.approx(1.0)
    assert np.allclose(np.diag(ex), 0.0)
    # first place is never touched by the discount: the row marginal is the win prob
    assert np.allclose(ex.sum(axis=1), p)
    t = trifecta_probs(p, lam)
    assert t.sum() == pytest.approx(1.0)
    assert np.allclose(t.sum(axis=2), ex)
    assert place_probs(p, 3, lam).sum() == pytest.approx(3.0)


def test_lambda_one_is_exactly_harville(p):
    """The default must not change any existing number."""
    denom = np.clip(1.0 - p, 1e-12, None)
    harville = np.outer(p / denom, p)
    np.fill_diagonal(harville, 0.0)
    assert np.allclose(exacta_probs(p, 1.0), harville)


def test_discount_moves_place_probability_the_documented_way(p):
    """Below one, the favourite places less often and the outsider more."""
    full = place_probs(p, 3, 1.0)
    disc = place_probs(p, 3, 0.5)
    assert disc[0] < full[0], "the strongest runner must lose place probability"
    assert disc[-1] > full[-1], "the weakest must gain it"
    assert disc.sum() == pytest.approx(full.sum())


def test_quinella_is_symmetric_and_sums_to_one(p):
    q = quinella_probs(p, 0.7)
    assert np.allclose(q, q.T)
    assert np.triu(q, 1).sum() == pytest.approx(1.0)


def test_the_fitter_recovers_the_power_that_generated_the_data():
    rng = np.random.default_rng(4)
    true_lam = 0.55
    probs, first, second, third = [], [], [], []
    for _ in range(4000):
        n = int(rng.integers(8, 15))
        pr = win_probs(rng.normal(0, 1.2, n))
        remaining = list(range(n))
        picks = []
        for pos in range(3):
            w = pr[remaining] if pos == 0 else np.power(pr[remaining], true_lam)
            w = w / w.sum()
            k = int(rng.choice(len(remaining), p=w))
            picks.append(remaining.pop(k))
        probs.append(pr)
        first.append(picks[0]); second.append(picks[1]); third.append(picks[2])
    fit = RunDownDiscount.fit(probs, first, second, third)
    assert fit.fitted and abs(fit.lam - true_lam) < 0.08, fit.lam
    assert abs(fit.mu - true_lam) < 0.10, fit.mu


def test_an_unfitted_discount_is_a_no_op():
    d = RunDownDiscount()
    assert (d.lam, d.mu, d.fitted) == (1.0, 1.0, False)
    assert RunDownDiscount.from_dict(d.to_dict()).lam == 1.0


# --------------------------------------------------------------------------
# selection and settlement
# --------------------------------------------------------------------------
def _race(n=10):
    return pd.DataFrame({
        "race_id": ["R1"] * n, "entrant_id": [f"H{i}" for i in range(n)], "n_runners": [n] * n,
        "p_win": np.array([.30, .18, .13, .10, .08, .06, .05, .04, .04, .02])[:n],
        "win_odds": np.array([2.5, 4.0, 6.0, 8.0, 11., 15., 20., 28., 35., 60.])[:n],
        "place_odds": np.array([1.2, 1.6, 2.0, 2.6, 3.4, 4.5, 6.0, 8.0, 10., 18.])[:n],
    })


def test_place_depth_follows_the_field_size():
    assert place_payout_depth(18) == 3 and place_payout_depth(8) == 3
    assert place_payout_depth(7) == 2 and place_payout_depth(5) == 2
    assert place_payout_depth(4) == 1


def test_enabling_a_market_never_breaches_the_race_cap():
    race = _race()
    for tt in [("win",), ("win", "place"), ("win", "place", "quinella")]:
        pol = BetPolicy(alpha=0.25, ev_threshold=1.05, max_bets_per_race=8, ticket_types=tt)
        bets = select_bets(race, 1_000_000, pol, run_down=(0.7, 0.66))
        assert sum(b.fraction for b in bets) <= pol.max_race_exposure + 1e-9
        assert all(b.bet_type in tt for b in bets)
        assert all(b.stake % 100 == 0 and b.stake > 0 for b in bets)


def test_a_policy_cannot_ask_for_a_market_that_does_not_exist():
    with pytest.raises(ValueError, match="unknown ticket types"):
        BetPolicy(ticket_types=("win", "superfecta"))
    with pytest.raises(ValueError, match="never bet"):
        BetPolicy(ticket_types=())


def test_place_bets_are_skipped_without_place_odds():
    race = _race().drop(columns=["place_odds"])
    bets = select_bets(race, 1_000_000, BetPolicy(ticket_types=("win", "place")), run_down=(0.7, 0.7))
    assert all(b.bet_type == "win" for b in bets)


def test_settlement_by_ticket_type():
    n = 10
    finishes = {f"H{i}": i + 1 for i in range(n)}
    cases = [
        (Bet("R1", "H0", "win", .3, 2.5, 1.0, 0.01, 1000, ("H0",)), True),
        (Bet("R1", "H1", "win", .2, 4.0, 1.0, 0.01, 1000, ("H1",)), False),
        (Bet("R1", "H2", "place", .5, 2.0, 1.0, 0.01, 1000, ("H2",)), True),
        (Bet("R1", "H3", "place", .4, 2.6, 1.0, 0.01, 1000, ("H3",)), False),
        (Bet("R1", "H0+H1", "quinella", .1, 8.0, 1.0, 0.01, 1000, ("H0", "H1")), True),
        (Bet("R1", "H1+H2", "quinella", .1, 9.0, 1.0, 0.01, 1000, ("H1", "H2")), False),
    ]
    for bet, expected in cases:
        assert bet_wins(bet, finishes, n) is expected, bet.entrant_id
        profit = settle_bet(bet, finishes, n)
        assert (profit > 0) is expected


def test_a_runner_with_no_result_never_wins():
    bet = Bet("R1", "H9", "place", .3, 3.0, 1.0, 0.01, 1000, ("H9",))
    assert bet_wins(bet, {"H9": 0}, 10) is False
    assert settle_bet(bet, {}, 10) == -1000


def test_modelled_quinella_pool_carries_the_takeout():
    mkt = market_win_probs(np.array([2.5, 4.0, 6.0, 8.0]))
    assert mkt.sum() == pytest.approx(1.0)
    o = quinella_odds_from_win_pool(mkt, takeout=0.25)
    pair = quinella_probs(mkt, 1.0)
    implied = np.triu(1.0 / o, 1).sum()
    assert implied == pytest.approx(1.0 / 0.75, rel=1e-6)   # overround equals 1/(1-takeout)
    assert np.isinf(o[0, 0])
    assert o[0, 1] == pytest.approx(0.75 / pair[0, 1])
