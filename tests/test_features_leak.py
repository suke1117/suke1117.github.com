"""Leak detection: every feature must be reproducible from strictly earlier races."""
import numpy as np
import pytest
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
    full, cols, _ = build_features(races, entries)
    r2, e2 = races[races["race_date"] <= cutoff], entries[entries["race_date"] <= cutoff]
    trunc, cols2, _ = build_features(r2, e2)
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


# --------------------------------------------------------------------------
# conditional / pair / switch families
# --------------------------------------------------------------------------
def test_jockey_stats_exclude_the_same_race_day(small_table):
    """A jockey rides several races a day; none of them may feed each other."""
    table, _ = small_table
    rows = table[table["jky_starts"] > 20].sample(25, random_state=3)
    for _, row in rows.iterrows():
        past = table[(table["jockey_id"] == row["jockey_id"]) & (table["race_date"] < row["race_date"])]
        assert row["jky_starts"] == len(past)
        same_day = table[(table["jockey_id"] == row["jockey_id"]) & (table["race_date"] == row["race_date"])]
        assert len(same_day) >= 1  # the row itself, and usually siblings, all excluded above


def test_conditional_rate_is_shrunk_toward_its_parent(small_table):
    """A thin cell must sit between its own raw rate and the parent rate."""
    from src.data.features import SHRINK_K

    table, _ = small_table
    rows = table[(table["entc_venue_starts"] > 0) & (table["ent_starts"] > 3)].sample(25, random_state=5)
    for _, row in rows.iterrows():
        past = table[(table["entrant_id"] == row["entrant_id"]) & (table["race_date"] < row["race_date"])]
        cell = past[past["race_id"].str[8:10] == row["race_id"][8:10]]
        assert len(cell) == row["entc_venue_starts"]
        norm = ((cell["finish_position"] - 1) / (cell["n_runners"] - 1)).mean()
        parent = row["ent_avg_norm_pos"]
        k = SHRINK_K["ent_cond"]
        expected = (norm * len(cell) + k * parent) / (len(cell) + k)
        assert row["entc_venue_norm_pos"] == pytest.approx(expected, abs=1e-6)
        # and it must never leave the interval spanned by the two inputs
        assert min(norm, parent) - 1e-9 <= row["entc_venue_norm_pos"] <= max(norm, parent) + 1e-9


def test_pair_features_count_only_earlier_rides(small_table):
    table, _ = small_table
    rows = table[table["pair_starts"] > 1].sample(20, random_state=7)
    for _, row in rows.iterrows():
        past = table[(table["entrant_id"] == row["entrant_id"]) & (table["jockey_id"] == row["jockey_id"])
                     & (table["race_date"] < row["race_date"])]
        assert row["pair_starts"] == len(past)


def test_switch_flags_match_the_previous_start(small_table):
    table, _ = small_table
    t = table.sort_values(["entrant_id", "race_date"])
    prev_j = t.groupby("entrant_id")["jockey_id"].shift(1)
    same = (t["jockey_id"] == prev_j).where(prev_j.notna())
    assert t["sw_same_jockey"].equals(same.astype(float))
    first_start = table[table["ent_starts"] == 0]
    assert first_start["sw_first_time_pair"].eq(1.0).all()


def test_feature_groups_partition_the_feature_list(small_table, small_groups):
    _, cols = small_table
    flat = [c for g in small_groups.values() for c in g]
    assert sorted(flat) == sorted(cols), "every feature belongs to exactly one group"
    assert {"jockey_cond", "pair", "switch", "entrant_cond"} <= set(small_groups)
