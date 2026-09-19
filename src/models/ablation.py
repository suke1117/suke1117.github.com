#!/usr/bin/env python
"""Measure what a feature family is worth, by removing it.

Most roadmap tasks are accepted on "holdout log loss improves", which needs a
measurement that is not a single noisy fit.  This trains the full pipeline with
and without a family of features over several seeds and reports the difference,
so a family that only looked useful in one run does not get credit.

    python src/models/ablation.py --each                          # drop each family in turn
    python src/models/ablation.py --drop jockey_cond,pair,switch  # one combined comparison
    python src/models/ablation.py --keep entrant,jockey,trainer,static,relative

Log loss is per winner, so lower is better; ``delta`` is (variant - baseline),
meaning a positive delta is how much worse the model gets without the family.
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

from src.common.logging_utils import get_logger  # noqa: E402
from src.models.train_lgbm import chronological_split, evaluate, fit_pipeline, load_table  # noqa: E402

log = get_logger("ablation")


def run_variant(train: pd.DataFrame, calib: pd.DataFrame, test: pd.DataFrame, cols: List[str], cats: List[str],
                seeds: List[int], market_blend: bool) -> Dict[str, float]:
    """Average the holdout metrics of one feature set over several seeds."""
    runs = []
    for seed in seeds:
        pred = fit_pipeline(train, calib, cols, [c for c in cats if c in cols], params={"seed": seed},
                            market_blend=market_blend).predict(test)
        runs.append(evaluate(pred))
    keys = ("winner_logloss_p_win_model", "winner_logloss_p_win", "top1_hit_rate", "ece_p_win_model")
    out = {k: float(np.mean([r[k] for r in runs])) for k in keys}
    out["logloss_sd"] = float(np.std([r["winner_logloss_p_win_model"] for r in runs], ddof=1)) if len(seeds) > 1 else 0.0
    out["n_features"] = len(cols)
    return out


@click.command()
@click.option("--data", "data_path", default="processed/train.csv", show_default=True)
@click.option("--drop", default=None, help="comma-separated feature groups to remove")
@click.option("--keep", default=None, help="comma-separated feature groups to keep (everything else removed)")
@click.option("--each", is_flag=True, help="drop each group in turn and rank them by what they are worth")
@click.option("--seeds", default=3, show_default=True, type=int, help="fits to average per variant")
@click.option("--market_blend/--no_market_blend", default=False, show_default=True,
              help="off by default: the blend hides feature differences behind the market's own information")
@click.option("--output", default=None, help="write the table to this CSV")
def main(data_path, drop, keep, each, seeds, market_blend, output):
    df, meta = load_table(data_path)
    cols, cats = meta["feature_columns"], meta["categorical_columns"]
    groups: Dict[str, List[str]] = meta.get("feature_groups") or {}
    if not groups:
        raise click.ClickException("processed/features.json has no feature_groups; re-run preprocess.py")
    train, calib, test = chronological_split(df, None, None)
    seed_list = [20240101 + i for i in range(max(1, seeds))]
    log.info("train %d races | calib %d | holdout %d | %d seeds per variant", train["race_id"].nunique(),
             calib["race_id"].nunique(), test["race_id"].nunique(), len(seed_list))

    base = run_variant(train, calib, test, cols, cats, seed_list, market_blend)
    rows = [{"variant": "baseline (all)", **base, "delta_logloss": 0.0}]

    def add(name: str, keep_cols: List[str]) -> None:
        if not keep_cols:
            log.warning("variant %s would keep no features; skipped", name)
            return
        r = run_variant(train, calib, test, keep_cols, cats, seed_list, market_blend)
        r["delta_logloss"] = r["winner_logloss_p_win_model"] - base["winner_logloss_p_win_model"]
        rows.append({"variant": name, **r})

    if each:
        for g in groups:
            add(f"- {g}", [c for c in cols if c not in set(groups[g])])
    if drop:
        names = [g.strip() for g in drop.split(",") if g.strip()]
        unknown = [g for g in names if g not in groups]
        if unknown:
            raise click.BadParameter(f"unknown groups {unknown}; available: {list(groups)}", param_hint="--drop")
        removed = {c for g in names for c in groups[g]}
        add("- " + ",".join(names), [c for c in cols if c not in removed])
    if keep:
        names = [g.strip() for g in keep.split(",") if g.strip()]
        unknown = [g for g in names if g not in groups]
        if unknown:
            raise click.BadParameter(f"unknown groups {unknown}; available: {list(groups)}", param_hint="--keep")
        kept = {c for g in names for c in groups[g]}
        add("only " + ",".join(names), [c for c in cols if c in kept])
    if not (each or drop or keep):
        raise click.ClickException("pass --each, --drop or --keep")

    table = pd.DataFrame(rows).sort_values("delta_logloss", ascending=False)
    click.echo("=" * 88)
    click.echo(f"holdout {test['race_date'].min().date()}..{test['race_date'].max().date()} "
               f"({test['race_id'].nunique()} races), {len(seed_list)} seeds, market blend "
               f"{'on' if market_blend else 'off'}")
    click.echo("-" * 88)
    click.echo(f"{'variant':<28}{'features':>9}{'logloss':>10}{'±sd':>8}{'delta':>9}{'top1':>9}{'ECE':>8}")
    for _, r in table.iterrows():
        click.echo(f"{str(r['variant'])[:27]:<28}{int(r['n_features']):>9}{r['winner_logloss_p_win_model']:>10.4f}"
                   f"{r['logloss_sd']:>8.4f}{r['delta_logloss']:>+9.4f}{r['top1_hit_rate']:>9.3f}"
                   f"{r['ece_p_win_model']:>8.4f}")
    click.echo("-" * 88)
    click.echo("delta = variant - baseline. Positive means the model is worse without that family.")
    click.echo("A delta smaller than the seed spread (±sd) is not evidence of anything.")
    if output:
        table.to_csv(output, index=False)
        click.echo(f"wrote {output}")


if __name__ == "__main__":
    main()
