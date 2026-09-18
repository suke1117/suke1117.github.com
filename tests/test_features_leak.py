"""Leak detection: every feature must be reproducible from strictly earlier races."""
import numpy as np
import pandas as pd

from src.data.features import assert_no_forbidden, build_features
from src.data.schema import FORBIDDEN_FEATURE_COLUMNS


def test_no_forbidden_feature_columns(small_table):
    _, feature_cols = small_table
    assert_no_forbidden(feature_cols)
    assert not (set(feature_cols) & FORBIDDEN_FEATURE_COLUMNS)


def test_entrant_history_uses_only_past_races(small_table):
    table, _ = small_table
    rng = np.random.default_rng(0)
    rows = table[table["ent_starts"] >= 3].sample(40, random_state=1)
    for _, row in rows.iterrows():
        past = table[(table["entrant_id"] == row["entrant_id"]) & (table["race_date"] < row["race_date"])]
        assert len(past) == row["ent_starts"]
        assert np.isclose(row["ent_win_rate"], (past["finish_position"] == 1).mean())
        assert np.isclose(row["ent_last_pos"], past.sort_values("race_date")["finish_position"].iloc[-1])
        norm = (past["finish_position"] - 1) / (past["n_runners"] - 1)
        assert np.isclose(row["ent_avg_norm_pos"], norm.mean())


def test_first_start_has_no_history(small_table):
    table, _ = small_table
    first = table[table["ent_starts"] == 0]
    assert len(first) > 0
    assert first["ent_win_rate"].isna().all()
    assert first["ent_last_pos"].isna().all()
    assert first["ent_days_since"].isna().all()


def test_future_results_do_not_change_past_features(small_raw):
    """Truncating the future must leave features of earlier races byte-identical."""
    races, entries = small_raw
    cutoff = pd.Timestamp("2019-12-31")
    full, cols = build_features(races, entries)
    r2, e2 = races[races["race_date"] <= cutoff], entries[entries["race_date"] <= cutoff]
    trunc, cols2 = build_features(r2, e2)
    assert cols == cols2
    a = full[full["race_date"] <= cutoff].reset_index(drop=True)
    b = trunc.reset_index(drop=True)
    pd.testing.assert_frame_equal(a[cols].astype(object), b[cols].astype(object))


def test_relevance_label_is_rank_based(small_table):
    table, _ = small_table
    g = table.groupby("race_id")
    assert (g["relevance"].max() == (g["n_runners"].first() - 1).clip(upper=17)).all()
    winners = table[table["finish_position"] == 1]
    assert (winners["relevance"] == (winners["n_runners"] - 1).clip(upper=17)).all()


def test_same_day_double_run_is_rejected(small_raw):
    races, entries = small_raw
    e = entries.copy()
    dup = e.iloc[[0]].copy()
    other_race = races[(races["race_date"] == dup["race_date"].iloc[0]) & (races["race_id"] != dup["race_id"].iloc[0])].iloc[0]
    dup["race_id"] = other_race["race_id"]
    dup["post_position"] = 99
    e = pd.concat([e, dup], ignore_index=True)
    import pytest
    with pytest.raises(ValueError, match="same date"):
        build_features(races, e)
