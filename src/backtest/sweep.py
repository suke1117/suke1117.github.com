#!/usr/bin/env python
"""Phase 4b CLI: walk-forward search for the EV threshold and Kelly fraction.

CLAUDE.md requires the betting parameters to be *found* by walk-forward
validation rather than guessed.  This module does that honestly:

1. Walk-forward predictions are generated **once** over the whole window.
   They depend only on the data and the retrain schedule, never on the
   betting policy, so the expensive model fitting is not repeated per grid
   point.
2. The window is split chronologically into a **search** part and a
   **holdout** part.  The grid is scored on the search part only.
3. The winner is re-scored on the holdout part, which no grid point ever saw.
   The gap between the two is reported, because a policy that only looks good
   in-sample is the classic way to lose money with a "validated" system.

Selection is risk-first: grid points that breach ``--max_dd`` or place fewer
than ``--min_bets`` bets are disqualified regardless of their return.

    python src/backtest/sweep.py --start_date 2021-01-01 --end_date 2023-12-31 \
        --holdout_start 2023-01-01
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtest.metrics import summarize  # noqa: E402
from src.backtest.simulator import WalkForwardSimulator  # noqa: E402
from src.betting.kelly_calculator import KellyAlphaError, validate_alpha  # noqa: E402
from src.betting.strategy import BetPolicy  # noqa: E402
from src.common.config import DEFAULT_BANKROLL_YEN, MAX_KELLY_ALPHA, MAX_STAKE_YEN  # noqa: E402
from src.common.logging_utils import get_logger  # noqa: E402
from src.models.train_lgbm import load_table  # noqa: E402

log = get_logger("sweep")

OBJECTIVES = ("sharpe", "growth", "recovery")


def parse_grid(text: str) -> List[float]:
    return [float(x) for x in str(text).replace(" ", "").split(",") if x != ""]


def log_growth_per_day(daily: pd.DataFrame, start_bankroll: float) -> float:
    """Mean log growth per betting day - the quantity Kelly staking maximises."""
    if daily.empty:
        return 0.0
    final = float(daily["bankroll"].iloc[-1])
    if final <= 0:
        return float("-inf")
    return float(np.log(final / start_bankroll) / len(daily))


def score_policy(pred: pd.DataFrame, sim: WalkForwardSimulator, policy: BetPolicy, bankroll: float,
                 compound: bool) -> Dict:
    bets, daily, _ = sim.simulate(pred, policy=policy, bankroll=bankroll, compound=compound)
    if bets.empty:
        return {"n_bets": 0, "roi_recovery_rate": float("nan"), "max_drawdown": 0.0, "sharpe_daily_annualised": 0.0,
                "final_bankroll": bankroll, "total_return": 0.0, "hit_rate": float("nan"),
                "log_growth_per_day": 0.0, "avg_odds_bet": float("nan"), "total_staked": 0.0, "total_profit": 0.0}
    s = summarize(bets, daily, bankroll)
    s["log_growth_per_day"] = log_growth_per_day(daily, bankroll)
    return s


def objective_value(row: Dict, objective: str) -> float:
    if objective == "sharpe":
        return row["sharpe_daily_annualised"]
    if objective == "growth":
        return row["log_growth_per_day"]
    if objective == "recovery":
        rr = row["roi_recovery_rate"]
        return float(rr) if np.isfinite(rr) else float("-inf")
    raise ValueError(f"unknown objective '{objective}'; choose from {OBJECTIVES}")


def run_grid(pred: pd.DataFrame, sim: WalkForwardSimulator, alphas: List[float], evs: List[float],
             max_bets_list: List[int], bankroll: float, compound: bool, max_stake_yen: float) -> pd.DataFrame:
    rows = []
    for alpha in alphas:
        validate_alpha(alpha)  # hard limit: full Kelly can never enter the grid
        for ev in evs:
            for mb in max_bets_list:
                policy = BetPolicy(alpha=alpha, ev_threshold=ev, max_bets_per_race=mb, max_stake_yen=max_stake_yen)
                s = score_policy(pred, sim, policy, bankroll, compound)
                rows.append({"alpha": alpha, "ev_threshold": ev, "max_bets_per_race": mb, **{
                    k: s[k] for k in ("n_bets", "hit_rate", "avg_odds_bet", "total_staked", "total_profit",
                                      "roi_recovery_rate", "max_drawdown", "sharpe_daily_annualised",
                                      "log_growth_per_day", "final_bankroll", "total_return")}})
                log.info("alpha=%.3f ev=%.2f maxbets=%d | bets=%5d recovery=%.3f dd=%.3f sharpe=%.2f",
                         alpha, ev, mb, s["n_bets"], s["roi_recovery_rate"], s["max_drawdown"],
                         s["sharpe_daily_annualised"])
    return pd.DataFrame(rows)


def select_best(grid: pd.DataFrame, objective: str, max_dd: float, min_bets: int) -> Optional[pd.Series]:
    """Risk-first selection: disqualify on drawdown and sample size, then rank."""
    ok = grid[(grid["max_drawdown"] <= max_dd) & (grid["n_bets"] >= min_bets)].copy()
    if ok.empty:
        return None
    ok["objective"] = [objective_value(r, objective) for _, r in ok.iterrows()]
    return ok.sort_values("objective", ascending=False).iloc[0]


@click.command()
@click.option("--data", "data_path", default="processed/train.csv", show_default=True)
@click.option("--start_date", default="2021-01-01", show_default=True, help="first race day of the whole window")
@click.option("--end_date", default="2023-12-31", show_default=True)
@click.option("--holdout_start", default=None, help="first race day of the holdout part (default: last third of the window)")
@click.option("--alphas", default="0.02,0.05,0.10,0.15,0.20,0.25", show_default=True, help="Kelly fractions to try")
@click.option("--ev_thresholds", default="1.00,1.05,1.10,1.20,1.30,1.50", show_default=True)
@click.option("--max_bets", default="1,2,3", show_default=True, help="max tickets per race to try")
@click.option("--objective", type=click.Choice(OBJECTIVES), default="sharpe", show_default=True)
@click.option("--max_dd", type=float, default=0.25, show_default=True, help="disqualify policies breaching this drawdown")
@click.option("--min_bets", type=int, default=200, show_default=True, help="disqualify policies with too few bets")
@click.option("--bankroll", type=float, default=DEFAULT_BANKROLL_YEN, show_default=True)
@click.option("--retrain_months", type=int, default=3, show_default=True)
@click.option("--calib_months", type=int, default=6, show_default=True)
@click.option("--odds_haircut", type=float, default=0.05, show_default=True)
@click.option("--market_blend/--no_market_blend", default=True, show_default=True)
@click.option("--compound/--no_compound", default=True, show_default=True)
@click.option("--max_stake_yen", type=float, default=MAX_STAKE_YEN, show_default=True)
@click.option("--predictions", default=None, help="reuse a wf_predictions.csv instead of refitting the models")
@click.option("--output_dir", default="backtest_results/sweep/", show_default=True)
def main(data_path, start_date, end_date, holdout_start, alphas, ev_thresholds, max_bets, objective, max_dd, min_bets,
         bankroll, retrain_months, calib_months, odds_haircut, market_blend, compound, max_stake_yen, predictions,
         output_dir):
    alphas_l, evs_l = parse_grid(alphas), parse_grid(ev_thresholds)
    max_bets_l = [int(x) for x in parse_grid(max_bets)]
    try:
        for a in alphas_l:
            validate_alpha(a)
    except KellyAlphaError as exc:
        raise click.BadParameter(str(exc), param_hint="--alphas")

    table, meta = load_table(data_path)
    sim = WalkForwardSimulator(table, meta["feature_columns"], meta["categorical_columns"], BetPolicy(), bankroll,
                               retrain_months=retrain_months, calib_months=calib_months, odds_haircut=odds_haircut,
                               market_blend=market_blend, compound=compound)
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)

    if predictions:
        pred = pd.read_csv(predictions, parse_dates=["race_date", "period_start", "period_end"], dtype={"race_id": str,
                                                                                                       "entrant_id": str})
        pred = pred[(pred["race_date"] >= start) & (pred["race_date"] <= end)]
        log.info("reusing %d cached predictions from %s", len(pred), predictions)
    else:
        log.info("generating walk-forward predictions once for the whole window (this is the expensive step)")
        pred = sim.generate_predictions(start, end)
    if pred.empty:
        raise click.ClickException("no walk-forward predictions were produced; widen the date range")

    days = np.sort(pred["race_date"].unique())
    hold_start = pd.Timestamp(holdout_start) if holdout_start else pd.Timestamp(days[int(len(days) * 2 / 3)])
    search, holdout = pred[pred["race_date"] < hold_start], pred[pred["race_date"] >= hold_start]
    if search.empty or holdout.empty:
        raise click.ClickException("--holdout_start splits the window into an empty part; pick a date inside it")
    log.info("search %s..%s (%d races) | holdout %s..%s (%d races)",
             search["race_date"].min().date(), search["race_date"].max().date(), search["race_id"].nunique(),
             holdout["race_date"].min().date(), holdout["race_date"].max().date(), holdout["race_id"].nunique())

    grid = run_grid(search, sim, alphas_l, evs_l, max_bets_l, bankroll, compound, max_stake_yen)
    best = select_best(grid, objective, max_dd, min_bets)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    grid.to_csv(out / "sweep_grid.csv", index=False)

    click.echo("=" * 78)
    click.echo(f"Parameter sweep  objective={objective}  constraints: drawdown<={max_dd:.0%}, bets>={min_bets}")
    click.echo(f"search  {search['race_date'].min().date()}..{search['race_date'].max().date()}")
    click.echo(f"holdout {holdout['race_date'].min().date()}..{holdout['race_date'].max().date()}")
    click.echo("-" * 78)
    if best is None:
        click.echo("No grid point satisfied the risk constraints. That is a result, not a bug:")
        click.echo("loosen --max_dd, lower --min_bets, or accept that this model has no exploitable edge.")
        top = grid.sort_values("max_drawdown").head(5)
        click.echo(top[["alpha", "ev_threshold", "max_bets_per_race", "n_bets", "roi_recovery_rate",
                        "max_drawdown", "sharpe_daily_annualised"]].to_string(index=False))
        (out / "sweep_summary.json").write_text(json.dumps({"selected": None, "objective": objective,
                                                            "max_dd": max_dd, "min_bets": min_bets}, indent=2, default=str))
        return

    policy = BetPolicy(alpha=float(best["alpha"]), ev_threshold=float(best["ev_threshold"]),
                       max_bets_per_race=int(best["max_bets_per_race"]), max_stake_yen=max_stake_yen)
    hold = score_policy(holdout, sim, policy, bankroll, compound)

    click.echo(f"selected: alpha={policy.alpha}  EV>={policy.ev_threshold}  max_bets_per_race={policy.max_bets_per_race}")
    click.echo("-" * 78)
    click.echo(f"{'metric':<28}{'search (in-sample)':>24}{'holdout (out-of-sample)':>26}")
    for label, key, fmt in (("bets", "n_bets", "{:,.0f}"), ("recovery rate (回収率)", "roi_recovery_rate", "{:.1%}"),
                            ("hit rate", "hit_rate", "{:.1%}"), ("max drawdown", "max_drawdown", "{:.1%}"),
                            ("Sharpe (daily, annualised)", "sharpe_daily_annualised", "{:.2f}"),
                            ("log growth / day", "log_growth_per_day", "{:.5f}")):
        a = fmt.format(best[key]) if np.isfinite(float(best[key])) else "n/a"
        b = fmt.format(hold[key]) if np.isfinite(float(hold[key])) else "n/a"
        click.echo(f"{label:<28}{a:>24}{b:>26}")
    click.echo("-" * 78)
    degradation = hold["roi_recovery_rate"] - best["roi_recovery_rate"]
    verdict = ("holds up out-of-sample" if hold["roi_recovery_rate"] > 1.0 and degradation > -0.10
               else "degrades out-of-sample - treat the search result as overfitted")
    click.echo(f"out-of-sample recovery rate moved {degradation:+.1%} vs search: {verdict}")

    summary = {"objective": objective, "constraints": {"max_dd": max_dd, "min_bets": min_bets},
               "selected": {"alpha": policy.alpha, "ev_threshold": policy.ev_threshold,
                            "max_bets_per_race": policy.max_bets_per_race},
               "search": {k: (None if not np.isfinite(float(best[k])) else float(best[k]))
                          for k in ("n_bets", "roi_recovery_rate", "hit_rate", "max_drawdown",
                                    "sharpe_daily_annualised", "log_growth_per_day", "final_bankroll")},
               "holdout": {k: (None if not np.isfinite(float(hold[k])) else float(hold[k]))
                           for k in ("n_bets", "roi_recovery_rate", "hit_rate", "max_drawdown",
                                     "sharpe_daily_annualised", "log_growth_per_day", "final_bankroll")},
               "search_window": [str(search["race_date"].min().date()), str(search["race_date"].max().date())],
               "holdout_window": [str(holdout["race_date"].min().date()), str(holdout["race_date"].max().date())],
               "compound": compound, "odds_haircut": odds_haircut, "bankroll": bankroll}
    (out / "sweep_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    click.echo(f"outputs -> {out}/")


if __name__ == "__main__":
    main()
