"""Leak-free time-series feature engineering.

Every feature for a row (race r, entrant h) is computed from rows whose
``race_date`` is *strictly earlier* than r's date.  Two mechanisms enforce it:

* the entrant's own history uses ``groupby().shift(1)`` on a date-sorted frame,
  guarded by :func:`_same_day_guard` so a second same-day start cannot leak;
* everything else goes through :func:`_asof_cum`, which aggregates to whole
  days first and then reads the cumulative total as of an earlier day.  A
  jockey rides several races a day, so a ride-level ``shift(1)`` would let a
  later race on the same card feed an earlier one; the day boundary is the
  finest cut JV-Data timestamps let us prove is in the past.

Conditional rates (jockey by venue, horse by going, a specific horse-jockey
pair) run out of data quickly - a jockey gets about 16 rides per venue x
surface x distance cell in five years, and most horse-jockey pairs have one
ride.  A raw rate from that is noise, so every conditional rate is shrunk
toward its parent rate by :func:`_shrunk_rate` with a pseudo-count.

The functions are sport-agnostic: "entrant" may be a horse, a keirin rider or
a boat racer; jockey and trainer features are skipped when those ids equal the
entrant id.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

from src.data.schema import FORBIDDEN_FEATURE_COLUMNS

CATEGORICAL_FEATURES = ["surface", "going", "race_class", "venue", "sex"]

STATIC_FEATURES = [
    "distance_m", "n_runners", "post_position", "draw", "age", "weight_carried", "body_weight", "body_weight_diff",
    "race_no", "month",
]

#: Pseudo-counts for empirical-Bayes shrinkage: how many observations of the
#: parent rate a conditional cell must beat before it is believed on its own.
#: Larger where cells are sparse relative to the parent.
SHRINK_K: Dict[str, float] = {
    "entity": 20.0,     # an entity's overall rate toward the population rate
    "ent_cond": 4.0,    # horse x condition toward the horse's own rate (horses have few starts)
    "jky_cond": 60.0,   # jockey x condition toward the jockey's overall rate
    "pair": 8.0,        # horse x jockey toward the horse's own rate
    "tj": 25.0,         # trainer x jockey toward the trainer's overall rate
}

RECENT_WINDOW_DAYS = 90
_VALUE_COLS = ["_won", "_placed", "_norm_pos", "_speed_idx", "_resid"]


# ---------------------------------------------------------------------------
# as-of primitives
# ---------------------------------------------------------------------------
def _asof_cum(df: pd.DataFrame, keys: List[str], value_cols: Sequence[str], lag_days: int = 1) -> pd.DataFrame:
    """Cumulative totals over rows sharing ``keys`` on days before ``race_date - lag_days + 1``.

    Returns one column per value plus ``_n`` (the number of such rows).  With
    ``lag_days=1`` this is "everything before today"; differencing against
    ``lag_days=W+1`` gives the trailing W-day window.
    """
    cols = list(value_cols)
    tmp = df[keys + ["race_date"]].copy()
    for c in cols:
        tmp[c] = df[c].to_numpy(dtype=float)
    tmp["_n"] = 1.0
    cols_n = cols + ["_n"]

    daily = tmp.groupby(keys + ["race_date"], sort=True, observed=True)[cols_n].sum().reset_index()
    daily = daily.sort_values(keys + ["race_date"], kind="stable")
    daily[cols_n] = daily.groupby(keys, sort=False, observed=True)[cols_n].cumsum()
    daily = daily.sort_values("race_date", kind="stable").rename(columns={"race_date": "_d"})

    left = df[keys].copy()
    left["_asof"] = df["race_date"] - pd.Timedelta(days=lag_days)
    left["_row"] = np.arange(len(df))
    left = left.sort_values("_asof", kind="stable")

    merged = pd.merge_asof(left, daily, left_on="_asof", right_on="_d", by=keys, direction="backward")
    out = pd.DataFrame(merged[cols_n].to_numpy(dtype=float), index=merged["_row"].to_numpy(), columns=cols_n)
    out = out.sort_index().fillna(0.0)
    out.index = df.index
    return out


def _asof_days_since(df: pd.DataFrame, keys: List[str]) -> pd.Series:
    """Days since the most recent earlier day on which this key appeared."""
    daily = df.groupby(keys + ["race_date"], sort=True, observed=True).size().reset_index()[keys + ["race_date"]]
    daily["_last"] = daily["race_date"]
    daily = daily.sort_values("race_date", kind="stable").rename(columns={"race_date": "_d"})
    left = df[keys].copy()
    left["_asof"] = df["race_date"] - pd.Timedelta(days=1)
    left["_row"] = np.arange(len(df))
    left = left.sort_values("_asof", kind="stable")
    merged = pd.merge_asof(left, daily, left_on="_asof", right_on="_d", by=keys, direction="backward")
    last = pd.Series(merged["_last"].to_numpy(), index=merged["_row"].to_numpy()).sort_index()
    last.index = df.index
    return (df["race_date"] - last).dt.days


def _shrunk_rate(cell_sum: pd.Series, cell_n: pd.Series, prior: pd.Series, k: float) -> pd.Series:
    """Empirical-Bayes posterior mean: a thin cell falls back to its parent rate."""
    return (cell_sum + k * prior) / (cell_n + k)


def _rate_block(df: pd.DataFrame, keys: List[str], prefix: str, prior: pd.Series, k: float,
                measures: Sequence[Tuple[str, str]]) -> pd.DataFrame:
    """Shrunk as-of rates for one key combination.

    ``measures`` pairs the raw column with the suffix of the emitted feature,
    e.g. ``("_won", "win")`` -> ``<prefix>_win``.
    """
    cum = _asof_cum(df, keys, [m[0] for m in measures])
    out = pd.DataFrame(index=df.index)
    out[f"{prefix}_starts"] = cum["_n"]
    for col, suffix in measures:
        p = prior[col] if isinstance(prior, pd.DataFrame) else prior
        out[f"{prefix}_{suffix}"] = _shrunk_rate(cum[col], cum["_n"], p, k)
    return out


# ---------------------------------------------------------------------------
# entrant history (a horse starts once a day, so shift(1) is already day-safe)
# ---------------------------------------------------------------------------
def _as_of_group_stats(df: pd.DataFrame, key: str, prefix: str, windows: Tuple[int, ...] = (3, 10)) -> pd.DataFrame:
    g = df.groupby(key, sort=False)
    out = pd.DataFrame(index=df.index)

    prev_pos = g["finish_position"].shift(1)
    prev_won = g["_won"].shift(1)
    prev_placed = g["_placed"].shift(1)
    prev_norm = g["_norm_pos"].shift(1)
    prev_speed = g["_speed_idx"].shift(1)

    out[f"{prefix}_starts"] = g.cumcount()
    starts = out[f"{prefix}_starts"].replace(0, np.nan)
    out[f"{prefix}_win_rate"] = prev_won.groupby(df[key]).cumsum() / starts
    out[f"{prefix}_place_rate"] = prev_placed.groupby(df[key]).cumsum() / starts
    out[f"{prefix}_avg_norm_pos"] = prev_norm.groupby(df[key]).cumsum() / starts

    for w in windows:
        out[f"{prefix}_norm_pos_last{w}"] = prev_norm.groupby(df[key]).rolling(w, min_periods=1).mean().reset_index(
            level=0, drop=True)
        out[f"{prefix}_speed_last{w}"] = prev_speed.groupby(df[key]).rolling(w, min_periods=1).mean().reset_index(
            level=0, drop=True)
    out[f"{prefix}_last_pos"] = prev_pos
    out[f"{prefix}_last_norm_pos"] = prev_norm
    out[f"{prefix}_best_speed"] = prev_speed.groupby(df[key]).cummax()
    out[f"{prefix}_days_since"] = (df["race_date"] - g["race_date"].shift(1)).dt.days
    return out


def _same_day_guard(df: pd.DataFrame, key: str) -> None:
    dup = df.duplicated([key, "race_date"], keep=False)
    if dup.any():
        raise ValueError(f"{dup.sum()} rows where {key} runs twice on the same date; use a finer time key")


# ---------------------------------------------------------------------------
# main builder
# ---------------------------------------------------------------------------
def build_features(races: pd.DataFrame, entries: pd.DataFrame) -> Tuple[pd.DataFrame, List[str], Dict[str, List[str]]]:
    """Return (frame, feature_columns, feature_groups).

    ``feature_groups`` names each family so an ablation can drop one whole
    family and measure what it was worth.
    """
    df = entries.merge(races.drop(columns=["race_date"], errors="ignore"), on="race_id", how="inner", validate="m:1")
    df["race_date"] = pd.to_datetime(df["race_date"])
    df = df.sort_values(["race_date", "race_id", "post_position"]).reset_index(drop=True)
    _same_day_guard(df, "entrant_id")

    has_jockey = not (df["jockey_id"] == df["entrant_id"]).all()
    has_trainer = not (df["trainer_id"] == df["entrant_id"]).all()

    # ----- label-side helper columns (only ever read through an as-of lookup) --
    n = df["n_runners"].astype(float)
    fp = df["finish_position"].astype(float)
    fp_valid = fp.where(fp > 0)
    df["_won"] = (fp == 1).astype(float)
    df["_placed"] = ((fp >= 1) & (fp <= 3)).astype(float)
    df["_norm_pos"] = ((fp_valid - 1) / (n - 1).clip(lower=1)).fillna(1.0)  # 0 = won, 1 = last
    race_mean = df.groupby("race_id")["finish_time_sec"].transform("mean")
    race_std = df.groupby("race_id")["finish_time_sec"].transform("std").replace(0, np.nan)
    df["_speed_idx"] = (-(df["finish_time_sec"] - race_mean) / race_std).fillna(0.0)
    df["_dist_band"] = (df["distance_m"] // 400).astype(int).astype(str)
    df["_all"] = "all"

    groups: Dict[str, List[str]] = {}
    blocks: List[pd.DataFrame] = []

    def emit(group: str, frame: pd.DataFrame) -> None:
        groups.setdefault(group, []).extend([c for c in frame.columns])
        blocks.append(frame)

    # ----- entrant -----------------------------------------------------------
    ent = _as_of_group_stats(df, "entrant_id", "ent")
    emit("entrant", ent)

    # population baseline, as of earlier days, for the top of the shrinkage chain
    pop = _asof_cum(df, ["_all"], ["_won", "_placed", "_norm_pos", "_speed_idx"])
    pop_n = pop["_n"].replace(0, np.nan)
    # Fallbacks for the very first day must not be dataset-wide statistics: the
    # field size of *this* race is known before it is run, the mean field size
    # over all races is not. The leak test catches the difference.
    pop_win = (pop["_won"] / pop_n).fillna(1.0 / df["n_runners"].clip(lower=1))
    pop_norm = (pop["_norm_pos"] / pop_n).fillna(0.5)

    # A horse's own expectation, used to turn a result into a residual: how much
    # better than this horse's usual level did it run? The jockey's average
    # residual separates riding from being handed good rides.
    df["_expected"] = ent["ent_avg_norm_pos"].fillna(pop_norm).to_numpy()
    df["_resid"] = df["_norm_pos"] - df["_expected"]
    ent_prior_norm = ent["ent_avg_norm_pos"].fillna(pop_norm)

    # ----- entrant x condition ----------------------------------------------
    ent_cond = []
    for cond, name in (("surface", "surf"), ("_dist_band", "dist"), ("venue", "venue"), ("going", "going"),
                       ("race_class", "class")):
        ent_cond.append(_rate_block(df, ["entrant_id", cond], f"entc_{name}", ent_prior_norm, SHRINK_K["ent_cond"],
                                    [("_norm_pos", "norm_pos")]))
    ent_cond = pd.concat(ent_cond, axis=1)
    misc = pd.DataFrame(index=df.index)
    prev_dist = df.groupby("entrant_id")["distance_m"].shift(1)
    misc["ent_dist_change"] = df["distance_m"] - prev_dist
    class_rank = {c: i for i, c in enumerate(["maiden", "1win", "2win", "3win", "OP", "G3", "G2", "G1"])}
    prev_class = df.groupby("entrant_id")["race_class"].shift(1)
    misc["ent_class_change"] = df["race_class"].map(class_rank).astype(float) - prev_class.map(class_rank).astype(float)
    emit("entrant_cond", pd.concat([ent_cond, misc], axis=1))

    jky_win_rate = None
    if has_jockey:
        # ----- jockey base, on the day boundary ------------------------------
        cum = _asof_cum(df, ["jockey_id"], _VALUE_COLS)
        recent = cum - _asof_cum(df, ["jockey_id"], _VALUE_COLS, lag_days=RECENT_WINDOW_DAYS + 1)
        jb = pd.DataFrame(index=df.index)
        jb["jky_starts"] = cum["_n"]
        jky_win_rate = _shrunk_rate(cum["_won"], cum["_n"], pop_win, SHRINK_K["entity"])
        jb["jky_win_rate"] = jky_win_rate
        jb["jky_place_rate"] = _shrunk_rate(cum["_placed"], cum["_n"], pop_win * 3, SHRINK_K["entity"])
        jb["jky_avg_norm_pos"] = _shrunk_rate(cum["_norm_pos"], cum["_n"], pop_norm, SHRINK_K["entity"])
        jb["jky_starts_90d"] = recent["_n"]
        jb["jky_win_rate_90d"] = _shrunk_rate(recent["_won"], recent["_n"], jky_win_rate, SHRINK_K["entity"])
        jb["jky_norm_pos_90d"] = _shrunk_rate(recent["_norm_pos"], recent["_n"], jb["jky_avg_norm_pos"],
                                              SHRINK_K["entity"])
        jb["jky_days_since"] = _asof_days_since(df, ["jockey_id"])
        # skill net of the horses handed to them: negative means better than expected
        jb["jky_resid"] = _shrunk_rate(cum["_resid"], cum["_n"], pd.Series(0.0, index=df.index), SHRINK_K["entity"])
        jb["jky_resid_90d"] = _shrunk_rate(recent["_resid"], recent["_n"], jb["jky_resid"], SHRINK_K["entity"])
        emit("jockey", jb)

        # ----- jockey x condition --------------------------------------------
        jc = [
            _rate_block(df, ["jockey_id", "venue"], "jkyc_venue", pd.DataFrame(
                {"_won": jky_win_rate, "_norm_pos": jb["jky_avg_norm_pos"]}), SHRINK_K["jky_cond"],
                [("_won", "win"), ("_norm_pos", "norm_pos")]),
            _rate_block(df, ["jockey_id", "surface"], "jkyc_surf", jky_win_rate, SHRINK_K["jky_cond"],
                        [("_won", "win")]),
            _rate_block(df, ["jockey_id", "_dist_band"], "jkyc_dist", jky_win_rate, SHRINK_K["jky_cond"],
                        [("_won", "win")]),
            _rate_block(df, ["jockey_id", "going"], "jkyc_going", jb["jky_avg_norm_pos"], SHRINK_K["jky_cond"],
                        [("_norm_pos", "norm_pos")]),
        ]
        emit("jockey_cond", pd.concat(jc, axis=1))

        # ----- horse x jockey chemistry --------------------------------------
        pair = _rate_block(df, ["entrant_id", "jockey_id"], "pair", pd.DataFrame(
            {"_norm_pos": ent_prior_norm, "_resid": pd.Series(0.0, index=df.index)}), SHRINK_K["pair"],
            [("_norm_pos", "norm_pos"), ("_resid", "resid")])
        emit("pair", pair)

        # ----- jockey switch --------------------------------------------------
        sw = pd.DataFrame(index=df.index)
        prev_jockey = df.groupby("entrant_id")["jockey_id"].shift(1)
        sw["sw_same_jockey"] = (df["jockey_id"] == prev_jockey).astype(float).where(prev_jockey.notna())
        sw["sw_first_time_pair"] = (pair["pair_starts"] == 0).astype(float)
        # did the stable move up or down the jockey ranks for this start?
        prev_rate = jky_win_rate.groupby(df["entrant_id"]).shift(1)
        sw["sw_jockey_rate_delta"] = jky_win_rate - prev_rate
        emit("switch", sw)

    if has_trainer:
        cum = _asof_cum(df, ["trainer_id"], _VALUE_COLS)
        tb = pd.DataFrame(index=df.index)
        tb["trn_starts"] = cum["_n"]
        trn_win_rate = _shrunk_rate(cum["_won"], cum["_n"], pop_win, SHRINK_K["entity"])
        tb["trn_win_rate"] = trn_win_rate
        tb["trn_place_rate"] = _shrunk_rate(cum["_placed"], cum["_n"], pop_win * 3, SHRINK_K["entity"])
        tb["trn_avg_norm_pos"] = _shrunk_rate(cum["_norm_pos"], cum["_n"], pop_norm, SHRINK_K["entity"])
        tb["trn_resid"] = _shrunk_rate(cum["_resid"], cum["_n"], pd.Series(0.0, index=df.index), SHRINK_K["entity"])
        tb["trn_days_since"] = _asof_days_since(df, ["trainer_id"])
        emit("trainer", tb)
        if has_jockey:
            emit("trainer_jockey", _rate_block(df, ["trainer_id", "jockey_id"], "tj", trn_win_rate, SHRINK_K["tj"],
                                               [("_won", "win")]))

    # ----- static / categorical ---------------------------------------------
    df["month"] = df["race_date"].dt.month
    static = pd.DataFrame(index=df.index)
    for c in STATIC_FEATURES:
        static[c] = pd.to_numeric(df[c], errors="coerce")
    for c in CATEGORICAL_FEATURES:
        static[c] = df[c].astype("category")
    emit("static", static)

    feats = pd.concat(blocks, axis=1)

    # ----- within-race relative position -------------------------------------
    rel = pd.DataFrame(index=df.index)
    for col, ascending in (("ent_avg_norm_pos", True), ("ent_speed_last3", False), ("ent_best_speed", False),
                           ("jky_win_rate", False), ("jky_resid", True), ("trn_win_rate", False),
                           ("pair_norm_pos", True), ("jkyc_venue_win", False)):
        if col in feats:
            rel[f"{col}_rank"] = feats.groupby(df["race_id"])[col].rank(ascending=ascending, pct=True)
    emit("relative", rel)
    feats = pd.concat([feats, rel], axis=1)

    feature_cols = [c for c in feats.columns if c not in FORBIDDEN_FEATURE_COLUMNS]
    groups = {g: [c for c in cols if c in feature_cols] for g, cols in groups.items()}

    keep = ["race_id", "race_date", "entrant_id", "jockey_id", "trainer_id", "finish_position", "finish_time_sec",
            "win_odds", "place_odds", "popularity", "n_runners"]
    keep = [c for c in keep if c in df.columns and c not in feature_cols]
    out = pd.concat([df[keep], feats[feature_cols]], axis=1)
    out["relevance"] = np.where(fp > 0, (n - fp).clip(lower=0), 0).astype(int)
    out["relevance"] = out["relevance"].clip(upper=17)
    return out, feature_cols, groups


def assert_no_forbidden(feature_cols: List[str]) -> None:
    bad = sorted(set(feature_cols) & FORBIDDEN_FEATURE_COLUMNS)
    if bad:
        raise AssertionError(f"forbidden (leaking) feature columns present: {bad}")
