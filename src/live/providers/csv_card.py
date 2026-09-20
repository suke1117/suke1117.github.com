"""Read today's card, odds and results from files you drop in ``live_data/``.

This is the route that works without any subscription: whatever tool you are
licensed to use writes these three files, and the rest of the system does not
care where they came from.

    live_data/cards/2026-09-20.csv     one row per runner, race columns repeated
    live_data/odds/2026-09-20.csv      race_id, entrant_id, win_odds
    live_data/results/2026-09-20.csv   race_id, entrant_id, finish_position

Run ``python src/live/paper_trader.py template --date <date>`` to write an
empty card with the right header.

If the files come from somewhere that names its columns differently - a site's
own CSV export, a spreadsheet you keep by hand - put a ``mapping.yml`` in
``live_data/`` and the same loader reads them. It is the same file format the
history loader uses, so a source only has to be described once:

    entries:
      馬番: post_position
      馬名: entrant_name
    values:
      surface: {"芝": turf, "ダ": dirt}

Retyping tomorrow's card because the headers differ is the kind of friction
that stops a system being used at all, which is why this is here and not a
"later" item.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from src.data.sources.jravan_csv import (
    DEFAULT_VALUE_MAPS,
    load_mapping,
)
from src.live.providers.base import (
    CARD_ENTRY_COLUMNS,
    CARD_RACE_COLUMNS,
    OPTIONAL_CARD_ENTRY_COLUMNS,
    OPTIONAL_CARD_RACE_COLUMNS,
    LiveProvider,
    ProviderError,
)


def _apply_mapping(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Rename columns and translate coded values, per live_data/mapping.yml."""
    names = {}
    for section in ("races", "entries"):
        names.update({str(k): str(v) for k, v in (cfg.get(section) or {}).items()})
    if names:
        df = df.rename(columns={c: names[c] for c in df.columns if c in names})
    values = {k: dict(v) for k, v in DEFAULT_VALUE_MAPS.items()}
    for field, pairs in (cfg.get("values") or {}).items():
        values.setdefault(field, {}).update({str(k): str(v) for k, v in (pairs or {}).items()})
    for field, table in values.items():
        if field in df.columns:
            df[field] = df[field].astype(str).str.strip().map(lambda v, t=table: t.get(v, v))
    return df


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
        self.mapping = load_mapping(str(self.root))

    def _path(self, kind: str, date: pd.Timestamp) -> Path:
        return self.root / kind / f"{pd.Timestamp(date):%Y-%m-%d}.csv"

    def fetch_card(self, date: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame]:
        path = self._path("cards", date)
        if not path.exists():
            raise ProviderError(
                f"no card at {path}. Write one there, or run "
                f"`python src/live/paper_trader.py template --date {pd.Timestamp(date):%Y-%m-%d}` for the header.")
        df = _apply_mapping(_read(path), self.mapping)
        missing = [c for c in CARD_RACE_COLUMNS + CARD_ENTRY_COLUMNS if c not in df.columns and c != "n_runners"]
        if missing:
            raise ProviderError(
                f"{path} is missing columns: {missing}. "
                f"If they are there under other headings, map them in {self.root / 'mapping.yml'}. "
                f"Present: {list(df.columns)[:20]}")
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
        return self.validate_odds(_apply_mapping(_read(path), self.mapping))

    def fetch_results(self, date: pd.Timestamp) -> Optional[pd.DataFrame]:
        path = self._path("results", date)
        if not path.exists():
            return None
        df = _apply_mapping(_read(path), self.mapping)
        if "finish_position" not in df.columns:
            raise ProviderError(
                f"{path} has no finish_position column. "
                f"If it is there under another heading, map it in {self.root / 'mapping.yml'}. "
                f"Present: {list(df.columns)[:20]}")
        df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce").fillna(0).astype(int)
        return df
