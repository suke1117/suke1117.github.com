"""Call a JSON endpoint you have the right to call, described by a config file.

Nothing about the source is hard-coded.  You write a small YAML file saying
where to GET, where the list of rows sits in the response, and which source
key maps to each canonical column; this reads it.  That keeps a licensing or
terms-of-service decision with the operator, where it belongs, and means a new
source is a config change rather than a code change.

    # live_data/provider.yml
    card:
      url: "https://example.invalid/cards?date={date}"
      root: "data.races"            # dotted path to the list of runners
      fields:                       # canonical: source key
        race_id: raceId
        race_date: date
        venue: courseCode
        ...
    odds:
      url: "https://example.invalid/odds?date={date}"
      root: "data"
      fields: {race_id: raceId, entrant_id: horseId, win_odds: winOdds}
    headers:
      Authorization: "Bearer ${MY_TOKEN}"   # ${VAR} reads the environment

``{date}`` is replaced with the requested date (``%Y-%m-%d`` by default, or
``date_format`` if you set one).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

from src.live.providers.base import (
    CARD_ENTRY_COLUMNS,
    CARD_RACE_COLUMNS,
    OPTIONAL_CARD_RACE_COLUMNS,
    LiveProvider,
    ProviderError,
)

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value: str) -> str:
    return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _dig(payload: Any, path: str) -> Any:
    node = payload
    for part in [p for p in path.split(".") if p]:
        if isinstance(node, list):
            raise ProviderError(f"root path '{path}' walks into a list at '{part}'")
        if not isinstance(node, dict) or part not in node:
            raise ProviderError(f"root path '{path}' not found in the response (stopped at '{part}')")
        node = node[part]
    if not isinstance(node, list):
        raise ProviderError(f"root path '{path}' does not point at a list")
    return node


class HttpJsonProvider(LiveProvider):
    name = "http"

    def __init__(self, config_path: str = "live_data/provider.yml", timeout: int = 20):
        path = Path(config_path)
        if not path.exists():
            raise ProviderError(f"{path} not found. See the module docstring for the layout.")
        self.cfg: Dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.timeout = timeout
        for section in ("card", "odds"):
            if section not in self.cfg:
                raise ProviderError(f"{path} has no '{section}' section")

    def _get(self, section: str, date: pd.Timestamp) -> List[Dict]:
        spec = self.cfg[section]
        fmt = spec.get("date_format", self.cfg.get("date_format", "%Y-%m-%d"))
        url = _expand(str(spec["url"])).replace("{date}", pd.Timestamp(date).strftime(fmt))
        headers = {k: _expand(str(v)) for k, v in (self.cfg.get("headers") or {}).items()}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - operator-supplied URL
                payload = json.loads(r.read().decode(r.headers.get_content_charset() or "utf-8"))
        except Exception as exc:  # pragma: no cover - network shape varies
            raise ProviderError(f"{section} request to {url} failed: {exc}") from exc
        return _dig(payload, spec.get("root", ""))

    @staticmethod
    def _frame(rows: List[Dict], fields: Dict[str, str], required: List[str]) -> pd.DataFrame:
        if not rows:
            raise ProviderError("the endpoint returned no rows")
        missing = [c for c in required if c not in fields]
        if missing:
            raise ProviderError(f"provider.yml does not map these canonical columns: {missing}")
        out = pd.DataFrame({canon: [r.get(src) for r in rows] for canon, src in fields.items()})
        return out

    def fetch_card(self, date: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame]:
        spec = self.cfg["card"]
        need = [c for c in CARD_RACE_COLUMNS + CARD_ENTRY_COLUMNS if c != "n_runners"]
        df = self._frame(self._get("card", date), spec.get("fields", {}), need)
        races = df[[c for c in CARD_RACE_COLUMNS + OPTIONAL_CARD_RACE_COLUMNS
                if c in df.columns]].drop_duplicates("race_id").reset_index(drop=True)
        if "n_runners" not in races.columns:
            races = races.merge(df.groupby("race_id").size().rename("n_runners").reset_index(), on="race_id")
        return self.validate_card(races, df[CARD_ENTRY_COLUMNS].reset_index(drop=True))

    def fetch_odds(self, date: pd.Timestamp) -> pd.DataFrame:
        spec = self.cfg["odds"]
        df = self._frame(self._get("odds", date), spec.get("fields", {}), ["race_id", "entrant_id", "win_odds"])
        return self.validate_odds(df)

    def fetch_results(self, date: pd.Timestamp) -> Optional[pd.DataFrame]:
        if "results" not in self.cfg:
            return None
        spec = self.cfg["results"]
        df = self._frame(self._get("results", date), spec.get("fields", {}),
                         ["race_id", "entrant_id", "finish_position"])
        df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce").fillna(0).astype(int)
        return df
