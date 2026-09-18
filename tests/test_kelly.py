import numpy as np
import pandas as pd
import pytest

from src.betting.kelly_calculator import KellyAlphaError, expected_value, full_kelly_fraction, kelly_stake, validate_alpha
from src.betting.strategy import BetPolicy, multi_outcome_kelly, select_win_bets, settle_win_bet
from src.common.config import MAX_KELLY_ALPHA, MAX_RACE_EXPOSURE, MAX_SINGLE_BET_FRACTION


def test_kelly_formula():
    assert np.isclose(full_kelly_fraction(0.15, 10.0), 0.15 - 0.85 / 9)
    assert np.isclose(expected_value(0.15, 10.0), 1.5)
    assert full_kelly_fraction(0.05, 10.0) < 0  # negative EV -> negative fraction


def test_full_kelly_is_forbidden():
    for a in (1.0, 0.5, 0.26, 0.0, -0.1):
        with pytest.raises(KellyAlphaError):
            validate_alpha(a)
    assert validate_alpha(MAX_KELLY_ALPHA) == MAX_KELLY_ALPHA
    with pytest.raises(KellyAlphaError):
        kelly_stake(0.5, 3.0, 1e6, alpha=1.0)


def test_stake_respects_ev_threshold_and_units():
    r = kelly_stake(0.15, 10.0, 1_000_000, alpha=0.1, ev_threshold=1.05)
    assert r.bet and r.stake_yen == 5500 and r.stake_yen % 100 == 0
    r2 = kelly_stake(0.10, 10.0, 1_000_000, alpha=0.1, ev_threshold=1.05)  # EV = 1.0
    assert not r2.bet and r2.stake_yen == 0
    r3 = kelly_stake(0.9, 50.0, 1_000_000, alpha=0.25)
    assert r3.fraction <= MAX_SINGLE_BET_FRACTION + 1e-12


def test_policy_hard_caps():
    with pytest.raises(KellyAlphaError):
        BetPolicy(alpha=0.5)
    with pytest.raises(ValueError):
        BetPolicy(max_race_exposure=0.5)
    with pytest.raises(ValueError):
        BetPolicy(max_single_fraction=0.5)


def test_multi_outcome_kelly_matches_single_when_one_candidate():
    p = np.array([0.30, 0.10, 0.05])
    o = np.array([5.0, 8.0, 10.0])  # only first has EV > 1 (1.5); others 0.8, 0.5
    f = multi_outcome_kelly(p, o)
    assert f[1] == 0 and f[2] == 0
    assert np.isclose(f[0], full_kelly_fraction(0.30, 5.0))


def test_multi_outcome_kelly_total_below_one_and_positive():
    p = np.array([0.35, 0.25, 0.20, 0.10, 0.10])
    o = np.array([2.4, 3.0, 4.5, 6.0, 25.0])  # overround market: sum(1/o) > 1
    assert (1 / o).sum() > 1
    f = multi_outcome_kelly(p, o)
    assert (f >= 0).all() and f.sum() < 1
    # growth-rate optimality check: perturbing any bet must not increase expected log growth
    def growth(fr):
        return sum(p[i] * np.log(1 - fr.sum() + fr[i] * o[i]) for i in range(len(p)))
    base = growth(f)
    for i in range(len(p)):
        for d in (1e-3, -1e-3):
            g = f.copy()
            g[i] = max(0.0, g[i] + d)
            assert growth(g) <= base + 1e-9


def _race(p, o):
    n = len(p)
    return pd.DataFrame({"race_id": ["R1"] * n, "entrant_id": [f"H{i}" for i in range(n)], "p_win": p, "win_odds": o,
                         "finish_position": list(range(1, n + 1))})


def test_select_win_bets_only_positive_ev_and_capped():
    race = _race([0.40, 0.30, 0.20, 0.10], [2.0, 2.5, 8.0, 30.0])  # EV: 0.8, 0.75, 1.6, 3.0
    bets = select_win_bets(race, 1_000_000, BetPolicy(alpha=0.1, ev_threshold=1.05))
    ids = {b.entrant_id for b in bets}
    assert ids <= {"H2", "H3"} and len(bets) >= 1
    assert all(b.ev >= 1.05 for b in bets)
    assert sum(b.fraction for b in bets) <= MAX_RACE_EXPOSURE + 1e-12
    assert all(b.stake % 100 == 0 and b.stake > 0 for b in bets)


def test_no_bets_when_market_is_efficient():
    race = _race([0.5, 0.3, 0.2], [1.6, 2.6, 4.0])  # all EV < 1
    assert select_win_bets(race, 1_000_000, BetPolicy()) == []


def test_flat_sizing_and_liquidity_cap():
    race = _race([0.5, 0.5], [3.0, 1.5])
    pol = BetPolicy(alpha=0.25, max_stake_yen=1000)
    bets = select_win_bets(race, 10_000_000, pol, sizing_bankroll=1_000_000)
    assert bets and all(b.stake <= 1000 for b in bets)


def test_settlement():
    from src.betting.strategy import Bet

    b = Bet("R", "H", "win", 0.2, 6.0, 1.2, 0.01, 1000)
    assert settle_win_bet(b, 1) == 5000
    assert settle_win_bet(b, 2) == -1000
    assert settle_win_bet(b, 1, payout_odds=5.0) == 4000
