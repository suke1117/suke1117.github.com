"""Isotonic probability calibration for win probabilities.

Fit on *out-of-sample, chronologically later* predictions.  After the
monotone map is applied we renormalise within each race so the field's win
probabilities still sum to one (a requirement for the Kelly optimiser and for
consistent exotic-ticket probabilities).
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression


@dataclass
class WinProbCalibrator:
    model: IsotonicRegression = field(default_factory=lambda: IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1.0))
    fitted: bool = False
    n_fit: int = 0

    def fit(self, p_raw: np.ndarray, won: np.ndarray) -> "WinProbCalibrator":
        p_raw = np.asarray(p_raw, dtype=float)
        won = np.asarray(won, dtype=float)
        if p_raw.shape[0] < 50:
            raise ValueError("need at least 50 samples to fit isotonic calibration")
        self.model.fit(p_raw, won)
        self.fitted, self.n_fit = True, int(p_raw.shape[0])
        return self

    def transform(self, p_raw: np.ndarray, race_ids: np.ndarray) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("calibrator not fitted")
        p = self.model.predict(np.asarray(p_raw, dtype=float))
        p = np.clip(p, 1e-4, 1.0)
        s = pd.Series(p).groupby(np.asarray(race_ids)).transform("sum").to_numpy()
        return p / s

    def save(self, path: Path) -> None:
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path: Path) -> "WinProbCalibrator":
        with open(path, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, WinProbCalibrator):
            raise TypeError("not a WinProbCalibrator pickle")
        return obj


def reliability_table(p: np.ndarray, won: np.ndarray, n_bins: int = 10) -> List[Dict[str, float]]:
    """Reliability diagram data: predicted vs observed win rate by bucket."""
    p, won = np.asarray(p, dtype=float), np.asarray(won, dtype=float)
    edges = np.quantile(p, np.linspace(0, 1, n_bins + 1))
    edges[0], edges[-1] = 0.0, 1.0 + 1e-9
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if m.sum() == 0:
            continue
        rows.append({"bin": b, "n": int(m.sum()), "p_mean": float(p[m].mean()), "obs_rate": float(won[m].mean())})
    return rows


def expected_calibration_error(p: np.ndarray, won: np.ndarray, n_bins: int = 10) -> float:
    rows = reliability_table(p, won, n_bins)
    n = sum(r["n"] for r in rows)
    return float(sum(r["n"] / n * abs(r["p_mean"] - r["obs_rate"]) for r in rows)) if n else float("nan")
