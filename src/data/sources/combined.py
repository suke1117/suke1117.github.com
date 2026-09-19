"""Merge several racing organizations into one training set.

Weekly compounding is per-bet log growth times bets per week, and JRA only
races two days a week.  Adding 地方競馬 roughly triples the race days at the
same per-bet edge, which moves the weekly ceiling far more than any realistic
improvement to the edge itself.

Merging is not concatenation, though. Two guards matter:

* **race_id collisions.** Venue codes are disjoint by construction, but a
  source with its own numbering could still collide, so the merge refuses
  rather than silently dropping races.
* **an entrant in two places on one day.** A horse cannot be at two tracks at
  once, and the as-of feature builder rejects it anyway; catching it here
  points at the source that is wrong instead of failing later.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import pandas as pd

from src.common.sport import HORSE_RACING
from src.data.schema import DEFAULT_ORGANIZER
from src.data.sources.base import DataSource


class CombinedSource(DataSource):
    sport = HORSE_RACING

    def __init__(self, sources: Sequence[DataSource]):
        if not sources:
            raise ValueError("CombinedSource needs at least one source")
        self.sources = list(sources)

    def load(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        race_frames: List[pd.DataFrame] = []
        entry_frames: List[pd.DataFrame] = []
        for src in self.sources:
            races, entries = src.load()
            races = races.copy()
            if "organizer" not in races.columns:
                races["organizer"] = getattr(getattr(src, "sport", None), "name_ja", DEFAULT_ORGANIZER)
            race_frames.append(races)
            entry_frames.append(entries)

        races = pd.concat(race_frames, ignore_index=True)
        entries = pd.concat(entry_frames, ignore_index=True)

        dup = races["race_id"].duplicated()
        if dup.any():
            clash = races.loc[dup, "race_id"].unique()[:5]
            raise ValueError(f"race_id collides across sources: {list(clash)}. Give each organization its own "
                             "venue codes or id prefix before merging.")

        joined = entries.merge(races[["race_id", "race_date", "organizer"]], on="race_id", how="left",
                               suffixes=("", "_race"))
        date_col = "race_date_race" if "race_date_race" in joined.columns else "race_date"
        same_day = joined.duplicated(["entrant_id", date_col], keep=False)
        if same_day.any():
            offenders = joined.loc[same_day, ["entrant_id", date_col, "organizer"]].head(4)
            raise ValueError("the same entrant runs twice on one day across organizations:\n"
                             f"{offenders.to_string(index=False)}")
        return races, entries
