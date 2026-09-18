#!/usr/bin/env python
"""Phase 2 CLI: train LambdaMART ranker + Plackett-Luce temperature + isotonic calibration.

The data is split *chronologically* into train / calibration / test slices.
Never shuffle.

    python src/models/train_lgbm.py --data processed/train.csv --model_dir artifacts/
    python src/models/train_lgbm.py --data processed/train.csv --model_dir artifacts/ \
        --train_end 2021-12-31 --calib_end 2022-12-31

Artifacts written to --model_dir:
    ranker.txt, ranker_meta.json, pl_temperature.json, calibrator.pkl, metrics.json, feature_importance.csv
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.common.logging_utils import get_logger  # noqa: E402
from src.models.calibration import WinProbCalibrator, expected_calibration_error, reliability_table  # noqa: E402
from src.models.market_blend import MarketBlend  # noqa: E402
from src.models.plackett_luce import PLTemperature, race_win_probs_from_scores  # noqa: E402
from src.models.predict import Predictor  # noqa: E402
from src.models.ranker import RankerModel  # noqa: E402

log = get_logger("train")


def load_table(path: str) -> Tuple[pd.DataFrame, Dict]:
    df = pd.read_csv(path, parse_dates=["race_date"], dtype={"race_id": str, "entrant_id": str})
    meta_path = Path(path).with_name("features.json")
    if not meta_path.exists():
        raise FileNotFoundError(f"{meta_path} not found; run src/data/preprocess.py first")
    meta = json.loads(meta_path.read_text())
    return df.sort_values(["race_date", "race_id", "post_position"], kind="stable").reset_index(drop=True), meta


def chronological_split(df: pd.DataFrame, train_end: Optional[str], calib_end: Optional[str],
                        train_frac: float = 0.70, calib_frac: float = 0.15):
    dates = np.sort(df["race_date"].unique())
    if train_end is None:
        train_end = pd.Timestamp(dates[int(len(dates) * train_frac) - 1])
    if calib_end is None:
        calib_end = pd.Timestamp(dates[int(len(dates) * (train_frac + calib_frac)) - 1])
    train_end, calib_end = pd.Timestamp(train_end), pd.Timestamp(calib_end)
    if not train_end < calib_end:
        raise ValueError("--train_end must be before --calib_end")
    tr = df[df["race_date"] <= train_end]
    ca = df[(df["race_date"] > train_end) & (df["race_date"] <= calib_end)]
    te = df[df["race_date"] > calib_end]
    return tr, ca, te


def fit_pipeline(train: pd.DataFrame, calib: pd.DataFrame, feature_cols, categorical_cols, params=None,
                 market_blend: bool = True) -> Predictor:
    """Train ranker on ``train``; fit PL temperature, isotonic map and (optionally) the
    Benter market blend on ``calib`` (strictly later in time than ``train``)."""
    ranker = RankerModel(feature_cols, categorical_cols, params or {}) if params else RankerModel(feature_cols, categorical_cols)
    ranker.fit(train, calib)
    calib = calib.copy()
    calib["score"] = ranker.predict(calib)
    groups, winners = [], []
    for _, g in calib.groupby("race_id", sort=False):
        w = np.flatnonzero(g["finish_position"].to_numpy() == 1)
        if w.size == 1:
            groups.append(g["score"].to_numpy())
            winners.append(int(w[0]))
    temp = PLTemperature.fit(groups, winners)
    calib["p_win_raw"] = race_win_probs_from_scores(calib, "score", temp.temperature)
    won = (calib["finish_position"] == 1).to_numpy()
    calibrator = WinProbCalibrator().fit(calib["p_win_raw"].to_numpy(), won)
    blend = MarketBlend()
    if market_blend and "win_odds" in calib and calib["win_odds"].notna().all():
        p_model = calibrator.transform(calib["p_win_raw"].to_numpy(), calib["race_id"].to_numpy())
        blend = MarketBlend.fit(p_model, calib["win_odds"].to_numpy(), calib["race_id"].to_numpy(), won)
    return Predictor(ranker, temp, calibrator, blend)


def evaluate(pred: pd.DataFrame) -> Dict:
    """Ranking + probabilistic metrics on a predicted frame (needs finish_position, win_odds)."""
    won = (pred["finish_position"] == 1).to_numpy(dtype=float)
    res: Dict = {"n_races": int(pred["race_id"].nunique()), "n_rows": int(len(pred))}
    # top-1 hit rate
    top = pred.loc[pred.groupby("race_id")["score"].idxmax()]
    res["top1_hit_rate"] = float((top["finish_position"] == 1).mean())
    top3 = pred.sort_values(["race_id", "score"], ascending=[True, False]).groupby("race_id").head(3)
    res["top3_contains_winner"] = float(top3.groupby("race_id")["finish_position"].apply(lambda s: (s == 1).any()).mean())

    def logloss(p):
        p = np.clip(p, 1e-6, 1)
        return float(-np.sum(won * np.log(p)) / pred["race_id"].nunique())

    def brier(p):
        return float(np.mean((p - won) ** 2))

    for name in ("p_win_raw", "p_win_model", "p_win"):
        if name not in pred:
            continue
        res[f"winner_logloss_{name}"] = logloss(pred[name].to_numpy())
        res[f"brier_{name}"] = brier(pred[name].to_numpy())
        res[f"ece_{name}"] = expected_calibration_error(pred[name].to_numpy(), won)
    if "win_odds" in pred:
        inv = 1.0 / pred["win_odds"].clip(lower=1.01)
        p_mkt = inv / inv.groupby(pred["race_id"]).transform("sum")
        res["winner_logloss_market"] = logloss(p_mkt.to_numpy())
        res["brier_market"] = brier(p_mkt.to_numpy())
        fav = pred.loc[pred.groupby("race_id")["win_odds"].idxmin()]
        res["favourite_hit_rate"] = float((fav["finish_position"] == 1).mean())
    res["reliability_p_win"] = reliability_table(pred["p_win"].to_numpy(), won)
    return res


@click.command()
@click.option("--data", "data_path", default="processed/train.csv", show_default=True)
@click.option("--model_dir", default="artifacts/", show_default=True)
@click.option("--train_end", default=None, help="last date (inclusive) of the training slice")
@click.option("--calib_end", default=None, help="last date (inclusive) of the calibration slice; test = after")
@click.option("--market_blend/--no_market_blend", default=True, show_default=True,
              help="fit Benter conditional-logit blend of model and market probabilities on the calibration slice")
def main(data_path: str, model_dir: str, train_end: Optional[str], calib_end: Optional[str], market_blend: bool) -> None:
    df, meta = load_table(data_path)
    feature_cols, categorical_cols = meta["feature_columns"], meta["categorical_columns"]
    train, calib, test = chronological_split(df, train_end, calib_end)
    log.info("train %s..%s (%d races) | calib %s..%s (%d races) | test %s..%s (%d races)",
             train["race_date"].min().date(), train["race_date"].max().date(), train["race_id"].nunique(),
             calib["race_date"].min().date(), calib["race_date"].max().date(), calib["race_id"].nunique(),
             test["race_date"].min().date() if len(test) else None, test["race_date"].max().date() if len(test) else None,
             test["race_id"].nunique())

    predictor = fit_pipeline(train, calib, feature_cols, categorical_cols, market_blend=market_blend)
    log.info("ranker best_iteration=%d  PL temperature=%.4f  calibrator n=%d  blend(a=%.3f, b=%.3f, enabled=%s)",
             predictor.ranker.best_iteration, predictor.temperature.temperature, predictor.calibrator.n_fit,
             predictor.blend.a, predictor.blend.b, predictor.blend.enabled)

    out = Path(model_dir)
    predictor.save(out)
    predictor.ranker.feature_importance().to_csv(out / "feature_importance.csv", header=["gain"])

    metrics = {"split": {"train_end": str(train["race_date"].max().date()), "calib_end": str(calib["race_date"].max().date())},
               "best_iteration": predictor.ranker.best_iteration, "pl_temperature": predictor.temperature.temperature,
               "market_blend": predictor.blend.to_dict()}
    if len(test):
        metrics["test"] = evaluate(predictor.predict(test))
        t = metrics["test"]
        log.info("TEST  top1=%.3f (fav %.3f) | winner logloss raw=%.4f model=%.4f blend=%.4f market=%.4f | ECE model=%.4f blend=%.4f",
                 t["top1_hit_rate"], t.get("favourite_hit_rate", float("nan")), t["winner_logloss_p_win_raw"],
                 t["winner_logloss_p_win_model"], t["winner_logloss_p_win"], t.get("winner_logloss_market", float("nan")),
                 t["ece_p_win_model"], t["ece_p_win"])
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    log.info("artifacts saved to %s", out)


if __name__ == "__main__":
    main()
