"""Sport-specific specifications.

Everything that differs between horse racing, keirin and kyotei is confined to
this module so the modelling / betting / backtest layers stay sport-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class SportSpec:
    key: str
    name_ja: str
    max_entrants: int
    min_entrants: int
    bet_unit_yen: int
    win_takeout: float
    bet_types: List[str] = field(default_factory=lambda: ["win"])


HORSE_RACING = SportSpec(
    key="horse",
    name_ja="競馬 (JRA)",
    max_entrants=18,
    min_entrants=5,
    bet_unit_yen=100,
    win_takeout=0.20,
    bet_types=["win", "place", "quinella", "exacta", "trio", "trifecta"],
)

KEIRIN = SportSpec(
    key="keirin",
    name_ja="競輪",
    max_entrants=9,
    min_entrants=5,
    bet_unit_yen=100,
    win_takeout=0.25,
    bet_types=["win", "quinella", "exacta", "trio", "trifecta"],
)

KYOTEI = SportSpec(
    key="kyotei",
    name_ja="競艇 (ボートレース)",
    max_entrants=6,
    min_entrants=4,
    bet_unit_yen=100,
    win_takeout=0.25,
    bet_types=["win", "quinella", "exacta", "trio", "trifecta"],
)

SPORTS: Dict[str, SportSpec] = {s.key: s for s in (HORSE_RACING, KEIRIN, KYOTEI)}


def get_sport(key: str) -> SportSpec:
    try:
        return SPORTS[key]
    except KeyError as exc:  # pragma: no cover - trivial
        raise ValueError(f"unknown sport '{key}'. choose from {list(SPORTS)}") from exc
