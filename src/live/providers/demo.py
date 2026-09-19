"""Replay a day out of the stored history as if it were today.

This exists so the whole live path - card, odds, sizing, ledger, settlement -
can be exercised end to end before any subscription is in place, and so the
tests have something deterministic to run against.  The results are withheld
from the card and only handed back by :meth:`fetch_results`, which is what a
real day looks like: you size the bet before you know the answer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from src.live.providers.base import CARD_ENTRY_COLUMNS, CARD_RACE_COLUMNS, LiveProvider, ProviderError


class DemoProvider(LiveProvider):
    name = "demo"

    def __init__(self, processed_dir: str = "processed/", odds_drift: float = 0.0):
        self.dir = Path(processed_dir)
        self.odds_drift = odds_drift
        rp, ep = self.dir / "races.csv", self.dir / "entries.csv"
        if not rp.exists() or not ep.exists():
            raise ProviderError(f"{rp} / {ep} not found; run src/data/preprocess.py first")
        self.races = pd.read_csv(rp, dtype={"race_id": str}, parse_dates=["race_date"])
        self.entries = pd.read_csv(ep, dtype={"race_id": str, "entrant_id": str, "jockey_id": str, "trainer_id": str},
                                   parse_dates=["race_date"])

    def available_dates(self) -> pd.Series:
        return self.races["race_date"].drop_duplicates().sort_values()

    def latest_date(self) -> pd.Timestamp:
        return self.races["race_date"].max()

    def _day(self, date: pd.Timestamp):
        date = pd.Timestamp(date).normalize()
        races = self.races[self.races["race_date"] == date]
        if races.empty:
            raise ProviderError(f"no stored racing on {date:%Y-%m-%d}; latest is {self.latest_date():%Y-%m-%d}")
        entries = self.entries[self.entries["race_id"].isin(set(races["race_id"]))]
        return races, entries

    def fetch_card(self, date: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame]:
        races, entries = self._day(date)
        return self.validate_card(races[CARD_RACE_COLUMNS].copy(), entries[CARD_ENTRY_COLUMNS].copy())

    def fetch_odds(self, date: pd.Timestamp) -> pd.DataFrame:
        _, entries = self._day(date)
        odds = entries[["race_id", "entrant_id", "win_odds"]].copy()
        if self.odds_drift:
            # pre-off odds differ from the final ones; shift them to make that visible
            rng = __import__("numpy").random.default_rng(int(pd.Timestamp(date).value % (2 ** 32)))
            factor = 1.0 + rng.normal(0, self.odds_drift, len(odds))
            odds["win_odds"] = (odds["win_odds"] * factor).clip(lower=1.1).round(1)
        return self.validate_odds(odds)

    def fetch_results(self, date: pd.Timestamp) -> Optional[pd.DataFrame]:
        _, entries = self._day(date)
        return entries[["race_id", "entrant_id", "finish_position", "win_odds"]].copy()
