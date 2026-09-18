"""Performance metrics for the paper-betting equity curve."""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

RACE_DAYS_PER_YEAR = 104  # JRA runs ~2 days per week


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


def summarize(bets: pd.DataFrame, daily: pd.DataFrame, start_bankroll: float) -> Dict:
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
