#!/usr/bin/env python
"""Phase 1 CLI: parse raw data -> leak-free feature table.

Usage:
    python src/data/preprocess.py --input raw_data/ --output processed/
    python src/data/preprocess.py --synthetic --output processed/     # force dummy data

Outputs:
    <output>/train.csv        one row per (race, entrant) with features + label + market columns
    <output>/features.json    ordered list of feature columns and categorical columns
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402

from src.common.logging_utils import get_logger  # noqa: E402
from src.data.features import CATEGORICAL_FEATURES, assert_no_forbidden, build_features  # noqa: E402
from src.data.sources import JRAVanCSVSource, SyntheticSource  # noqa: E402

log = get_logger("preprocess")


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
def main(input_dir: str, output_dir: str, synthetic: bool, start: str, end: str, seed: int, public_noise: float,
         public_form_weight: float) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not synthetic and JRAVanCSVSource.has_data(input_dir):
        log.info("loading JRA-VAN CSV exports from %s", input_dir)
        source = JRAVanCSVSource(input_dir)
    else:
        if not synthetic:
            log.warning("no RA*/SE* CSV found in %s -> generating synthetic data", input_dir)
        source = SyntheticSource(start=start, end=end, seed=seed, public_noise=public_noise,
                                 public_form_weight=public_form_weight)

    races, entries = source.load_validated()
    log.info("loaded %d races / %d entries (%s .. %s)", len(races), len(entries),
             races["race_date"].min().date(), races["race_date"].max().date())

    table, feature_cols, feature_groups = build_features(races, entries)
    assert_no_forbidden(feature_cols)
    log.info("built %d features for %d rows | groups: %s", len(feature_cols), len(table),
             ", ".join(f"{g}={len(c)}" for g, c in feature_groups.items()))

    table.to_csv(out / "train.csv", index=False)
    meta = {
        "feature_columns": feature_cols,
        "categorical_columns": [c for c in CATEGORICAL_FEATURES if c in feature_cols],
        "n_rows": int(len(table)),
        "n_races": int(table["race_id"].nunique()),
        "date_min": str(table["race_date"].min().date()),
        "date_max": str(table["race_date"].max().date()),
        "source": type(source).__name__,
        "feature_groups": feature_groups,
    }
    (out / "features.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log.info("wrote %s and %s", out / "train.csv", out / "features.json")


if __name__ == "__main__":
    main()
