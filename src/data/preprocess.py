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
from typing import Dict, List

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


def report_input(input_dir: str) -> bool:
    """Print what a directory of CSVs would give the pipeline. True if usable.

    Every real-data route ends at the same question - "does this file have what
    the model needs?" - and answering it by running the whole import and reading
    a traceback is a bad loop. This answers it from the headers alone, and
    prints the mapping.yml lines that would close the gap.
    """
    from src.data.sources.jravan_csv import MAPPING_FILE, diagnose

    try:
        rep = diagnose(input_dir)
    except Exception as exc:  # a broken mapping.yml or an unreadable CSV
        click.echo(f"{input_dir} を読めませんでした: {exc}")
        return False

    click.echo("=" * 78)
    click.echo(f"{input_dir} を検査しました" +
               (f"  (設定: {rep['mapping_file']})" if rep["mapping_file"] else ""))
    click.echo("-" * 78)
    fixes: Dict[str, List[str]] = {}
    for table, info in rep["tables"].items():
        ja = "レース" if table == "races" else "出走馬"
        if info.get("error"):
            click.echo(f"{ja:<6}: {info['error']}")
            continue
        click.echo(f"{ja:<6}: {Path(info['path']).name}  {info['n_rows']:,} 行")
        click.echo(f"        認識できた列: {', '.join(info['mapped']) or 'なし'}")
        if info["missing_required"]:
            click.echo(f"        ★足りない必須列: {', '.join(info['missing_required'])}")
            fixes[table] = info["missing_required"]
        if info["missing_optional"]:
            click.echo(f"        無くても動く列: {', '.join(info['missing_optional'])}")
        for w in info.get("warnings", []):
            click.echo(f"        ⚠ {w}")
        if info["unmapped_columns"]:
            shown = info["unmapped_columns"][:14]
            more = "" if len(info["unmapped_columns"]) <= 14 else f" ほか{len(info['unmapped_columns']) - 14}列"
            click.echo(f"        使われていない列: {', '.join(shown)}{more}")
    click.echo("-" * 78)
    if all(info.get("error") for info in rep["tables"].values()):
        click.echo("CSV が 1 つも見つかりません。レース単位のファイルと出走馬単位のファイルを "
                   f"{input_dir} に置いてください。")
        click.echo(f"ファイル名が RA*/SE* でない場合は {Path(input_dir) / MAPPING_FILE} に:")
        click.echo("")
        click.echo("  files:")
        click.echo('    races: "*race*.csv"')
        click.echo('    entries: "*result*.csv"')
        return False
    if rep["usable"]:
        click.echo("このデータで学習まで通ります:")
        click.echo(f"  python src/data/preprocess.py --input {input_dir} --output processed/")
        return True
    if fixes:
        click.echo(f"足りない列が、上の「使われていない列」の中に別名で入っていませんか。"
                   f"入っていれば {Path(input_dir) / MAPPING_FILE} に対応を書けばコードは触らずに通ります:")
        click.echo("")
        for table, cols in fixes.items():
            click.echo(f"  {table}:")
            for c in cols:
                click.echo(f"    <その列の見出し>: {c}")
        click.echo("")
        click.echo("見出しが本当に存在しない場合、その列はこのデータでは取れません。"
                   "必須列が 1 つでも欠けると as-of 特徴量か精算のどちらかが作れなくなります。")
    return False


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
@click.option("--check", is_flag=True,
              help="inspect --input and report what maps, what is missing, and the mapping.yml that would fix it")
def main(input_dir: str, output_dir: str, synthetic: bool, start: str, end: str, seed: int, public_noise: float,
         public_form_weight: float, include_nar: bool, check: bool) -> None:
    if check:
        raise SystemExit(0 if report_input(input_dir) else 1)
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
