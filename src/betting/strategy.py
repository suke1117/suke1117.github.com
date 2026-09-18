"""Race-level bet selection.

For win tickets in one race the outcomes are mutually exclusive, so the
optimal Kelly allocation is *not* the independent single-bet formula applied
to each horse.  We implement the exact multi-outcome solution (Thorp 1997 /
Smoczynski & Tomkins): sort candidates by expected return ``p_i * o_i``,
extend the bet set while

    p_k * o_k > R(S) = (1 - sum_{i in S} p_i) / (1 - sum_{i in S} 1/o_i)

and stake ``f_i = p_i - R(S) / o_i``.  The fractions are then scaled by
``alpha`` (<= MAX_KELLY_ALPHA) and capped per ticket and per race.

Only positive-EV outcomes (``p_i * o_i >= ev_threshold``) are ever considered.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from src.betting.kelly_calculator import validate_alpha
from src.common.config import (
    BET_UNIT_YEN,
    DEFAULT_EV_THRESHOLD,
    DEFAULT_KELLY_ALPHA,
    MAX_RACE_EXPOSURE,
    MAX_SINGLE_BET_FRACTION,
    MAX_STAKE_YEN,
    MIN_WIN_PROB_TO_BET,
)


@dataclass(frozen=True)
class BetPolicy:
    alpha: float = DEFAULT_KELLY_ALPHA
    ev_threshold: float = DEFAULT_EV_THRESHOLD
    max_single_fraction: float = MAX_SINGLE_BET_FRACTION
    max_race_exposure: float = MAX_RACE_EXPOSURE
    min_prob: float = MIN_WIN_PROB_TO_BET
    bet_unit: int = BET_UNIT_YEN
    max_bets_per_race: int = 3
    simultaneous_kelly: bool = True
    max_stake_yen: float = MAX_STAKE_YEN

    def __post_init__(self) -> None:
        validate_alpha(self.alpha)
        if self.max_race_exposure > MAX_RACE_EXPOSURE + 1e-12:
            raise ValueError(f"max_race_exposure {self.max_race_exposure} exceeds hard cap {MAX_RACE_EXPOSURE}")
        if self.max_single_fraction > MAX_SINGLE_BET_FRACTION + 1e-12:
            raise ValueError(f"max_single_fraction {self.max_single_fraction} exceeds hard cap {MAX_SINGLE_BET_FRACTION}")


@dataclass(frozen=True)
class Bet:
    race_id: str
    entrant_id: str
    bet_type: str
    prob: float
    odds: float
    ev: float
    fraction: float
    stake: float


def multi_outcome_kelly(p: np.ndarray, o: np.ndarray) -> np.ndarray:
    """Full-Kelly fractions for mutually exclusive outcomes (win market)."""
    p, o = np.asarray(p, dtype=float), np.asarray(o, dtype=float)
    n = p.shape[0]
    f = np.zeros(n)
    order = np.argsort(-(p * o))
    sum_p, sum_inv = 0.0, 0.0
    chosen: List[int] = []
    for k in order:
        if p[k] <= 0 or o[k] <= 1.0:
            break
        r = (1.0 - sum_p) / (1.0 - sum_inv) if sum_inv < 1.0 else np.inf
        if p[k] * o[k] <= r:
            break
        chosen.append(k)
        sum_p += p[k]
        sum_inv += 1.0 / o[k]
    if not chosen or sum_inv >= 1.0:
        return f
    r = max(0.0, (1.0 - sum_p) / (1.0 - sum_inv))
    for k in chosen:
        f[k] = max(0.0, p[k] - r / o[k])
    return f


def select_win_bets(race: pd.DataFrame, bankroll: float, policy: BetPolicy = BetPolicy(),
                    prob_col: str = "p_win", odds_col: str = "win_odds", sizing_bankroll: Optional[float] = None) -> List[Bet]:
    """Return the list of win bets for one race (may be empty).

    ``bankroll`` is the cash actually available; ``sizing_bankroll`` (defaults to
    ``bankroll``) is the base for Kelly fractions - pass the initial bankroll to
    simulate flat, non-compounding staking.
    """
    if bankroll < policy.bet_unit:
        return []
    base = min(bankroll, sizing_bankroll if sizing_bankroll is not None else bankroll)
    p = race[prob_col].to_numpy(dtype=float)
    o = race[odds_col].to_numpy(dtype=float)
    ev = p * o
    eligible = (ev >= policy.ev_threshold) & (p >= policy.min_prob) & (o > 1.0) & np.isfinite(o)
    if not eligible.any():
        return []

    if policy.simultaneous_kelly:
        full = multi_outcome_kelly(np.where(eligible, p, 0.0), np.where(eligible, o, 1.0))
    else:
        full = np.where(eligible, p - (1 - p) / np.clip(o - 1, 1e-9, None), 0.0)
    full = np.where(eligible, np.clip(full, 0.0, None), 0.0)

    frac = np.minimum(policy.alpha * full, policy.max_single_fraction)
    total = frac.sum()
    if total > policy.max_race_exposure:
        frac *= policy.max_race_exposure / total

    idx = np.argsort(-frac)[: policy.max_bets_per_race]
    bets: List[Bet] = []
    for i in idx:
        if frac[i] <= 0:
            continue
        stake = int(min(frac[i] * base, policy.max_stake_yen) // policy.bet_unit) * policy.bet_unit
        if stake < policy.bet_unit:
            continue
        bets.append(Bet(str(race["race_id"].iloc[i]), str(race["entrant_id"].iloc[i]), "win", float(p[i]), float(o[i]),
                        float(ev[i]), float(frac[i]), float(stake)))
    return bets


def settle_win_bet(bet: Bet, finish_position: int, payout_odds: Optional[float] = None) -> float:
    """Profit (can be negative) of a win bet given the final finishing position."""
    odds = bet.odds if payout_odds is None else payout_odds
    return bet.stake * (odds - 1.0) if finish_position == 1 else -bet.stake
