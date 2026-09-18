"""LightGBM LambdaMART ranker wrapper (one query = one race)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.common.config import LGBM_DEFAULT_PARAMS, LGBM_EARLY_STOPPING_ROUNDS, LGBM_NUM_BOOST_ROUND
from src.data.schema import FORBIDDEN_FEATURE_COLUMNS


def _group_sizes(race_ids: pd.Series) -> np.ndarray:
    """Group sizes for LightGBM; requires rows of a race to be contiguous."""
    codes = pd.factorize(race_ids)[0]
    if np.any(np.diff(codes) < 0) or len(np.unique(codes)) != (np.diff(codes) != 0).sum() + 1:
        raise ValueError("rows must be sorted so each race_id forms one contiguous block")
    _, sizes = np.unique(codes, return_counts=True)
    return sizes[np.argsort(np.unique(codes, return_index=True)[1])]


def prepare_matrix(df: pd.DataFrame, feature_cols: List[str], categorical_cols: List[str]) -> pd.DataFrame:
    x = df[feature_cols].copy()
    for c in categorical_cols:
        x[c] = x[c].astype("category")
    return x


@dataclass
class RankerModel:
    feature_cols: List[str]
    categorical_cols: List[str]
    params: Dict = field(default_factory=lambda: dict(LGBM_DEFAULT_PARAMS))
    booster: Optional[lgb.Booster] = None
    best_iteration: int = 0
    category_levels: Dict[str, List[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        bad = sorted(set(self.feature_cols) & FORBIDDEN_FEATURE_COLUMNS)
        if bad:
            raise ValueError(f"forbidden feature columns: {bad}")

    # -- training ----------------------------------------------------------
    def _matrix(self, df: pd.DataFrame, fit_levels: bool = False) -> pd.DataFrame:
        x = df[self.feature_cols].copy()
        for c in self.categorical_cols:
            if fit_levels:
                self.category_levels[c] = sorted(map(str, x[c].dropna().unique()))
            x[c] = pd.Categorical(x[c].astype(str), categories=self.category_levels[c])
        return x

    def fit(self, train: pd.DataFrame, valid: Optional[pd.DataFrame] = None, num_boost_round: int = LGBM_NUM_BOOST_ROUND,
            early_stopping_rounds: int = LGBM_EARLY_STOPPING_ROUNDS) -> "RankerModel":
        train = train.sort_values(["race_date", "race_id"], kind="stable")
        x_tr = self._matrix(train, fit_levels=True)
        d_tr = lgb.Dataset(x_tr, label=train["relevance"].to_numpy(), group=_group_sizes(train["race_id"]),
                           categorical_feature=self.categorical_cols, free_raw_data=False)
        valid_sets, callbacks = [d_tr], [lgb.log_evaluation(0)]
        if valid is not None and len(valid) > 0:
            if valid["race_date"].min() < train["race_date"].max():
                raise ValueError("validation slice must be strictly after the training slice (time-ordered)")
            valid = valid.sort_values(["race_date", "race_id"], kind="stable")
            d_va = lgb.Dataset(self._matrix(valid), label=valid["relevance"].to_numpy(), group=_group_sizes(valid["race_id"]),
                               reference=d_tr, categorical_feature=self.categorical_cols, free_raw_data=False)
            valid_sets = [d_va]
            callbacks.append(lgb.early_stopping(early_stopping_rounds, verbose=False))
        self.booster = lgb.train(self.params, d_tr, num_boost_round=num_boost_round, valid_sets=valid_sets, callbacks=callbacks)
        self.best_iteration = self.booster.best_iteration or num_boost_round
        return self

    # -- inference ---------------------------------------------------------
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self.booster is None:
            raise RuntimeError("model not trained")
        return self.booster.predict(self._matrix(df), num_iteration=self.best_iteration or None)

    def feature_importance(self) -> pd.Series:
        imp = self.booster.feature_importance(importance_type="gain")
        return pd.Series(imp, index=self.feature_cols).sort_values(ascending=False)

    # -- persistence -------------------------------------------------------
    def save(self, model_dir: Path) -> None:
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(model_dir / "ranker.txt"), num_iteration=self.best_iteration or None)
        meta = {"feature_cols": self.feature_cols, "categorical_cols": self.categorical_cols, "params": self.params,
                "best_iteration": self.best_iteration, "category_levels": self.category_levels}
        (model_dir / "ranker_meta.json").write_text(json.dumps(meta, indent=2))

    @classmethod
    def load(cls, model_dir: Path) -> "RankerModel":
        model_dir = Path(model_dir)
        meta = json.loads((model_dir / "ranker_meta.json").read_text())
        obj = cls(meta["feature_cols"], meta["categorical_cols"], meta["params"])
        obj.booster = lgb.Booster(model_file=str(model_dir / "ranker.txt"))
        obj.best_iteration = meta["best_iteration"]
        obj.category_levels = meta["category_levels"]
        return obj
