"""Read today's card, odds and results from files you drop in ``live_data/``.

This is the route that works without any subscription: whatever tool you are
licensed to use writes these three files, and the rest of the system does not
care where they came from.

    live_data/cards/2026-09-20.csv     one row per runner, race columns repeated
    live_data/odds/2026-09-20.csv      race_id, entrant_id, win_odds
    live_data/results/2026-09-20.csv   race_id, entrant_id, finish_position

Run ``python src/live/paper_trader.py template --date <date>`` to write an
empty card with the right header.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from src.live.providers.base import (
    CARD_ENTRY_COLUMNS,
    CARD_RACE_COLUMNS,
    OPTIONAL_CARD_ENTRY_COLUMNS,
    OPTIONAL_CARD_RACE_COLUMNS,
    LiveProvider,
    ProviderError,
)


def _read(path: Path) -> pd.DataFrame:
    for enc in ("utf-8", "utf-8-sig", "cp932"):
        try:
            return pd.read_csv(path, encoding=enc, dtype={"race_id": str, "entrant_id": str, "jockey_id": str,
                                                          "trainer_id": str})
        except UnicodeDecodeError:
            continue
    raise ProviderError(f"could not decode {path} as UTF-8 or CP932")


class CsvCardProvider(LiveProvider):
    name = "csv"

    def __init__(self, root: str = "live_data/"):
        self.root = Path(root)

    def _path(self, kind: str, date: pd.Timestamp) -> Path:
        return self.root / kind / f"{pd.Timestamp(date):%Y-%m-%d}.csv"

    def fetch_card(self, date: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame]:
        path = self._path("cards", date)
        if not path.exists():
            raise ProviderError(
                f"no card at {path}. Write one there, or run "
                f"`python src/live/paper_trader.py template --date {pd.Timestamp(date):%Y-%m-%d}` for the header.")
        df = _read(path)
        missing = [c for c in CARD_RACE_COLUMNS + CARD_ENTRY_COLUMNS if c not in df.columns and c != "n_runners"]
        if missing:
            raise ProviderError(f"{path} is missing columns: {missing}")
        races = df[[c for c in CARD_RACE_COLUMNS + OPTIONAL_CARD_RACE_COLUMNS
                if c in df.columns]].drop_duplicates("race_id").reset_index(drop=True)
        if "n_runners" not in races.columns:
            races = races.merge(df.groupby("race_id").size().rename("n_runners").reset_index(), on="race_id")
        entries = df[CARD_ENTRY_COLUMNS + [c for c in OPTIONAL_CARD_ENTRY_COLUMNS
                                           if c in df.columns]].reset_index(drop=True)
        return self.validate_card(races, entries)

    def fetch_odds(self, date: pd.Timestamp) -> pd.DataFrame:
        path = self._path("odds", date)
        if not path.exists():
            raise ProviderError(f"no odds at {path}. Expected columns: race_id, entrant_id, win_odds.")
        return self.validate_odds(_read(path))

    def fetch_results(self, date: pd.Timestamp) -> Optional[pd.DataFrame]:
        path = self._path("results", date)
        if not path.exists():
            return None
        df = _read(path)
        if "finish_position" not in df.columns:
            raise ProviderError(f"{path} has no finish_position column")
        df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce").fillna(0).astype(int)
        return df
