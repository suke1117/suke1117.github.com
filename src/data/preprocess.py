#!/usr/bin/env python
"""Phase 1 CLI: parse raw data -> leak-free feature table.

Usage:
    python src/data/preprocess.py --input raw_data/ --output processed/
    python src/data/preprocess.py --synthetic --output processed/     # force dummy data

Outputs:
    <output>/train.csv        one row per (race, entrant) with features + label + market columns
    <output>/features.json    ordered list of feature columns and categorical columns
    <output>/races.csv        normalized race history (the live path appends today's card to this)
    <output>/entries.csv      normalized entry history
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402

from src.common.logging_utils import get_logger  # noqa: E402
from src.data.features import (  # noqa: E402
    CATEGORICAL_FEATURES,
    OPTIONAL_CATEGORICAL,
    assert_no_forbidden,
    build_features,
)
from src.data.sources import CombinedSource, JRAVanCSVSource, SyntheticSource  # noqa: E402

log = get_logger("preprocess")


def _is_synthetic(source) -> bool:
    """True if any part of the data was generated rather than observed."""
    children = getattr(source, "sources", None)
    if children:
        return any(_is_synthetic(c) for c in children)
    return isinstance(source, SyntheticSource)


@click.command()
@click.option("--input", "input_dir", default="raw_data/", show_default=True, help="directory with JRA-VAN CSV exports")
@click.option("--output", "output_dir", default="processed/", show_default=True)
@click.option("--synthetic", is_flag=True, help="ignore --input and generate synthetic data")
@click.option("--start", default="2018-01-06", show_default=True, help="synthetic data start date")
@click.option("--end", default="2023-12-24", show_default=True, help="synthetic data end date")
@click.option("--seed", default=20240101, show_default=True, type=int)
@click.option("--public_noise", default=0.20, show_default=True, type=float,
              help="synthetic only: noise in the crowd's view of latent strength (lower = more efficient market)")
@click.option("--public_form_weight", default=0.6, show_default=True, type=float,
              help="synthetic only: how much the crowd relies on past form (0..1)")
@click.option("--include_nar/--jra_only", default=False, show_default=True,
              help="synthetic only: also generate 地方競馬 (weekday racing), which multiplies the bets per week")
def main(input_dir: str, output_dir: str, synthetic: bool, start: str, end: str, seed: int, public_noise: float,
         public_form_weight: float, include_nar: bool) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not synthetic and JRAVanCSVSource.has_data(input_dir):
        log.info("loading JRA-VAN CSV exports from %s", input_dir)
        source = JRAVanCSVSource(input_dir)
    else:
        if not synthetic:
            log.warning("no RA*/SE* CSV found in %s -> generating synthetic data", input_dir)
        jra = SyntheticSource(start=start, end=end, seed=seed, public_noise=public_noise,
                              public_form_weight=public_form_weight, preset="jra")
        if include_nar:
            nar = SyntheticSource(start=start, end=end, seed=seed + 1, public_form_weight=public_form_weight,
                                  preset="nar", n_horses=3200, n_jockeys=180, n_trainers=200)
            source = CombinedSource([jra, nar])
            log.info("generating JRA + 地方競馬")
        else:
            source = jra

    races, entries = source.load_validated()
    log.info("loaded %d races / %d entries (%s .. %s)", len(races), len(entries),
             races["race_date"].min().date(), races["race_date"].max().date())

    table, feature_cols, feature_groups = build_features(races, entries)
    assert_no_forbidden(feature_cols)
    log.info("built %d features for %d rows | groups: %s", len(feature_cols), len(table),
             ", ".join(f"{g}={len(c)}" for g, c in feature_groups.items()))

    table.to_csv(out / "train.csv", index=False)
    # the live paper-betting path re-derives features from history + today's card,
    # so the normalized frames have to survive, not just the feature table
    races.to_csv(out / "races.csv", index=False)
    entries.to_csv(out / "entries.csv", index=False)
    meta = {
        "feature_columns": feature_cols,
        # every categorical the builder actually emitted, including the optional
        # ones: a categorical left out here reaches LightGBM as a raw string
        "categorical_columns": [c for c in CATEGORICAL_FEATURES + OPTIONAL_CATEGORICAL if c in feature_cols],
        "n_rows": int(len(table)),
        "n_races": int(table["race_id"].nunique()),
        "date_min": str(table["race_date"].min().date()),
        "date_max": str(table["race_date"].max().date()),
        "source": type(source).__name__,
        # Never infer this from the class name. A wrapper class silently turned
        # "合成データによるデモ" into "実データによる結果です" on the dashboard,
        # which is the worst thing this page can get wrong.
        "is_synthetic": _is_synthetic(source),
        "organizers": sorted(races["organizer"].dropna().unique().tolist()) if "organizer" in races else ["JRA"],
        "race_days": int(races["race_date"].nunique()),
        "feature_groups": feature_groups,
    }
    (out / "features.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log.info("wrote %s, %s, %s, %s", out / "train.csv", out / "features.json", out / "races.csv",
             out / "entries.csv")


if __name__ == "__main__":
    main()
