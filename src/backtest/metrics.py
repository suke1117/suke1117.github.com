"""Performance metrics for the paper-betting equity curve.

Every headline number carries an interval. A point estimate of the recovery
rate is not a result: with a per-bet return standard deviation above 4, a few
thousand bets still leave a band tens of points wide, and a change smaller
than that band is not evidence of anything.

Two recovery rates are reported, because they answer different questions and
disagree when the stake moves:

* **stake-weighted** (total returned / total staked) is what the bankroll did.
  Under compounding the later, larger bets dominate it, so a run of luck late
  in the period inflates it.
* **per-bet** (mean of profit/stake) weights every decision equally. This is
  the one to compare across policy changes, because it does not reward a
  strategy for having been lucky when it happened to be betting big.

Intervals come from a block bootstrap over race days rather than over
individual bets: bets in the same race are mutually exclusive, and bets on the
same day share a model fit and a bankroll, so resampling bets independently
would understate the spread.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

import numpy as np
import pandas as pd

RACE_DAYS_PER_YEAR = 104  # JRA runs ~2 days per week
DEFAULT_BOOTSTRAP = 4000


def max_drawdown(equity: np.ndarray) -> float:
    equity = np.asarray(equity, dtype=float)
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(-dd.min())


def sharpe_ratio(period_returns: np.ndarray, periods_per_year: int = RACE_DAYS_PER_YEAR) -> float:
    r = np.asarray(period_returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * np.sqrt(periods_per_year))


NO_BLOCKS = {"lo": None, "hi": None, "p_le_1": None, "n_blocks": 0, "n_boot": 0,
             "reason": "bets carry no race_date, so days cannot be resampled"}


def _day_blocks(bets: pd.DataFrame) -> Optional[list]:
    """(stake, profit) arrays grouped by race day - the bootstrap's resampling unit.

    Returns ``None`` when the frame has no ``race_date``. Falling back to
    resampling individual bets would produce an interval that looks right and
    is too narrow, because bets in one race are mutually exclusive and bets on
    one day share a model fit; no interval is better than a flattering one.
    """
    if "race_date" not in bets.columns:
        return None
    d = pd.to_datetime(bets["race_date"]).dt.date
    return [g[["stake", "profit"]].to_numpy(dtype=float) for _, g in bets.groupby(d, sort=True)]


def stake_weighted_recovery(arr: np.ndarray) -> float:
    staked = arr[:, 0].sum()
    return float(1.0 + arr[:, 1].sum() / staked) if staked > 0 else float("nan")


def per_bet_recovery(arr: np.ndarray) -> float:
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.divide(arr[:, 1], arr[:, 0], out=np.zeros(arr.shape[0]), where=arr[:, 0] > 0)
    return float(1.0 + r.mean()) if r.size else float("nan")


def block_bootstrap(bets: pd.DataFrame, statistic: Callable[[np.ndarray], float],
                    n_boot: int = DEFAULT_BOOTSTRAP, level: float = 0.95, seed: int = 0) -> Dict:
    """Resample whole race days with replacement and report the spread.

    Also returns ``p_le_1``: the share of resamples at or below break-even,
    which is the number to read before believing a recovery rate above 100%.
    """
    blocks = _day_blocks(bets)
    if blocks is None:
        return dict(NO_BLOCKS)
    n = len(blocks)
    if n < 3:
        return {"lo": None, "hi": None, "p_le_1": None, "n_blocks": n, "n_boot": 0}
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        draws[i] = statistic(np.concatenate([blocks[j] for j in idx]))
    draws = draws[np.isfinite(draws)]
    if draws.size == 0:
        return {"lo": None, "hi": None, "p_le_1": None, "n_blocks": n, "n_boot": 0}
    a = (1.0 - level) / 2.0
    return {"lo": float(np.quantile(draws, a)), "hi": float(np.quantile(draws, 1 - a)),
            "p_le_1": float((draws <= 1.0).mean()), "n_blocks": n, "n_boot": int(draws.size)}


def per_bet_t_stat(bets: pd.DataFrame) -> Optional[float]:
    """How many standard errors the per-bet edge sits above break-even."""
    r = (bets["profit"] / bets["stake"]).to_numpy(dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2 or r.std(ddof=1) == 0:
        return None
    return float(r.mean() / (r.std(ddof=1) / np.sqrt(r.size)))


def bets_needed_for_edge(bets: pd.DataFrame, edge: float = 0.15, z: float = 1.96) -> Optional[float]:
    """Sample size at which an edge of ``edge`` would clear ``z`` standard errors."""
    r = (bets["profit"] / bets["stake"]).to_numpy(dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2 or edge <= 0:
        return None
    return float((z * r.std(ddof=1) / edge) ** 2)


def summarize(bets: pd.DataFrame, daily: pd.DataFrame, start_bankroll: float, n_boot: int = DEFAULT_BOOTSTRAP,
              seed: int = 0) -> Dict:
    res: Dict = {"start_bankroll": float(start_bankroll)}
    res["final_bankroll"] = float(daily["bankroll"].iloc[-1]) if len(daily) else float(start_bankroll)
    res["total_return"] = res["final_bankroll"] / start_bankroll - 1.0
    res["n_bets"] = int(len(bets))
    res["n_races_bet"] = int(bets["race_id"].nunique()) if len(bets) else 0
    res["n_race_days"] = int(len(daily))
    staked = float(bets["stake"].sum()) if len(bets) else 0.0
    res["total_staked"] = staked
    res["total_profit"] = float(bets["profit"].sum()) if len(bets) else 0.0
    res["roi_recovery_rate"] = (staked + res["total_profit"]) / staked if staked > 0 else float("nan")  # 回収率
    if len(bets):
        arr = bets[["stake", "profit"]].to_numpy(dtype=float)
        res["recovery_per_bet"] = per_bet_recovery(arr)
        res["recovery_ci"] = block_bootstrap(bets, stake_weighted_recovery, n_boot, seed=seed)
        res["recovery_per_bet_ci"] = block_bootstrap(bets, per_bet_recovery, n_boot, seed=seed)
        res["per_bet_t_stat"] = per_bet_t_stat(bets)
        res["per_bet_sd"] = float((bets["profit"] / bets["stake"]).std(ddof=1)) if len(bets) > 1 else None
        res["bets_needed_for_15pt_edge"] = bets_needed_for_edge(bets, 0.15)
        res["significant_at_95"] = bool(
            res["recovery_per_bet_ci"]["lo"] is not None and res["recovery_per_bet_ci"]["lo"] > 1.0)
    else:
        for k in ("recovery_per_bet", "per_bet_t_stat", "per_bet_sd", "bets_needed_for_15pt_edge"):
            res[k] = None
        res["recovery_ci"] = res["recovery_per_bet_ci"] = {"lo": None, "hi": None, "p_le_1": None}
        res["significant_at_95"] = False
    res["hit_rate"] = float((bets["profit"] > 0).mean()) if len(bets) else float("nan")
    res["avg_odds_bet"] = float(bets["odds"].mean()) if len(bets) else float("nan")
    res["avg_ev_bet"] = float(bets["ev"].mean()) if len(bets) else float("nan")
    res["avg_stake_fraction"] = float(bets["fraction"].mean()) if len(bets) else float("nan")
    res["max_drawdown"] = max_drawdown(daily["bankroll"].to_numpy()) if len(daily) else 0.0
    res["sharpe_daily_annualised"] = sharpe_ratio(daily["ret"].to_numpy()) if len(daily) else 0.0
    if len(bets):
        per_bet = bets["profit"] / bets["stake"]
        res["sharpe_per_bet"] = float(per_bet.mean() / per_bet.std(ddof=1)) if per_bet.std(ddof=1) > 0 else 0.0
    return res
