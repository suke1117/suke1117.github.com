import numpy as np
import pandas as pd
import pytest

from src.backtest.metrics import summarize
from src.backtest.simulator import WalkForwardSimulator
from src.backtest.sweep import log_growth_per_day, objective_value, parse_grid, run_grid, score_policy, select_best
from src.betting.kelly_calculator import KellyAlphaError
from src.betting.strategy import BetPolicy
from src.data.features import CATEGORICAL_FEATURES


def test_parse_grid():
    assert parse_grid("0.02,0.05, 0.1") == [0.02, 0.05, 0.1]
    assert parse_grid("1.05") == [1.05]


def test_log_growth_per_day():
    daily = pd.DataFrame({"bankroll": [1100.0, 1210.0]})
    assert np.isclose(log_growth_per_day(daily, 1000.0), np.log(1.21) / 2)
    assert log_growth_per_day(pd.DataFrame(), 1000.0) == 0.0
    assert log_growth_per_day(pd.DataFrame({"bankroll": [0.0]}), 1000.0) == float("-inf")


def _grid():
    return pd.DataFrame({
        "alpha": [0.10, 0.02, 0.25, 0.05],
        "ev_threshold": [1.05, 1.30, 1.05, 1.10],
        "max_bets_per_race": [3, 1, 3, 2],
        "n_bets": [900, 800, 900, 50],           # last one is too small a sample
        "max_drawdown": [0.30, 0.06, 0.55, 0.05],  # first and third breach the cap
        "sharpe_daily_annualised": [3.9, 2.7, 4.5, 9.9],
        "log_growth_per_day": [0.01, 0.003, 0.02, 0.05],
        "roi_recovery_rate": [1.2, 1.4, 1.3, 2.0],
    })


def test_select_best_enforces_risk_constraints_before_return():
    best = select_best(_grid(), "sharpe", max_dd=0.25, min_bets=200)
    assert best is not None
    # the highest-Sharpe and highest-growth rows are disqualified by drawdown /
    # sample size, so the surviving conservative row must win
    assert best["alpha"] == 0.02 and best["ev_threshold"] == 1.30


def test_select_best_returns_none_when_nothing_qualifies():
    assert select_best(_grid(), "sharpe", max_dd=0.01, min_bets=200) is None
    assert select_best(_grid(), "sharpe", max_dd=0.25, min_bets=10_000) is None


def test_objective_value_dispatch():
    row = {"sharpe_daily_annualised": 1.5, "log_growth_per_day": 0.02, "roi_recovery_rate": 1.3}
    assert objective_value(row, "sharpe") == 1.5
    assert objective_value(row, "growth") == 0.02
    assert objective_value(row, "recovery") == 1.3
    assert objective_value({"roi_recovery_rate": float("nan")}, "recovery") == float("-inf")
    with pytest.raises(ValueError):
        objective_value(row, "nonsense")


@pytest.fixture(scope="module")
def sim_and_pred(small_table):
    table, cols = small_table
    cats = [c for c in CATEGORICAL_FEATURES if c in cols]
    sim = WalkForwardSimulator(table, cols, cats, BetPolicy(), 1_000_000, retrain_months=3, calib_months=3,
                               min_train_races=200)
    pred = sim.generate_predictions(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-28"))
    return sim, pred


def test_predictions_are_policy_free_and_out_of_sample(sim_and_pred, small_table):
    sim, pred = sim_and_pred
    table, _ = small_table
    assert not pred.empty
    # every predicted race is dated inside its own period, never before it
    assert (pred["race_date"] >= pred["period_start"]).all()
    assert (pred["race_date"] <= pred["period_end"]).all()
    assert np.allclose(pred.groupby("race_id")["p_win"].sum(), 1.0)


def test_grid_reuses_one_prediction_pass(sim_and_pred):
    sim, pred = sim_and_pred
    grid = run_grid(pred, sim, [0.05, 0.10], [1.05, 1.30], [1, 3], 1_000_000, compound=True, max_stake_yen=1e6)
    assert len(grid) == 2 * 2 * 2
    # a stricter EV threshold can never place more bets than a looser one
    for alpha in (0.05, 0.10):
        for mb in (1, 3):
            sub = grid[(grid["alpha"] == alpha) & (grid["max_bets_per_race"] == mb)].sort_values("ev_threshold")
            assert sub["n_bets"].is_monotonic_decreasing
    # a bigger Kelly fraction can never reduce drawdown at a fixed edge
    for ev in (1.05, 1.30):
        sub = grid[(grid["ev_threshold"] == ev) & (grid["max_bets_per_race"] == 3)].sort_values("alpha")
        assert sub["max_drawdown"].iloc[-1] >= sub["max_drawdown"].iloc[0]


def test_grid_refuses_full_kelly(sim_and_pred):
    sim, pred = sim_and_pred
    with pytest.raises(KellyAlphaError):
        run_grid(pred, sim, [0.10, 1.0], [1.05], [1], 1_000_000, compound=True, max_stake_yen=1e6)


def test_simulate_is_deterministic_and_bankroll_independent_of_history(sim_and_pred):
    sim, pred = sim_and_pred
    policy = BetPolicy(alpha=0.05, ev_threshold=1.10)
    a = sim.simulate(pred, policy=policy, bankroll=1_000_000, compound=False)[0]
    b = sim.simulate(pred, policy=policy, bankroll=1_000_000, compound=False)[0]
    pd.testing.assert_frame_equal(a, b)
    if len(a):
        assert (a["stake"] > 0).all() and (a["bankroll_after"] > 0).all()


# --------------------------------------------------------------------------
# V3: how much of a search result is just the search
# --------------------------------------------------------------------------
def test_the_null_world_follows_the_market_not_a_coin_flip():
    """Shuffling finishers would hand long shots a huge fake edge; this must not."""
    from src.backtest.sweep import null_outcomes

    odds = np.array([1.5, 4.0, 8.0, 20.0, 40.0, 60.0])
    df = pd.DataFrame({"race_id": ["A"] * 6, "win_odds": odds})
    wins = np.zeros(6)
    for seed in range(1500):
        out = null_outcomes(df, seed)
        wins[int(np.flatnonzero(out["finish_position"].to_numpy() == 1)[0])] += 1
    implied = (1 / odds) / (1 / odds).sum()
    assert np.abs(wins / 1500 - implied).max() < 0.03
    # every race still gets a complete, valid finishing order
    out = null_outcomes(pd.DataFrame({"race_id": ["A", "A", "B", "B", "B"],
                                      "win_odds": [2.0, 3.0, 2.0, 5.0, 9.0]}), 1)
    for _, g in out.groupby("race_id"):
        assert sorted(g["finish_position"]) == list(range(1, len(g) + 1))


def test_the_null_leaves_no_edge_for_any_strategy():
    """Expected value under the null is 1 - takeout for every runner."""
    from src.backtest.sweep import null_outcomes

    rng = np.random.default_rng(2)
    n_races, n = 3000, 8
    odds = np.round(np.clip(rng.lognormal(1.6, 0.7, n_races * n), 1.2, 200.0), 1)
    df = pd.DataFrame({"race_id": np.repeat(np.arange(n_races), n), "win_odds": odds})
    out = null_outcomes(df, 11)
    won = (out["finish_position"] == 1).to_numpy()
    ret = np.where(won, out["win_odds"].to_numpy(), 0.0)
    # Each runner's expected return is 1 / its own race's overround. Averaging
    # the overrounds first and inverting is a different number (Jensen), so the
    # expectation has to be built per race.
    per_race = df.groupby("race_id")["win_odds"].transform(lambda o: 1.0 / (1 / o).sum())
    assert ret.mean() == pytest.approx(per_race.mean(), rel=0.05)
    assert ret.mean() < 1.0, "the null must leave no strategy above break-even"


def test_null_outcomes_do_not_touch_the_original_frame():
    from src.backtest.sweep import null_outcomes

    df = pd.DataFrame({"race_id": ["A"] * 4, "win_odds": [2.0, 4.0, 6.0, 8.0],
                       "finish_position": [1, 2, 3, 4]})
    before = df["finish_position"].tolist()
    null_outcomes(df, 5)
    assert df["finish_position"].tolist() == before
