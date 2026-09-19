#!/usr/bin/env python
"""Measure what a feature family is worth, by removing it.

Most roadmap tasks are accepted on "holdout log loss improves", which needs a
measurement that is not a single noisy fit.  This trains the full pipeline with
and without a family of features and reports the difference, so a family that
only looked useful in one run does not get credit.

Two sources of noise are separated, because they fail differently:

* **seed spread** - the same data, a different random start. Averaging over
  seeds removes it.
* **window spread** - a different stretch of racing. This one does not average
  away by adding seeds, and it is the one that decides whether a finding
  survives outside the window it was found in.

A verdict of 有意 requires the mean contribution to clear the *window* spread,
not the seed spread. A single window cannot produce that verdict at all; it
reports 1ウィンドウ and leaves the judgement open, because deciding from one
window is the same selection error the parameter search is warned about.

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
from typing import Dict, List, Tuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.common.logging_utils import get_logger  # noqa: E402
from src.models.train_lgbm import chronological_split, evaluate, fit_pipeline, load_table  # noqa: E402

log = get_logger("ablation")


METRIC_KEYS = ("winner_logloss_p_win_model", "winner_logloss_p_win", "top1_hit_rate", "ece_p_win_model")


def walk_forward_windows(df: pd.DataFrame, n_windows: int, calib_frac: float = 0.15,
                         test_frac: float = 0.15) -> List[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]:
    """``n_windows`` expanding train / calib / holdout splits, each later than the last.

    The holdouts do not overlap, so a family that only helps in one stretch of
    racing shows up as disagreement between windows rather than as an average.
    """
    dates = np.sort(df["race_date"].unique())
    n = len(dates)
    if n < 20:
        raise ValueError("not enough race days to make windows")
    out = []
    span = int(n * test_frac)
    first_end = n - span * n_windows
    if first_end < n * 0.3:
        raise ValueError(f"{n_windows} windows leave too little history; use fewer")
    for w in range(n_windows):
        test_lo = first_end + span * w
        test_hi = test_lo + span
        calib_lo = int(test_lo - n * calib_frac)
        tr = df[df["race_date"] <= dates[calib_lo - 1]]
        ca = df[(df["race_date"] > dates[calib_lo - 1]) & (df["race_date"] <= dates[test_lo - 1])]
        te = df[(df["race_date"] > dates[test_lo - 1]) & (df["race_date"] <= dates[min(test_hi, n) - 1])]
        if tr.empty or ca.empty or te.empty:
            continue
        out.append((tr, ca, te))
    return out


def run_variant(windows: List[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]], cols: List[str], cats: List[str],
                seeds: List[int], market_blend: bool) -> Dict[str, float]:
    """Metrics for one feature set, averaged over seeds inside each window."""
    per_window = []
    for tr, ca, te in windows:
        runs = []
        for seed in seeds:
            pred = fit_pipeline(tr, ca, cols, [c for c in cats if c in cols], params={"seed": seed},
                                market_blend=market_blend).predict(te)
            runs.append(evaluate(pred))
        per_window.append({k: float(np.mean([r[k] for r in runs])) for k in METRIC_KEYS} |
                          {"seed_sd": float(np.std([r["winner_logloss_p_win_model"] for r in runs], ddof=1))
                           if len(seeds) > 1 else 0.0})
    out = {k: float(np.mean([w[k] for w in per_window])) for k in METRIC_KEYS}
    out["logloss_sd"] = float(np.mean([w["seed_sd"] for w in per_window]))
    out["n_features"] = len(cols)
    out["n_windows"] = len(per_window)
    out["_per_window"] = [w["winner_logloss_p_win_model"] for w in per_window]
    return out


@click.command()
@click.option("--data", "data_path", default="processed/train.csv", show_default=True)
@click.option("--drop", default=None, help="comma-separated feature groups to remove")
@click.option("--keep", default=None, help="comma-separated feature groups to keep (everything else removed)")
@click.option("--each", is_flag=True, help="drop each group in turn and rank them by what they are worth")
@click.option("--seeds", default=3, show_default=True, type=int, help="fits to average per variant per window")
@click.option("--windows", default=3, show_default=True, type=int,
              help="walk-forward holdout windows; a verdict needs the contribution to clear the window spread")
@click.option("--market_blend/--no_market_blend", default=False, show_default=True,
              help="off by default: the blend hides feature differences behind the market's own information")
@click.option("--output", default=None, help="write the table to this CSV")
def main(data_path, drop, keep, each, seeds, windows, market_blend, output):
    df, meta = load_table(data_path)
    cols, cats = meta["feature_columns"], meta["categorical_columns"]
    groups: Dict[str, List[str]] = meta.get("feature_groups") or {}
    if not groups:
        raise click.ClickException("processed/features.json has no feature_groups; re-run preprocess.py")
    seed_list = [20240101 + i for i in range(max(1, seeds))]
    if windows <= 1:
        train, calib, test = chronological_split(df, None, None)
        win_list = [(train, calib, test)]
    else:
        try:
            win_list = walk_forward_windows(df, windows)
        except ValueError as exc:
            raise click.ClickException(str(exc))
    log.info("%d window(s) x %d seeds | holdouts: %s", len(win_list), len(seed_list),
             ", ".join(f"{t['race_date'].min():%Y-%m-%d}..{t['race_date'].max():%Y-%m-%d}" for _, _, t in win_list))

    base = run_variant(win_list, cols, cats, seed_list, market_blend)
    rows = [{"variant": "baseline (all)", **base, "delta_logloss": 0.0, "window_sd": 0.0, "verdict": "基準"}]

    def add(name: str, keep_cols: List[str]) -> None:
        if not keep_cols:
            log.warning("variant %s would keep no features; skipped", name)
            return
        r = run_variant(win_list, keep_cols, cats, seed_list, market_blend)
        per_window_delta = [a - b for a, b in zip(r.pop("_per_window"), base["_per_window"])]
        r["delta_logloss"] = float(np.mean(per_window_delta))
        r["window_sd"] = float(np.std(per_window_delta, ddof=1)) if len(per_window_delta) > 1 else 0.0
        r["windows_agreeing"] = int(sum(1 for d in per_window_delta
                                        if np.sign(d) == np.sign(r["delta_logloss"]) and d != 0))
        if len(per_window_delta) < 2:
            r["verdict"] = "1ウィンドウ"
        elif abs(r["delta_logloss"]) > 2 * r["window_sd"] and r["windows_agreeing"] == len(per_window_delta):
            r["verdict"] = "有意"
        else:
            r["verdict"] = "判定不能"
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

    base.pop("_per_window", None)
    table = pd.DataFrame(rows).sort_values("delta_logloss", ascending=False)
    click.echo("=" * 96)
    click.echo(f"{len(win_list)} ウィンドウ x {len(seed_list)} シード、market blend "
               f"{'on' if market_blend else 'off'}")
    for i, (_, _, te) in enumerate(win_list, 1):
        click.echo(f"  ウィンドウ{i}: {te['race_date'].min():%Y-%m-%d}..{te['race_date'].max():%Y-%m-%d} "
                   f"({te['race_id'].nunique()} レース)")
    click.echo("-" * 96)
    click.echo(f"{'variant':<26}{'features':>9}{'logloss':>10}{'seed sd':>9}{'win sd':>9}{'delta':>9}"
               f"{'一致':>7}{'判定':>12}")
    for _, r in table.iterrows():
        agree = "" if r["variant"].startswith("baseline") else f"{int(r.get('windows_agreeing', 0))}/{len(win_list)}"
        click.echo(f"{str(r['variant'])[:25]:<26}{int(r['n_features']):>9}{r['winner_logloss_p_win_model']:>10.4f}"
                   f"{r['logloss_sd']:>9.4f}{r['window_sd']:>9.4f}{r['delta_logloss']:>+9.4f}"
                   f"{agree:>7}{r['verdict']:>12}")
    click.echo("-" * 96)
    click.echo("delta = variant - baseline の全ウィンドウ平均。正なら、その family が無いとモデルが悪くなる。")
    click.echo("判定が 有意 になるのは、平均がウィンドウ間のばらつきの 2 倍を超え、かつ全ウィンドウで符号が揃うとき。")
    click.echo("シード分散は同じデータの揺れ、ウィンドウ分散は別の時期でも成り立つかどうか。後者が判断基準。")
    if output:
        table.to_csv(output, index=False)
        click.echo(f"wrote {output}")


if __name__ == "__main__":
    main()
