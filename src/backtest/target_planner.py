#!/usr/bin/env python
"""What a return target costs, in probability.

A target is not a plan.  Given the edge the backtest actually measured, a
target implies a stake size, and that stake size implies a distribution of
outcomes - including the bad half.  This computes both, by resampling the real
bets rather than assuming a distribution, so the answer comes from the same
data the rest of the system was validated on.

    python src/backtest/target_planner.py --target 4.0 --days 2

Two numbers decide everything:

* **expected log growth per bet** - what the edge is worth. Compounding is
  additive in logs, so a week's ceiling is this times the number of bets.
* **the Kelly multiple** - how hard you push. Above 1.0 the mean return keeps
  rising while the median falls, because the upside comes from a thinner and
  thinner slice of outcomes. That gap is the entire subject.

The honest use of this tool is to read the median and the loss columns, not
the probability of the target.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.common.config import MAX_KELLY_ALPHA  # noqa: E402
from src.common.logging_utils import get_logger  # noqa: E402

log = get_logger("planner")

MULTIPLES = [0.1, 0.25, 0.5, 1.0, 2.0, 4.0]


def full_kelly(prob: np.ndarray, odds: np.ndarray) -> np.ndarray:
    return np.clip(prob - (1.0 - prob) / np.clip(odds - 1.0, 1e-9, None), 0.0, None)


def growth_per_bet(prob: np.ndarray, odds: np.ndarray, fraction: np.ndarray) -> float:
    """Expected log growth of one bet at the given stake fraction."""
    f = np.clip(fraction, 0.0, 0.999)
    return float(np.mean(prob * np.log1p(f * (odds - 1.0)) + (1.0 - prob) * np.log1p(-f)))


def simulate(prob, odds, won, n_bets: int, multiple: float, trials: int, seed: int) -> Dict:
    """Bootstrap ``trials`` runs of ``n_bets`` real bets, staked at ``multiple`` x Kelly."""
    rng = np.random.default_rng(seed)
    fk = full_kelly(prob, odds)
    idx = rng.integers(0, prob.shape[0], size=(trials, n_bets))
    f = np.clip(fk[idx] * multiple, 0.0, 0.999)
    step = np.where(won[idx], 1.0 + f * (odds[idx] - 1.0), 1.0 - f)
    end = np.exp(np.log(step).sum(axis=1))
    return {
        "multiple": multiple,
        "median": float(np.median(end)),
        "mean": float(end.mean()),
        "p10": float(np.quantile(end, 0.10)),
        "p90": float(np.quantile(end, 0.90)),
        "growth_per_bet": growth_per_bet(prob, odds, fk * multiple),
        "end": end,
    }


def summarise(row: Dict, target: float) -> Dict:
    end = row["end"]
    return {k: v for k, v in row.items() if k != "end"} | {
        "p_target": float((end >= target).mean()),
        "p_half": float((end <= 0.5).mean()),
        "p_ruin": float((end <= 0.1).mean()),
        "p_down": float((end < 1.0).mean()),
    }


@click.command()
@click.option("--bets", "bets_path", default="backtest_results/bets.csv", show_default=True)
@click.option("--target", type=float, default=4.0, show_default=True, help="target multiple of the bankroll")
@click.option("--days", type=int, default=2, show_default=True, help="race days in the horizon (JRA runs Sat+Sun)")
@click.option("--bets_per_day", type=int, default=None, help="default: the average from the backtest")
@click.option("--trials", type=int, default=200_000, show_default=True)
@click.option("--seed", type=int, default=7, show_default=True)
@click.option("--output", default=None, help="write the table to this JSON file")
def main(bets_path, target, days, bets_per_day, trials, seed, output):
    path = Path(bets_path)
    if not path.exists():
        raise click.ClickException(f"{path} not found; run src/backtest/simulator.py first")
    bets = pd.read_csv(path)
    prob = bets["prob"].to_numpy(float)
    odds = bets["odds"].to_numpy(float)
    won = bets["won"].to_numpy().astype(bool)
    if bets_per_day is None:
        bets_per_day = max(1, int(round(len(bets) / max(1, bets["race_date"].nunique()))))
    n = days * bets_per_day

    fk = full_kelly(prob, odds)
    g_full = growth_per_bet(prob, odds, fk)
    ceiling = float(np.exp(g_full * n))
    need_growth = float(np.log(target) / n) if target > 1 else 0.0
    need_bets = float(np.log(target) / g_full) if g_full > 0 else float("inf")

    rows = [summarise(simulate(prob, odds, won, n, m, trials, seed), target) for m in MULTIPLES]

    click.echo("=" * 86)
    click.echo(f"実測 {len(bets):,} 点 (平均オッズ {odds.mean():.1f}、的中率 {won.mean():.1%}、平均期待値 "
               f"{(prob * odds).mean():.3f}) からの再標本化")
    click.echo(f"horizon: {days} 開催日 x {bets_per_day} 点 = {n} 点   目標 {target:.1f} 倍")
    click.echo("-" * 86)
    click.echo(f"1 点あたり期待対数成長 (フルケリー)   {g_full:.5f}")
    click.echo(f"フルケリーでの理論上の到達点          {ceiling:.2f} 倍")
    click.echo(f"目標に必要な 1 点あたり成長           {need_growth:.5f}  "
               f"(現在の {need_growth / g_full:.1f} 倍のエッジが要る)" if g_full > 0 else "")
    click.echo(f"現在のエッジのまま目標に到達する点数   {need_bets:,.0f} 点 "
               f"= {need_bets / max(1, bets_per_day):.0f} 開催日")
    click.echo("-" * 86)
    click.echo(f"{'ケリー倍率':<11}{'中央値':>8}{'平均':>8}{'下位10%':>9}{'上位10%':>9}"
               f"{'P(目標)':>9}{'P(半減)':>9}{'P(9割減)':>10}{'P(元本割れ)':>12}")
    for r in rows:
        flag = "" if r["multiple"] <= MAX_KELLY_ALPHA else ("  <- 上限超" if r["multiple"] > MAX_KELLY_ALPHA else "")
        click.echo(f"{r['multiple']:<11.2f}{r['median']:>8.2f}{r['mean']:>8.2f}{r['p10']:>9.2f}{r['p90']:>9.2f}"
                   f"{r['p_target']:>9.2%}{r['p_half']:>9.2%}{r['p_ruin']:>10.2%}{r['p_down']:>12.2%}{flag}")
    click.echo("-" * 86)
    best = max(rows, key=lambda r: r["p_target"])
    click.echo(f"目標到達の確率が最も高いのは {best['multiple']:.2f} 倍ケリーで {best['p_target']:.1%}。"
               f"ただし中央値は {best['median']:.2f} 倍、元本割れの確率は {best['p_down']:.1%}。")
    click.echo(f"システムの上限 α={MAX_KELLY_ALPHA} は 0.25 倍ケリー相当で、そこでの到達確率は "
               f"{[r for r in rows if r['multiple'] == 0.25][0]['p_target']:.2%}。")
    click.echo("平均が中央値より大きいほど、その利益は細い確率の側にある。読むべきは平均ではなく中央値と損失側。")

    if output:
        payload = {"generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
                   "target": target, "days": days, "bets_per_day": bets_per_day, "n_bets": n,
                   "n_source_bets": int(len(bets)), "avg_odds": float(odds.mean()), "hit_rate": float(won.mean()),
                   "avg_ev": float((prob * odds).mean()), "growth_per_bet_full_kelly": g_full,
                   "ceiling_full_kelly": ceiling, "required_growth_per_bet": need_growth,
                   "required_bets_at_current_edge": need_bets, "max_kelly_alpha": MAX_KELLY_ALPHA,
                   "rows": rows}
        Path(output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        click.echo(f"wrote {output}")


if __name__ == "__main__":
    main()
