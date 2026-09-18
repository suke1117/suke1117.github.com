"""Abstract data source.

To add a new sport or vendor, subclass ``DataSource`` and implement ``load``.
The returned frames must satisfy ``src.data.schema``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple

import pandas as pd

from src.common.sport import SportSpec
from src.data.schema import validate_frames


class DataSource(ABC):
    sport: SportSpec

    @abstractmethod
    def load(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Return (races, entries) in the canonical schema."""

    def load_validated(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        races, entries = self.load()
        races = races.copy()
        races["race_date"] = pd.to_datetime(races["race_date"])
        validate_frames(races, entries)
        # entries inherit the date for convenient sorting
        entries = entries.merge(races[["race_id", "race_date"]], on="race_id", how="inner", validate="m:1")
        return races.sort_values(["race_date", "race_id"]).reset_index(drop=True), entries.sort_values(
            ["race_date", "race_id", "post_position"]
        ).reset_index(drop=True)
