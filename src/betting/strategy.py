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

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.betting.kelly_calculator import validate_alpha
from src.models.plackett_luce import place_probs, quinella_probs
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
    ticket_types: Tuple[str, ...] = ("win",)
    takeout: float = 0.20

    def __post_init__(self) -> None:
        validate_alpha(self.alpha)
        if self.max_race_exposure > MAX_RACE_EXPOSURE + 1e-12:
            raise ValueError(f"max_race_exposure {self.max_race_exposure} exceeds hard cap {MAX_RACE_EXPOSURE}")
        if self.max_single_fraction > MAX_SINGLE_BET_FRACTION + 1e-12:
            raise ValueError(f"max_single_fraction {self.max_single_fraction} exceeds hard cap {MAX_SINGLE_BET_FRACTION}")
        unknown = [t for t in self.ticket_types if t not in ("win", "place", "quinella")]
        if unknown:
            raise ValueError(f"unknown ticket types {unknown}; choose from win, place, quinella")
        if not self.ticket_types:
            raise ValueError("a policy with no ticket types can never bet")


#: how many finishers a 複勝 ticket pays on, by field size (JRA rule)
def place_payout_depth(n_runners: int) -> int:
    if n_runners >= 8:
        return 3
    if n_runners >= 5:
        return 2
    return 1


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
    legs: Tuple[str, ...] = ()

    @property
    def selection(self) -> Tuple[str, ...]:
        return self.legs or (self.entrant_id,)


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


def select_bets(race: pd.DataFrame, bankroll: float, policy: "BetPolicy" = None, prob_col: str = "p_win",
                odds_col: str = "win_odds", sizing_bankroll: Optional[float] = None,
                run_down: Tuple[float, float] = (1.0, 1.0)) -> List[Bet]:
    """Tickets across every enabled market for one race.

    Win tickets in a race are mutually exclusive, so they get the exact
    simultaneous Kelly solution. Place and quinella tickets overlap each other
    and the win market, and the exact joint solution is a convex program this
    does not solve; each is sized independently and the whole set is then
    scaled to the race exposure cap. That is conservative in the direction that
    matters - it can under-stake, never over-stake the cap - but it is an
    approximation, and a race where many overlapping tickets fire will be
    sized below what a joint solution would allow.
    """
    policy = policy or BetPolicy()
    if bankroll < policy.bet_unit:
        return []
    base = min(bankroll, sizing_bankroll if sizing_bankroll is not None else bankroll)
    cands = [c for c in _candidates(race, policy, prob_col, odds_col, run_down)
             if c["p"] * c["o"] >= policy.ev_threshold and c["p"] >= policy.min_prob]
    if not cands:
        return []

    fractions = np.zeros(len(cands))
    win_idx = [k for k, c in enumerate(cands) if c["type"] == "win"]
    if win_idx and policy.simultaneous_kelly:
        p = np.array([cands[k]["p"] for k in win_idx])
        o = np.array([cands[k]["o"] for k in win_idx])
        fractions[win_idx] = multi_outcome_kelly(p, o)
    for k, c in enumerate(cands):
        if c["type"] == "win" and win_idx and policy.simultaneous_kelly:
            continue
        b = c["o"] - 1.0
        fractions[k] = max(0.0, c["p"] - (1.0 - c["p"]) / b) if b > 0 else 0.0

    frac = np.minimum(policy.alpha * fractions, policy.max_single_fraction)
    total = frac.sum()
    if total > policy.max_race_exposure:
        frac *= policy.max_race_exposure / total

    order = np.argsort(-frac)[: policy.max_bets_per_race]
    bets: List[Bet] = []
    for k in order:
        if frac[k] <= 0:
            continue
        stake = int(min(frac[k] * base, policy.max_stake_yen) // policy.bet_unit) * policy.bet_unit
        if stake < policy.bet_unit:
            continue
        c = cands[k]
        bets.append(Bet(str(race["race_id"].iloc[0]), "+".join(c["legs"]), c["type"], c["p"], c["o"],
                        c["p"] * c["o"], float(frac[k]), float(stake), tuple(c["legs"])))
    return bets


def select_win_bets(race: pd.DataFrame, bankroll: float, policy: "BetPolicy" = None, prob_col: str = "p_win",
                    odds_col: str = "win_odds", sizing_bankroll: Optional[float] = None) -> List[Bet]:
    """Win tickets only - the exact mutually-exclusive Kelly case."""
    policy = policy or BetPolicy()
    win_only = replace(policy, ticket_types=("win",))
    return select_bets(race, bankroll, win_only, prob_col, odds_col, sizing_bankroll)


def settle_win_bet(bet: Bet, finish_position: int, payout_odds: Optional[float] = None) -> float:
    """Profit (can be negative) of a win bet given the final finishing position."""
    odds = bet.odds if payout_odds is None else payout_odds
    return bet.stake * (odds - 1.0) if finish_position == 1 else -bet.stake


def bet_wins(bet: Bet, finishes: Dict[str, int], n_runners: int) -> bool:
    """Did this ticket come in, given every runner's finishing position?"""
    pos = [finishes.get(e, 0) for e in bet.selection]
    if any(p <= 0 for p in pos):          # a runner with no result cannot be a winner
        return False
    if bet.bet_type == "win":
        return pos[0] == 1
    if bet.bet_type == "place":
        return pos[0] <= place_payout_depth(n_runners)
    if bet.bet_type == "quinella":
        return set(pos) == {1, 2}
    raise ValueError(f"unknown bet type '{bet.bet_type}'")


def settle_bet(bet: Bet, finishes: Dict[str, int], n_runners: int, payout_odds: Optional[float] = None) -> float:
    odds = bet.odds if payout_odds is None else payout_odds
    return bet.stake * (odds - 1.0) if bet_wins(bet, finishes, n_runners) else -bet.stake


# ---------------------------------------------------------------------------
# candidate tickets beyond the win market
# ---------------------------------------------------------------------------
def market_win_probs(odds: np.ndarray) -> np.ndarray:
    """The crowd's win probabilities, with the takeout divided back out."""
    inv = 1.0 / np.clip(np.asarray(odds, dtype=float), 1.01, None)
    total = inv.sum()
    return inv / total if total > 0 else inv


def quinella_odds_from_win_pool(market_p: np.ndarray, takeout: float) -> np.ndarray:
    """Model a 馬連 pool as the crowd pricing pairs off its own win view.

    Real exotic pools are separate pools with their own money, so on live data
    this is replaced by the published odds. It stands in where only the win
    pool is available, and encodes the documented fact that the crowd prices
    exotics close to plain Harville - which is what makes them mispriced when
    the race actually runs down at a discount.
    """
    pair = quinella_probs(market_p, lam=1.0)
    with np.errstate(divide="ignore"):
        o = (1.0 - takeout) / np.clip(pair, 1e-9, None)
    np.fill_diagonal(o, np.inf)
    return o


def _candidates(race: pd.DataFrame, policy: "BetPolicy", prob_col: str, odds_col: str,
                run_down: Tuple[float, float]) -> List[Dict]:
    """Every ticket the policy is willing to consider, with its probability and price."""
    lam, mu = run_down
    p = race[prob_col].to_numpy(dtype=float)
    ids = race["entrant_id"].astype(str).to_numpy()
    n = int(race["n_runners"].iloc[0]) if "n_runners" in race else len(race)
    out: List[Dict] = []

    if "win" in policy.ticket_types:
        o = race[odds_col].to_numpy(dtype=float)
        for i in range(len(race)):
            if np.isfinite(o[i]) and o[i] > 1.0:
                out.append({"type": "win", "legs": (ids[i],), "p": float(p[i]), "o": float(o[i]), "idx": (i,)})

    if "place" in policy.ticket_types and "place_odds" in race:
        depth = place_payout_depth(n)
        pp = place_probs(p, depth, lam, mu)
        po = race["place_odds"].to_numpy(dtype=float)
        for i in range(len(race)):
            if np.isfinite(po[i]) and po[i] > 1.0:
                out.append({"type": "place", "legs": (ids[i],), "p": float(pp[i]), "o": float(po[i]), "idx": (i,)})

    if "quinella" in policy.ticket_types and len(race) >= 3:
        pair_p = quinella_probs(p, lam)
        mkt = market_win_probs(race[odds_col].to_numpy(dtype=float))
        pair_o = quinella_odds_from_win_pool(mkt, policy.takeout)
        for i in range(len(race)):
            for j in range(i + 1, len(race)):
                if np.isfinite(pair_o[i, j]) and pair_o[i, j] > 1.0:
                    out.append({"type": "quinella", "legs": (ids[i], ids[j]), "p": float(pair_p[i, j]),
                                "o": float(pair_o[i, j]), "idx": (i, j)})
    return out
