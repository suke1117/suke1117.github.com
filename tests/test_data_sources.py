"""Getting real data in must not require a code change.

Every route to real racing data ends at the same question - does this file
have what the model needs - and the answer has to be cheap. These tests fix
that a foreign CSV is wired with a mapping file rather than a patch, and that
the diagnosis says what is missing instead of failing somewhere downstream.
"""
import pandas as pd
import pytest

from src.data.sources.jravan_csv import JRAVanCSVSource, diagnose, load_mapping

RACES = pd.DataFrame({
    "レースID": ["R1", "R2"],
    "開催年月日": ["2024-01-06", "2024-01-06"],
    "競馬場": ["05", "05"],
    "R": [1, 2],
    "距離": [1600, 2000],
    "トラック": ["芝", "ダ"],
    "馬場状態": ["良", "稍重"],
    "条件": ["未勝利", "1勝"],
    "頭数": [2, 2],
})
ENTRIES = pd.DataFrame({
    "レースID": ["R1", "R1", "R2", "R2"],
    "馬ID": ["H1", "H2", "H3", "H4"],
    "馬番": [1, 2, 1, 2],
    "枠番": [1, 2, 1, 2],
    "騎手ID": ["J1", "J2", "J1", "J3"],
    "調教師ID": ["T1", "T2", "T1", "T3"],
    "馬齢": [3, 4, 3, 5],
    "性別": ["牡", "牝", "牡", "セ"],
    "斤量": [55.0, 54.0, 56.0, 57.0],
    "着順": [1, 2, 2, 1],
    "単勝オッズ": [2.5, 3.1, 4.0, 1.8],
})
MAPPING = """
files:
  races: "race*.csv"
  entries: "entry*.csv"
races:
  レースID: race_id
  開催年月日: race_date
  競馬場: venue
  R: race_no
  距離: distance_m
  トラック: surface
  馬場状態: going
  条件: race_class
  頭数: n_runners
entries:
  レースID: race_id
  馬ID: entrant_id
  馬番: post_position
  枠番: draw
  騎手ID: jockey_id
  調教師ID: trainer_id
  馬齢: age
  性別: sex
  斤量: weight_carried
  着順: finish_position
  単勝オッズ: win_odds
"""


@pytest.fixture
def foreign_csv(tmp_path):
    """A CSV that shares no column name with the JV-Data specification."""
    RACES.to_csv(tmp_path / "race_master.csv", index=False)
    ENTRIES.to_csv(tmp_path / "entry_detail.csv", index=False)
    return tmp_path


def test_without_a_mapping_the_check_names_every_missing_column(foreign_csv):
    rep = diagnose(str(foreign_csv))
    assert rep["usable"] is False
    missing = rep["tables"]["races"]["missing_required"]
    assert {"race_id", "race_date", "venue", "distance_m"} <= set(missing)
    # and it shows the headers that are there, so the gap can be closed by eye
    assert "レースID" in rep["tables"]["races"]["unmapped_columns"]


def test_a_mapping_file_is_enough_to_make_a_foreign_csv_usable(foreign_csv):
    (foreign_csv / "mapping.yml").write_text(MAPPING, encoding="utf-8")
    assert diagnose(str(foreign_csv))["usable"] is True
    assert JRAVanCSVSource.has_data(str(foreign_csv))


def test_the_loaded_frames_match_the_canonical_schema(foreign_csv):
    (foreign_csv / "mapping.yml").write_text(MAPPING, encoding="utf-8")
    races, entries = JRAVanCSVSource(str(foreign_csv)).load()
    assert list(races["race_id"]) == ["R1", "R2"]
    assert races["race_date"].dtype.kind == "M"
    # Japanese surface and going spellings translate without a mapping entry:
    # every non-JV-Data export of Japanese racing uses them
    assert list(races["surface"]) == ["turf", "dirt"]
    assert list(races["going"]) == ["good", "yielding"]
    assert list(entries["entrant_id"]) == ["H1", "H2", "H3", "H4"]
    assert list(entries["win_odds"]) == [2.5, 3.1, 4.0, 1.8]


def test_optional_names_are_carried_but_never_required(foreign_csv):
    e = ENTRIES.copy()
    e["馬名"] = ["ウマA", "ウマB", "ウマC", "ウマD"]
    e.to_csv(foreign_csv / "entry_detail.csv", index=False)
    (foreign_csv / "mapping.yml").write_text(MAPPING, encoding="utf-8")
    _, entries = JRAVanCSVSource(str(foreign_csv)).load()
    assert list(entries["entrant_name"]) == ["ウマA", "ウマB", "ウマC", "ウマD"]

    e.drop(columns=["馬名"]).to_csv(foreign_csv / "entry_detail.csv", index=False)
    _, without = JRAVanCSVSource(str(foreign_csv)).load()
    assert "entrant_name" not in without.columns
    assert diagnose(str(foreign_csv))["usable"] is True


def test_a_value_map_can_be_overridden_per_source(foreign_csv):
    r = RACES.copy()
    r["トラック"] = ["T", "D"]
    r.to_csv(foreign_csv / "race_master.csv", index=False)
    (foreign_csv / "mapping.yml").write_text(
        MAPPING + '\nvalues:\n  surface: {"T": turf, "D": dirt}\n', encoding="utf-8")
    races, _ = JRAVanCSVSource(str(foreign_csv)).load()
    assert list(races["surface"]) == ["turf", "dirt"]


def test_a_mapping_with_an_unknown_section_is_refused(foreign_csv):
    """A typo that silently does nothing is worse than an error."""
    (foreign_csv / "mapping.yml").write_text("race:\n  a: race_id\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown sections"):
        load_mapping(str(foreign_csv))


def test_an_empty_directory_is_reported_rather_than_crashed(tmp_path):
    rep = diagnose(str(tmp_path))
    assert rep["usable"] is False
    assert all(t.get("error") for t in rep["tables"].values())
