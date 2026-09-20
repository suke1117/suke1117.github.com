"""Live data providers.

There is no official public JRA API.  The routes that actually exist are:

* **JRA-VAN Data Lab.** - a paid subscription whose JV-Link component is a
  Windows COM object.  This is the sanctioned realtime source, including odds.
* **A file you produce yourself** - export from JV-Link, or any tool you are
  licensed to use, into the CSV layout :class:`CsvCardProvider` reads.
* **Any JSON endpoint you have the right to call** - point
  :class:`HttpJsonProvider` at it with a small mapping file; no code change.

So the system does not hard-code a source.  A provider only has to return the
canonical frames from ``src/data/schema.py``, and everything downstream - the
as-of features, the calibrated probabilities, the Kelly sizing - is unchanged
from the backtest path.  That symmetry is the point: the code that places a
paper bet today is the code the backtest validated.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Tuple

import pandas as pd

#: entry columns a card must carry; the result columns are absent by definition
CARD_ENTRY_COLUMNS = ["race_id", "entrant_id", "post_position", "draw", "jockey_id", "trainer_id", "age", "sex",
                      "weight_carried", "body_weight", "body_weight_diff"]
CARD_RACE_COLUMNS = ["race_id", "race_date", "venue", "race_no", "distance_m", "surface", "going", "race_class",
                     "n_runners"]
#: carried through when the source has them. ``organizer`` is a trained feature
#: once the data spans more than one organization, so dropping it here would
#: make today's card disagree with the model.
OPTIONAL_CARD_RACE_COLUMNS = ["organizer"]
#: display-only, and never fed to the model. Dropping these would leave the
#: screens naming horses by 血統登録番号, which reads as nothing to a person.
OPTIONAL_CARD_ENTRY_COLUMNS = ["entrant_name", "jockey_name"]


class ProviderError(RuntimeError):
    """Raised when a provider cannot serve a request, with what to do about it."""


class LiveProvider(ABC):
    """Supplies today's card, its odds, and (later) its results."""

    name: str = "base"

    @abstractmethod
    def fetch_card(self, date: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Return (races, entries) for ``date``. Entries carry no result columns."""

    @abstractmethod
    def fetch_odds(self, date: pd.Timestamp) -> pd.DataFrame:
        """Return race_id, entrant_id, win_odds as of now.

        Odds move until the off, so the caller records the time it read them.
        """

    def fetch_results(self, date: pd.Timestamp) -> Optional[pd.DataFrame]:
        """Return race_id, entrant_id, finish_position (and win_odds if known).

        ``None`` means the provider cannot settle; the ledger then waits.
        """
        return None

    # -- shared validation -------------------------------------------------
    @staticmethod
    def validate_card(races: pd.DataFrame, entries: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        missing_r = [c for c in CARD_RACE_COLUMNS if c not in races.columns]
        missing_e = [c for c in CARD_ENTRY_COLUMNS if c not in entries.columns]
        if missing_r or missing_e:
            raise ProviderError(f"card is missing columns: races={missing_r} entries={missing_e}")
        if races.empty or entries.empty:
            raise ProviderError("card is empty")
        races = races.copy()
        races["race_date"] = pd.to_datetime(races["race_date"])
        entries = entries.copy()
        for c in ("race_id", "entrant_id", "jockey_id", "trainer_id"):
            entries[c] = entries[c].astype(str)
        races["race_id"] = races["race_id"].astype(str)
        unknown = set(entries["race_id"]) - set(races["race_id"])
        if unknown:
            raise ProviderError(f"entries reference races not on the card: {sorted(unknown)[:5]}")
        if entries.duplicated(["race_id", "entrant_id"]).any():
            raise ProviderError("the card lists the same entrant twice in one race")
        return races, entries

    @staticmethod
    def validate_odds(odds: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in ("race_id", "entrant_id", "win_odds") if c not in odds.columns]
        if missing:
            raise ProviderError(f"odds are missing columns: {missing}")
        odds = odds.copy()
        odds["race_id"] = odds["race_id"].astype(str)
        odds["entrant_id"] = odds["entrant_id"].astype(str)
        odds["win_odds"] = pd.to_numeric(odds["win_odds"], errors="coerce")
        bad = odds["win_odds"].isna() | (odds["win_odds"] <= 1.0)
        if bad.all():
            raise ProviderError("no usable win odds in the response (decimal odds must exceed 1.0)")
        return odds
