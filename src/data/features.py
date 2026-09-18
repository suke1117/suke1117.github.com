"""Leak-free time-series feature engineering.

Every feature for a row (race r, entrant h) is computed from rows whose
``race_date`` is *strictly earlier* than r's date.  We implement this with
``groupby().shift(1)`` on frames sorted by date, plus an explicit same-day
guard.  Result columns of the current race are never read.

The functions are sport-agnostic: "entrant" may be a horse, a keirin rider or
a boat racer; "jockey"/"trainer" features are skipped when those ids equal the
entrant id.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd

from src.data.schema import FORBIDDEN_FEATURE_COLUMNS

CATEGORICAL_FEATURES = ["surface", "going", "race_class", "venue", "sex"]

STATIC_FEATURES = [
    "distance_m", "n_runners", "post_position", "draw", "age", "weight_carried", "body_weight", "body_weight_diff",
    "race_no", "month",
]


def _as_of_group_stats(df: pd.DataFrame, key: str, prefix: str, windows: Tuple[int, ...] = (3, 10)) -> pd.DataFrame:
    """Expanding / rolling statistics of *previous* results for ``key``.

    ``df`` must be sorted by (race_date, race_id).  All statistics use
    ``shift(1)`` so the current race is excluded.
    """
    g = df.groupby(key, sort=False)
    out = pd.DataFrame(index=df.index)

    prev_pos = g["finish_position"].shift(1)
    prev_won = g["_won"].shift(1)
    prev_placed = g["_placed"].shift(1)
    prev_norm = g["_norm_pos"].shift(1)
    prev_speed = g["_speed_idx"].shift(1)

    # career counts (previous starts)
    out[f"{prefix}_starts"] = g.cumcount()
    # expanding means over previous races
    cum_won = prev_won.groupby(df[key]).cumsum()
    cum_placed = prev_placed.groupby(df[key]).cumsum()
    starts = out[f"{prefix}_starts"].replace(0, np.nan)
    out[f"{prefix}_win_rate"] = cum_won / starts
    out[f"{prefix}_place_rate"] = cum_placed / starts
    out[f"{prefix}_avg_norm_pos"] = prev_norm.groupby(df[key]).cumsum() / starts

    for w in windows:
        roll = prev_norm.groupby(df[key]).rolling(w, min_periods=1).mean().reset_index(level=0, drop=True)
        out[f"{prefix}_norm_pos_last{w}"] = roll
        roll_s = prev_speed.groupby(df[key]).rolling(w, min_periods=1).mean().reset_index(level=0, drop=True)
        out[f"{prefix}_speed_last{w}"] = roll_s
    out[f"{prefix}_last_pos"] = prev_pos
    out[f"{prefix}_last_norm_pos"] = prev_norm
    out[f"{prefix}_best_speed"] = prev_speed.groupby(df[key]).cummax()

    # days since previous start
    prev_date = g["race_date"].shift(1)
    out[f"{prefix}_days_since"] = (df["race_date"] - prev_date).dt.days
    return out


def _same_day_guard(df: pd.DataFrame, key: str) -> None:
    """Fail loudly if an entrant appears twice on the same date.

    shift(1) would then leak a same-day result.  For JRA horses this cannot
    happen; for keirin/kyotei (multiple heats per day) a date+session key
    should be used instead of the plain date before calling the builder.
    """
    dup = df.duplicated([key, "race_date"], keep=False)
    if dup.any():
        raise ValueError(f"{dup.sum()} rows where {key} runs twice on the same date; use a finer time key")


def build_features(races: pd.DataFrame, entries: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Return (frame, feature_columns).

    The frame keeps identifiers, the label ``relevance`` and market columns
    (``win_odds`` etc.) for the backtester, but ``feature_columns`` never
    includes anything from ``FORBIDDEN_FEATURE_COLUMNS``.
    """
    df = entries.merge(races.drop(columns=["race_date"], errors="ignore"), on="race_id", how="inner", validate="m:1")
    df["race_date"] = pd.to_datetime(df["race_date"])
    df = df.sort_values(["race_date", "race_id", "post_position"]).reset_index(drop=True)
    _same_day_guard(df, "entrant_id")

    # ----- helper columns derived from *this* race's result (label-side only) --
    n = df["n_runners"].astype(float)
    fp = df["finish_position"].astype(float)
    fp_valid = fp.where(fp > 0)
    df["_won"] = (fp == 1).astype(float)
    df["_placed"] = ((fp >= 1) & (fp <= 3)).astype(float)
    df["_norm_pos"] = ((fp_valid - 1) / (n - 1).clip(lower=1)).fillna(1.0)  # 0 = won, 1 = last
    # race-standardised time ("speed index"); larger = faster.  Only used through shift(1).
    t = df["finish_time_sec"]
    race_mean = df.groupby("race_id")["finish_time_sec"].transform("mean")
    race_std = df.groupby("race_id")["finish_time_sec"].transform("std").replace(0, np.nan)
    df["_speed_idx"] = (-(t - race_mean) / race_std).fillna(0.0)

    # ----- as-of statistics -------------------------------------------------
    parts = [_as_of_group_stats(df, "entrant_id", "ent")]
    if not (df["jockey_id"] == df["entrant_id"]).all():
        parts.append(_as_of_group_stats(df, "jockey_id", "jky", windows=(20,)))
    if not (df["trainer_id"] == df["entrant_id"]).all():
        parts.append(_as_of_group_stats(df, "trainer_id", "trn", windows=(20,)))
    feats = pd.concat(parts, axis=1)

    # same-condition history for the entrant (surface, distance band)
    df["_dist_band"] = (df["distance_m"] // 400).astype(int)
    for cond, name in (("surface", "surf"), ("_dist_band", "dist")):
        key = df["entrant_id"] + "|" + df[cond].astype(str)
        tmp = df.assign(_k=key)
        g = tmp.groupby("_k", sort=False)
        prev = g["_norm_pos"].shift(1)
        cnt = g.cumcount()
        feats[f"ent_{name}_starts"] = cnt
        feats[f"ent_{name}_avg_norm_pos"] = prev.groupby(tmp["_k"]).cumsum() / cnt.replace(0, np.nan)

    # distance change vs previous start
    prev_dist = df.groupby("entrant_id")["distance_m"].shift(1)
    feats["ent_dist_change"] = df["distance_m"] - prev_dist
    prev_class = df.groupby("entrant_id")["race_class"].shift(1)
    class_rank = {c: i for i, c in enumerate(["maiden", "1win", "2win", "3win", "OP", "G3", "G2", "G1"])}
    feats["ent_class_change"] = df["race_class"].map(class_rank).astype(float) - prev_class.map(class_rank).astype(float)

    # static / categorical
    df["month"] = df["race_date"].dt.month
    for c in STATIC_FEATURES:
        feats[c] = pd.to_numeric(df[c], errors="coerce")
    for c in CATEGORICAL_FEATURES:
        feats[c] = df[c].astype("category")

    # relative-to-field features (rank within race of a past-only stat is still past-only)
    for col in ("ent_avg_norm_pos", "ent_speed_last3", "ent_best_speed", "jky_win_rate", "trn_win_rate"):
        if col in feats:
            feats[f"{col}_rank"] = feats.groupby(df["race_id"])[col].rank(ascending=col.endswith("norm_pos"), pct=True)

    feature_cols = [c for c in feats.columns if c not in FORBIDDEN_FEATURE_COLUMNS]

    # ----- assemble output -------------------------------------------------
    keep = ["race_id", "race_date", "entrant_id", "jockey_id", "trainer_id", "finish_position", "finish_time_sec",
            "win_odds", "place_odds", "popularity", "n_runners"]
    keep = [c for c in keep if c in df.columns and c not in feature_cols]  # never duplicate a column
    out = pd.concat([df[keep], feats[feature_cols]], axis=1)
    # ranking label: higher is better; 0 for DNF
    out["relevance"] = np.where(fp > 0, (n - fp).clip(lower=0), 0).astype(int)
    out["relevance"] = out["relevance"].clip(upper=17)
    return out, feature_cols


def assert_no_forbidden(feature_cols: List[str]) -> None:
    bad = sorted(set(feature_cols) & FORBIDDEN_FEATURE_COLUMNS)
    if bad:
        raise AssertionError(f"forbidden (leaking) feature columns present: {bad}")
