import numpy as np
import pandas as pd
import pytest

from src.backtest.metrics import max_drawdown, sharpe_ratio, summarize
from src.backtest.simulator import WalkForwardSimulator, period_boundaries
from src.betting.strategy import BetPolicy
from src.data.features import CATEGORICAL_FEATURES
from src.models.ranker import RankerModel
from src.models.train_lgbm import chronological_split, evaluate, fit_pipeline


@pytest.fixture(scope="module")
def split(small_table):
    table, cols = small_table
    cats = [c for c in CATEGORICAL_FEATURES if c in cols]
    tr, ca, te = chronological_split(table, None, None)
    return table, cols, cats, tr, ca, te


def test_chronological_split_is_ordered(split):
    _, _, _, tr, ca, te = split
    assert tr["race_date"].max() < ca["race_date"].min()
    assert ca["race_date"].max() < te["race_date"].min()


def test_ranker_rejects_future_validation(split):
    table, cols, cats, tr, ca, te = split
    with pytest.raises(ValueError, match="strictly after"):
        RankerModel(cols, cats).fit(ca, tr)


def test_ranker_rejects_forbidden_columns(split):
    _, cols, cats, *_ = split
    with pytest.raises(ValueError, match="forbidden"):
        RankerModel(cols + ["finish_position"], cats)


def test_pipeline_probabilities_are_calibrated_and_beat_random(split, tmp_path):
    table, cols, cats, tr, ca, te = split
    pred = fit_pipeline(tr, ca, cols, cats, market_blend=True).predict(te)
    sums = pred.groupby("race_id")["p_win"].sum()
    assert np.allclose(sums, 1.0)
    m = evaluate(pred)
    random_top1 = (1.0 / te.groupby("race_id")["n_runners"].first()).mean()
    assert m["top1_hit_rate"] > random_top1 * 1.5
    assert m["ece_p_win"] < 0.03

    # round-trip persistence
    from src.models.predict import Predictor

    p = fit_pipeline(tr, ca, cols, cats)
    p.save(tmp_path)
    p2 = Predictor.load(tmp_path)
    a, b = p.predict(te)["p_win"].to_numpy(), p2.predict(te)["p_win"].to_numpy()
    assert np.allclose(a, b)


def test_period_boundaries():
    b = period_boundaries(pd.Timestamp("2022-01-01"), pd.Timestamp("2022-07-15"), 3)
    assert b[0] == pd.Timestamp("2022-01-01") and b[-1] == pd.Timestamp("2022-07-16")
    assert b[1] == pd.Timestamp("2022-04-01")


def test_walk_forward_never_trains_on_future(small_table, monkeypatch):
    table, cols = small_table
    cats = [c for c in CATEGORICAL_FEATURES if c in cols]
    seen = []
    import src.backtest.simulator as sim_mod

    real = sim_mod.fit_pipeline

    def spy(train, calib, *a, **k):
        seen.append((train["race_date"].max(), calib["race_date"].min(), calib["race_date"].max()))
        return real(train, calib, *a, **k)

    monkeypatch.setattr(sim_mod, "fit_pipeline", spy)
    sim = WalkForwardSimulator(table, cols, cats, BetPolicy(), 1_000_000, retrain_months=2, calib_months=3,
                               min_train_races=200)
    bets, daily, periods = sim.run(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-28"))
    assert len(periods) >= 2
    for (tr_max, ca_min, ca_max), (_, row) in zip(seen, periods.iterrows()):
        assert tr_max < ca_min <= ca_max < row["period_start"]
    if len(bets):
        assert (bets["stake"] % 100 == 0).all()
        assert (bets["ev"] >= 1.05).all()
        # bankroll must never go negative with fractional Kelly
        assert (bets["bankroll_after"] > 0).all()
    assert daily["race_date"].is_monotonic_increasing


def test_metrics():
    eq = np.array([100, 120, 90, 95, 130])
    assert np.isclose(max_drawdown(eq), 0.25)
    assert sharpe_ratio(np.array([0.01, 0.01, 0.01])) == 0.0
    bets = pd.DataFrame({"race_id": ["a", "b"], "stake": [100.0, 100.0], "profit": [300.0, -100.0], "odds": [4.0, 3.0],
                         "ev": [1.2, 1.1], "fraction": [0.01, 0.01]})
    daily = pd.DataFrame({"race_date": pd.to_datetime(["2022-01-01", "2022-01-02"]), "bankroll": [1300.0, 1200.0],
                          "ret": [0.3, -1 / 13]})
    s = summarize(bets, daily, 1000.0)
    assert np.isclose(s["roi_recovery_rate"], 2.0) and s["n_bets"] == 2 and s["hit_rate"] == 0.5
