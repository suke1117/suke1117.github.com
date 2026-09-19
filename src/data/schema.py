"""Canonical, sport-agnostic data schema.

Every ``DataSource`` adapter (JRA-VAN CSV, synthetic, future keirin/kyotei)
must return two pandas DataFrames with at least these columns.  Column names
are deliberately generic ("entrant" rather than "horse") so the same code
serves all pari-mutuel sports.

RACES  - one row per race
ENTRIES - one row per (race, entrant) with the *post-race* result columns.
          Result columns are only ever used to build labels and to settle
          paper bets; the feature builder must never read them for the
          current race (see ``src/data/features.py``).
"""
from __future__ import annotations

from typing import Dict, List

RACE_COLUMNS: Dict[str, str] = {
    "race_id": "str",          # unique id, e.g. JV-Data RA key YYYYMMDDJJKKNNRR
    "race_date": "datetime",   # kaisai date
    "venue": "str",            # 場コード
    "race_no": "int",
    "distance_m": "int",
    "surface": "str",          # turf / dirt
    "going": "str",            # 良/稍重/重/不良 -> good/yielding/soft/heavy
    "race_class": "str",       # 新馬/未勝利/1勝/2勝/3勝/OP/G3/G2/G1 (coarse)
    "n_runners": "int",
}

#: Optional race columns. ``organizer`` separates JRA from NAR (地方競馬): the
#: two have different takeout, class ladders and field sizes, so a model trained
#: on both needs to know which it is looking at.
OPTIONAL_RACE_COLUMNS: Dict[str, str] = {"organizer": "str"}
DEFAULT_ORGANIZER = "JRA"

ENTRY_COLUMNS: Dict[str, str] = {
    "race_id": "str",
    "entrant_id": "str",       # 血統登録番号 (horse), 選手登録番号 (keirin/kyotei)
    "post_position": "int",    # 馬番 / 車番 / 艇番
    "draw": "int",             # 枠番
    "jockey_id": "str",        # 騎手 (keirin/kyotei: same as entrant_id)
    "trainer_id": "str",       # 調教師
    "age": "int",
    "sex": "str",
    "weight_carried": "float", # 斤量 (kg)
    "body_weight": "float",    # 馬体重 (kg), NaN if unknown
    "body_weight_diff": "float",
    # --- results / market (post-race, label-only) ---
    "finish_position": "int",  # 1 = winner; 0/NaN = did not finish
    "finish_time_sec": "float",
    "win_odds": "float",       # final 単勝 decimal odds (e.g. 10.0 -> 1000 yen return per 100)
    "place_odds": "float",     # optional
    "popularity": "int",       # 人気順 (optional)
}

#: Optional entry columns. Names are display-only and never reach the model: a
#: 血統登録番号 identifies a horse but tells a reader nothing, so every screen
#: shows the name when the source carries one and the 馬番 when it does not.
#: They are never invented - a made-up name on a page of real-looking numbers
#: reads as a real horse.
OPTIONAL_ENTRY_COLUMNS: Dict[str, str] = {"entrant_name": "str", "jockey_name": "str"}

RESULT_COLUMNS: List[str] = ["finish_position", "finish_time_sec", "win_odds", "place_odds", "popularity"]

# columns that must NEVER appear in the model feature matrix
FORBIDDEN_FEATURE_COLUMNS = (set(RESULT_COLUMNS) | set(OPTIONAL_ENTRY_COLUMNS)
                              | {"relevance", "label", "race_date", "race_id", "entrant_id"})


def validate_frames(races, entries) -> None:
    missing_r = [c for c in RACE_COLUMNS if c not in races.columns]
    missing_e = [c for c in ENTRY_COLUMNS if c not in entries.columns and c not in ("place_odds", "popularity")]
    if missing_r:
        raise ValueError(f"races frame missing columns: {missing_r}")
    if missing_e:
        raise ValueError(f"entries frame missing columns: {missing_e}")
    if races["race_id"].duplicated().any():
        raise ValueError("duplicate race_id in races frame")
    if entries.duplicated(["race_id", "entrant_id"]).any():
        raise ValueError("duplicate (race_id, entrant_id) in entries frame")
