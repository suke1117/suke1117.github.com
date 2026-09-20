"""The reasoning shown to a user must be the reasoning that made the decision."""
import numpy as np
import pandas as pd
import pytest

from src.betting.strategy import BetPolicy
from src.live.explain import (
    TOP_FACTORS,
    decision_for,
    explain_race,
    feature_label,
    race_conditions,
    race_title,
    runner_label,
    score_contributions,
    top_factors,
    venue_name,
)


# --------------------------------------------------------------------------
# labelling
# --------------------------------------------------------------------------
def test_venue_codes_survive_a_csv_round_trip():
    """A CSV read turns "04" into 4, which used to print as a bare number."""
    assert venue_name("04") == "新潟" and venue_name(4) == "新潟"
    assert venue_name("45") == "大井"
    assert venue_name("zz") == "zz"


def test_generated_feature_names_get_readable_labels():
    assert feature_label("jky_win_rate") == "騎手の勝率"
    assert "コース" in feature_label("jkyc_venue_win")
    assert "距離帯" in feature_label("entc_dist_norm_pos")
    assert "出走馬内順位" in feature_label("ent_avg_norm_pos_rank")
    assert feature_label("some_unmapped_thing") == "some_unmapped_thing"


def test_race_header_reads_as_a_race():
    meta = {"venue": "05", "race_no": 11, "distance_m": 2400, "surface": "turf", "going": "good", "n_runners": 18}
    assert race_title(meta) == "東京 11R"
    assert race_conditions(meta) == "2400m 芝 良 18頭"


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------
def _row(ev=1.4, p=0.2, odds=7.0, eid="H1"):
    return {"entrant_id": eid, "ev": ev, "p_win": p, "odds": odds}


def test_a_backed_runner_is_never_labelled_a_pass():
    """A place ticket used to show 見送り beside its own stake."""
    pol = BetPolicy(ev_threshold=1.15)
    backing = {"H1": [{"ticket": "place", "stake": 1400.0, "ev": 2.13}]}
    d = decision_for(_row(ev=0.92), pol, backing, 0)
    assert d["action"] == "bet" and d["ticket_ja"] == "複勝" and d["stake"] == 1400.0
    assert "複勝" in d["reason"]


def test_every_pass_names_the_rule_that_decided_it():
    pol = BetPolicy(ev_threshold=1.15, min_prob=0.02, max_bets_per_race=1)
    assert "オッズ" in decision_for(_row(odds=float("nan")), pol, {}, 0)["reason"]
    assert "閾値" in decision_for(_row(ev=1.0), pol, {}, 0)["reason"]
    assert "下限" in decision_for(_row(p=0.005), pol, {}, 0)["reason"]
    assert "上位" in decision_for(_row(), pol, {}, 3)["reason"]
    assert "最低購入単位" in decision_for(_row(), pol, {}, 0)["reason"]
    for case in (_row(ev=1.0), _row(p=0.005), _row(), _row(odds=1.0)):
        assert decision_for(case, pol, {}, 5)["action"] == "skip"


def test_multiple_tickets_on_one_runner_are_all_reported():
    backing = {"H1": [{"ticket": "win", "stake": 800.0, "ev": 1.3},
                      {"ticket": "place", "stake": 600.0, "ev": 1.5}]}
    d = decision_for(_row(), BetPolicy(), backing, 0)
    assert d["stake"] == 1400.0 and set(d["tickets"]) == {"win", "place"}
    assert "単勝" in d["ticket_ja"] and "複勝" in d["ticket_ja"]


# --------------------------------------------------------------------------
# attribution
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def fitted(small_table):
    from src.data.features import CATEGORICAL_FEATURES
    from src.models.train_lgbm import chronological_split, fit_pipeline

    table, cols = small_table
    cats = [c for c in CATEGORICAL_FEATURES if c in cols]
    tr, ca, te = chronological_split(table, None, None)
    return fit_pipeline(tr, ca, cols, cats, market_blend=True), te


def test_contributions_sum_exactly_to_the_score(fitted):
    """An explanation with a remainder is an explanation that hides something."""
    predictor, test = fitted
    race = test[test["race_id"] == test["race_id"].iloc[0]]
    contrib, base = score_contributions(predictor, race)
    assert contrib.shape == (len(race), len(predictor.ranker.feature_cols))
    assert np.allclose(contrib.sum(axis=1) + base, predictor.ranker.predict(race), atol=1e-6)


def test_top_factors_are_the_largest_by_magnitude(fitted):
    predictor, test = fitted
    race = test[test["race_id"] == test["race_id"].iloc[0]]
    contrib, _ = score_contributions(predictor, race)
    cols = predictor.ranker.feature_cols
    fs = top_factors(contrib[0], race.iloc[0][cols], cols)
    assert len(fs) <= TOP_FACTORS
    mags = [abs(f["contribution"]) for f in fs]
    assert mags == sorted(mags, reverse=True)
    biggest = max(abs(contrib[0]))
    assert abs(fs[0]["contribution"]) == pytest.approx(biggest, abs=1e-4)
    for f in fs:
        assert f["direction"] == ("up" if f["contribution"] > 0 else "down")
        assert f["label"] and f["feature"] in cols


def test_explained_race_covers_every_runner_and_agrees_with_the_bets(fitted):
    predictor, test = fitted
    race = test[test["race_id"] == test["race_id"].iloc[0]]
    priced = predictor.predict(race)
    pol = BetPolicy(alpha=0.1, ev_threshold=1.05, max_bets_per_race=3)
    out = explain_race(priced, predictor, pol, 1_000_000)

    assert len(out["runners"]) == len(race)
    assert [r["p_win"] for r in out["runners"]] == sorted((r["p_win"] for r in out["runners"]), reverse=True)
    assert all(r["decision"]["reason"] for r in out["runners"]), "every runner needs a stated reason"
    # every yen in the bets is attributed to a runner marked as backed
    backed = {r["entrant_id"] for r in out["runners"] if r["decision"]["action"] == "bet"}
    for b in out["bets"]:
        assert set(b["selection"]) <= backed
    assert out["total_stake"] == sum(b["stake"] for b in out["bets"])
    assert out["n_bets"] == len(out["bets"])
    # the pricing chain is present and ordered through the pipeline
    chain = out["runners"][0]["pricing"]
    assert set(chain) == {"score", "p_plackett_luce", "p_calibrated", "p_final"}
    assert 0 <= chain["p_final"] <= 1


def test_attributions_are_kept_for_contenders_and_anything_backed(fitted):
    predictor, test = fitted
    race = test[test["race_id"] == test["race_id"].iloc[0]]
    priced = predictor.predict(race)
    out = explain_race(priced, predictor, BetPolicy(ev_threshold=1.0), 1_000_000, max_explained=2)
    for pos, r in enumerate(out["runners"]):
        if r["decision"]["action"] == "bet" or pos < 2:
            assert r["factors"], (pos, r["entrant_id"])
        else:
            assert r["factors"] == []


# --------------------------------------------------------------------------
# naming: a registration id is not an answer
# --------------------------------------------------------------------------
def test_a_runner_is_named_by_the_number_on_its_saddlecloth():
    assert runner_label(None, 3, "H00918") == "3番"
    assert runner_label("", 3, "H00918") == "3番"


def test_a_name_is_appended_when_the_source_has_one():
    assert runner_label("ディープインパクト", 3, "H00918") == "3番 ディープインパクト"


def test_a_missing_name_is_never_invented():
    """NaN read back from a CSV must not print as the word "nan"."""
    for empty in (None, "", "nan", "None", float("nan")):
        label = runner_label(empty if empty == empty else "nan", 7, "H1")
        assert label == "7番"


def test_the_id_is_the_last_resort_only():
    """Nothing in the schema allows this, but a label must never be blank."""
    assert runner_label(None, None, "H00918") == "H00918"
