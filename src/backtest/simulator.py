#!/usr/bin/env python
"""Phase 4 CLI: walk-forward paper-betting simulator.

Timeline for each evaluation period P = [period_start, period_end):

    |<---------- train ---------->|<-- calib -->|<-- P (bet) -->|
                                              ^ retrain boundary = period_start

The model is refit on every period boundary using *only* rows dated before
``period_start``; the most recent ``--calib_months`` of those rows are held
out for early stopping / temperature / isotonic / market-blend fitting.  Bets
for races in P are sized with the bankroll as it stands *before* each race.

Odds: ``win_odds`` in the table are final odds.  Because live bets are placed
minutes before the off, ``--odds_haircut`` (default 5%) shrinks the payout to
be conservative: payout_odds = 1 + (odds - 1) * (1 - haircut).

    python src/backtest/simulator.py --start_date 2022-01-01 --end_date 2023-12-31
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtest.metrics import summarize  # noqa: E402
from src.betting.strategy import BetPolicy, select_bets, settle_bet  # noqa: E402
from src.common.config import DEFAULT_BANKROLL_YEN, DEFAULT_EV_THRESHOLD, DEFAULT_KELLY_ALPHA, MAX_STAKE_YEN  # noqa: E402
from src.common.logging_utils import get_logger  # noqa: E402
from src.models.train_lgbm import fit_pipeline, load_table  # noqa: E402

log = get_logger("backtest")

#: columns carried from a predicted period into the betting stage
PREDICTION_COLUMNS = ["race_date", "race_id", "entrant_id", "n_runners", "finish_position", "win_odds", "place_odds",
                      "score", "p_win_raw", "p_win_model", "p_win"]


def period_boundaries(start: pd.Timestamp, end: pd.Timestamp, months: int) -> List[pd.Timestamp]:
    bounds = [start]
    while bounds[-1] < end:
        bounds.append(min(bounds[-1] + pd.DateOffset(months=months), end + pd.Timedelta(days=1)))
    return bounds


class WalkForwardSimulator:
    def __init__(self, table: pd.DataFrame, feature_cols, categorical_cols, policy: BetPolicy, bankroll: float,
                 retrain_months: int = 3, calib_months: int = 6, min_train_races: int = 500, odds_haircut: float = 0.05,
                 market_blend: bool = True, compound: bool = True):
        self.table = table
        self.feature_cols, self.categorical_cols = feature_cols, categorical_cols
        self.policy = policy
        self.start_bankroll = bankroll
        self.retrain_months, self.calib_months = retrain_months, calib_months
        self.min_train_races = min_train_races
        self.odds_haircut = odds_haircut
        self.market_blend = market_blend
        self.compound = compound

    # ------------------------------------------------------------------
    # Stage 1: walk-forward out-of-sample predictions (expensive; policy-free)
    # ------------------------------------------------------------------
    def generate_predictions(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Refit the pipeline on every period boundary and predict that period.

        The result depends only on the data and the retrain schedule - never on
        the betting policy - so a parameter sweep can reuse one pass of this.
        """
        frames = []
        bounds = period_boundaries(start, end, self.retrain_months)
        for p_start, p_end in zip(bounds[:-1], bounds[1:]):
            hist = self.table[self.table["race_date"] < p_start]
            calib_start = p_start - pd.DateOffset(months=self.calib_months)
            train, calib = hist[hist["race_date"] < calib_start], hist[hist["race_date"] >= calib_start]
            period = self.table[(self.table["race_date"] >= p_start) & (self.table["race_date"] < p_end)]
            if train["race_id"].nunique() < self.min_train_races or calib["race_id"].nunique() < 50 or period.empty:
                log.warning("skipping %s..%s: insufficient history (train %d races, calib %d)", p_start.date(), p_end.date(),
                            train["race_id"].nunique(), calib["race_id"].nunique())
                continue
            assert train["race_date"].max() < calib["race_date"].min() <= calib["race_date"].max() < period["race_date"].min(), \
                "time ordering violated"
            predictor = fit_pipeline(train, calib, self.feature_cols, self.categorical_cols, market_blend=self.market_blend)
            pred = predictor.predict(period)
            log.info("period %s..%s | train<%s (%d races) calib (%d) | predict %d races | blend a=%.2f b=%.2f",
                     p_start.date(), (p_end - pd.Timedelta(days=1)).date(), calib_start.date(), train["race_id"].nunique(),
                     calib["race_id"].nunique(), period["race_id"].nunique(), predictor.blend.a, predictor.blend.b)
            pred = pred[[c for c in PREDICTION_COLUMNS if c in pred.columns]].copy()
            pred["period_start"] = p_start
            pred["period_end"] = p_end - pd.Timedelta(days=1)
            pred["blend_a"] = predictor.blend.a
            pred["blend_b"] = predictor.blend.b
            pred["pl_temperature"] = predictor.temperature.temperature
            pred["run_down_lam"] = predictor.run_down.lam
            pred["run_down_mu"] = predictor.run_down.mu
            frames.append(pred)
        if not frames:
            return pd.DataFrame(columns=PREDICTION_COLUMNS + ["period_start", "period_end", "blend_a", "blend_b",
                                                             "pl_temperature"])
        return pd.concat(frames, ignore_index=True).sort_values(["race_date", "race_id"], kind="stable")

    # ------------------------------------------------------------------
    # Stage 2: paper betting over pre-computed predictions (cheap; policy-dependent)
    # ------------------------------------------------------------------
    def simulate(self, pred: pd.DataFrame, policy: Optional[BetPolicy] = None, bankroll: Optional[float] = None,
                 compound: Optional[bool] = None):
        """Settle paper bets over ``pred`` (from :meth:`generate_predictions`)."""
        policy = policy or self.policy
        start_bankroll = self.start_bankroll if bankroll is None else bankroll
        compound = self.compound if compound is None else compound
        bank = start_bankroll
        bet_rows, daily_rows, period_rows = [], [], []
        if pred.empty:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        for (p_start, p_end), period_pred in pred.groupby(["period_start", "period_end"], sort=True):
            p_bets, p_profit, p_staked = 0, 0.0, 0.0
            for day, day_df in period_pred.groupby("race_date", sort=True):
                day_start = bank
                for race_id, race in day_df.groupby("race_id", sort=True):
                    race = race.reset_index(drop=True)
                    lam = float(race["run_down_lam"].iloc[0]) if "run_down_lam" in race else 1.0
                    mu = float(race["run_down_mu"].iloc[0]) if "run_down_mu" in race else lam
                    bets = select_bets(race, bank, policy, sizing_bankroll=None if compound else start_bankroll,
                                       run_down=(lam, mu))
                    if not bets:
                        continue
                    fp = {str(k): int(v) for k, v in zip(race["entrant_id"], race["finish_position"])}
                    n_runners = int(race["n_runners"].iloc[0]) if "n_runners" in race else len(race)
                    for b in bets:
                        payout_odds = 1.0 + (b.odds - 1.0) * (1.0 - self.odds_haircut)
                        profit = settle_bet(b, fp, n_runners, payout_odds)
                        bank += profit
                        p_bets += 1
                        p_profit += profit
                        p_staked += b.stake
                        bet_rows.append({"race_date": day, "race_id": race_id, "entrant_id": b.entrant_id,
                                         "bet_type": b.bet_type, "prob": b.prob, "odds": b.odds,
                                         "payout_odds": payout_odds, "ev": b.ev, "fraction": b.fraction,
                                         "stake": b.stake, "won": int(profit > 0), "profit": profit,
                                         "bankroll_after": bank})
                daily_rows.append({"race_date": day, "bankroll": bank, "ret": bank / day_start - 1.0})
            period_rows.append({"period_start": p_start, "period_end": p_end, "n_bets": p_bets, "staked": p_staked,
                                "profit": p_profit, "recovery_rate": (p_staked + p_profit) / p_staked if p_staked else np.nan,
                                "bankroll_end": bank, "blend_a": float(period_pred["blend_a"].iloc[0]),
                                "blend_b": float(period_pred["blend_b"].iloc[0]),
                                "pl_temperature": float(period_pred["pl_temperature"].iloc[0])})
        return pd.DataFrame(bet_rows), pd.DataFrame(daily_rows), pd.DataFrame(period_rows)

    def run(self, start: pd.Timestamp, end: pd.Timestamp):
        """Convenience: generate walk-forward predictions, then paper-bet them."""
        return self.simulate(self.generate_predictions(start, end))


@click.command()
@click.option("--data", "data_path", default="processed/train.csv", show_default=True)
@click.option("--start_date", default="2022-01-01", show_default=True)
@click.option("--end_date", default="2023-12-31", show_default=True)
@click.option("--bankroll", type=float, default=DEFAULT_BANKROLL_YEN, show_default=True)
@click.option("--alpha", type=float, default=DEFAULT_KELLY_ALPHA, show_default=True, help="Kelly fraction (hard max 0.25)")
@click.option("--ev_threshold", type=float, default=DEFAULT_EV_THRESHOLD, show_default=True)
@click.option("--retrain_months", type=int, default=3, show_default=True)
@click.option("--calib_months", type=int, default=6, show_default=True)
@click.option("--odds_haircut", type=float, default=0.05, show_default=True, help="payout shrink vs final odds")
@click.option("--max_bets_per_race", type=int, default=3, show_default=True)
@click.option("--tickets", default="win", show_default=True,
              help="comma-separated ticket types: win, place, quinella")
@click.option("--takeout", type=float, default=0.20, show_default=True,
              help="used only to model a 馬連 pool from the win pool when real exotic odds are absent")
@click.option("--market_blend/--no_market_blend", default=True, show_default=True)
@click.option("--compound/--no_compound", default=True, show_default=True,
              help="size bets on the running bankroll (compound) or on the initial bankroll (flat)")
@click.option("--max_stake_yen", type=float, default=MAX_STAKE_YEN, show_default=True, help="liquidity cap per ticket")
@click.option("--output_dir", default="backtest_results/", show_default=True)
def main(data_path, start_date, end_date, bankroll, alpha, ev_threshold, retrain_months, calib_months, odds_haircut,
         max_bets_per_race, tickets, takeout, market_blend, compound, max_stake_yen, output_dir):
    table, meta = load_table(data_path)
    ticket_types = tuple(t.strip() for t in tickets.split(",") if t.strip())
    policy = BetPolicy(alpha=alpha, ev_threshold=ev_threshold, max_bets_per_race=max_bets_per_race,
                       max_stake_yen=max_stake_yen, ticket_types=ticket_types, takeout=takeout)
    sim = WalkForwardSimulator(table, meta["feature_columns"], meta["categorical_columns"], policy, bankroll,
                               retrain_months=retrain_months, calib_months=calib_months, odds_haircut=odds_haircut,
                               market_blend=market_blend, compound=compound)
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    pred = sim.generate_predictions(start, end)
    bets, daily, periods = sim.simulate(pred)
    summary = summarize(bets, daily, bankroll)
    summary["policy"] = policy.__dict__
    summary["odds_haircut"] = odds_haircut
    summary["compound"] = compound
    summary["period"] = {"start": start_date, "end": end_date}

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    bets.to_csv(out / "bets.csv", index=False)
    daily.to_csv(out / "equity_curve.csv", index=False)
    periods.to_csv(out / "periods.csv", index=False)
    pred.to_csv(out / "wf_predictions.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    click.echo("=" * 72)
    click.echo(f"Paper betting {start_date} .. {end_date}   alpha={alpha} EV>={ev_threshold} haircut={odds_haircut} "
               f"{'compound' if compound else 'flat'}  券種 {'/'.join(ticket_types)}")
    click.echo("-" * 72)
    click.echo(f"bets: {summary['n_bets']}  races bet: {summary['n_races_bet']}  race days: {summary['n_race_days']}")
    click.echo(f"total staked: {summary['total_staked']:,.0f}  profit: {summary['total_profit']:,.0f}")
    ci, pci = summary.get("recovery_ci") or {}, summary.get("recovery_per_bet_ci") or {}
    def _band(v, c):
        if c.get("lo") is None:
            return f"{v * 100:.1f}%"
        return f"{v * 100:.1f}%  [{c['lo'] * 100:.1f}%, {c['hi'] * 100:.1f}%]"
    click.echo(f"回収率 投入額加重: {_band(summary['roi_recovery_rate'], ci)}")
    click.echo(f"回収率 1点等重み: {_band(summary['recovery_per_bet'], pci)}   "
               f"t={summary.get('per_bet_t_stat') or float('nan'):.2f}")
    click.echo(f"損益分岐を下回る確率: {pci.get('p_le_1', float('nan')):.1%}   "
               f"{'95%で有意' if summary.get('significant_at_95') else '95%では有意でない'}")
    click.echo(f"hit rate: {summary['hit_rate']*100:.1f}%")
    if len(bets) and "bet_type" in bets:
        by = bets.groupby("bet_type").apply(
            lambda g: pd.Series({"n": len(g), "staked": g["stake"].sum(), "profit": g["profit"].sum(),
                                 "recovery": (g["stake"].sum() + g["profit"].sum()) / g["stake"].sum(),
                                 "hit": (g["profit"] > 0).mean()}), include_groups=False)
        click.echo("-" * 72)
        click.echo("券種別: " + "  ".join(
            f"{t} n={int(r['n'])} 回収率={r['recovery']*100:.1f}% 的中={r['hit']*100:.1f}%" for t, r in by.iterrows()))
    click.echo(f"bankroll: {bankroll:,.0f} -> {summary['final_bankroll']:,.0f}  ({summary['total_return']*100:+.1f}%)")
    click.echo(f"max drawdown: {summary['max_drawdown']*100:.2f}%   Sharpe (daily, annualised): {summary['sharpe_daily_annualised']:.2f}")
    if len(periods):
        click.echo("-" * 72)
        click.echo(periods[["period_start", "period_end", "n_bets", "recovery_rate", "bankroll_end"]].to_string(index=False))
    click.echo(f"outputs -> {out}/")


if __name__ == "__main__":
    main()
