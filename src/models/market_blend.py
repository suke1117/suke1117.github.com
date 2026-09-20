"""Benter-style second stage: blend model probability with the public's odds.

Benter (1994) showed that the public's final odds contain information not in
the fundamental model, and that a conditional-logit combination

    p_final_i  propto  exp( a * log p_model_i + b * log p_market_i )

fitted on held-out races strictly dominates either input.  ``a`` and ``b`` are
fitted by maximum likelihood on the calibration slice (never on training
races).  When ``b`` is near zero the market adds nothing; when ``a`` is near
zero the model adds nothing and no positive-EV bets will be found - which is
the honest answer.

Caveat: the odds used here are *final* odds; live betting would use odds a few
minutes before the off, so a haircut is applied in the backtester.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize


def market_probs(win_odds: np.ndarray, race_ids: np.ndarray) -> np.ndarray:
    inv = 1.0 / np.clip(np.asarray(win_odds, dtype=float), 1.01, None)
    s = pd.Series(inv).groupby(np.asarray(race_ids)).transform("sum").to_numpy()
    return inv / s


def _blend(lp_model: np.ndarray, lp_mkt: np.ndarray, race_ids: np.ndarray, a: float, b: float) -> np.ndarray:
    z = a * lp_model + b * lp_mkt
    z -= pd.Series(z).groupby(np.asarray(race_ids)).transform("max").to_numpy()
    e = np.exp(z)
    return e / pd.Series(e).groupby(np.asarray(race_ids)).transform("sum").to_numpy()


@dataclass
class MarketBlend:
    a: float = 1.0   # weight on model log-prob
    b: float = 0.0   # weight on market log-prob
    enabled: bool = False

    @classmethod
    def fit(cls, p_model: np.ndarray, win_odds: np.ndarray, race_ids: np.ndarray, won: np.ndarray) -> "MarketBlend":
        lp_m = np.log(np.clip(p_model, 1e-6, 1))
        lp_k = np.log(np.clip(market_probs(win_odds, race_ids), 1e-6, 1))
        won = np.asarray(won, dtype=float)
        n_races = len(np.unique(race_ids))

        def nll(theta):
            p = _blend(lp_m, lp_k, race_ids, theta[0], theta[1])
            return -np.sum(won * np.log(np.clip(p, 1e-12, 1))) / n_races

        res = minimize(nll, x0=np.array([1.0, 0.5]), bounds=[(0.0, 3.0), (0.0, 3.0)], method="L-BFGS-B")
        return cls(float(res.x[0]), float(res.x[1]), enabled=True)

    def transform(self, p_model: np.ndarray, win_odds: Optional[np.ndarray], race_ids: np.ndarray) -> np.ndarray:
        if not self.enabled or win_odds is None:
            return np.asarray(p_model, dtype=float)
        lp_m = np.log(np.clip(p_model, 1e-6, 1))
        lp_k = np.log(np.clip(market_probs(win_odds, race_ids), 1e-6, 1))
        return _blend(lp_m, lp_k, race_ids, self.a, self.b)

    def to_dict(self) -> Dict:
        return {"a": self.a, "b": self.b, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d: Dict) -> "MarketBlend":
        return cls(float(d["a"]), float(d["b"]), bool(d["enabled"]))

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: Path) -> "MarketBlend":
        p = Path(path)
        return cls.from_dict(json.loads(p.read_text())) if p.exists() else cls()
