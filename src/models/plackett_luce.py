"""Plackett-Luce probability model.

Given per-entrant strength scores ``s`` (LightGBM ranker output), the PL model
assigns

    P(i wins)                    = exp(s_i/T) / sum_j exp(s_j/T)
    P(i first, j second)         = P(i) * p_j / (1 - p_i)
    P(i, j, k in that order)     = P(i) * p_j/(1-p_i) * p_k/(1-p_i-p_j)

with temperature ``T`` fitted by maximum likelihood on the winners of a
held-out (chronologically later) slice.  All exotic ticket probabilities are
derived from the same ``p`` so they are mutually consistent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize_scalar


def softmax(x: np.ndarray) -> np.ndarray:
    z = x - np.max(x)
    e = np.exp(z)
    return e / e.sum()


def win_probs(scores: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    scores = np.asarray(scores, dtype=float)
    if scores.ndim != 1:
        raise ValueError("scores must be 1-D (one race)")
    return softmax(scores / temperature)


# ---------------------------------------------------------------------------
# Running-down: Harville vs the discounted (Stern / Henery) model
# ---------------------------------------------------------------------------
# Plain Plackett-Luce ("Harville") says the race for second place runs on the
# same strengths as the race for first. Observed racing does not behave that
# way: the strong horse's chance of finishing second is systematically lower
# than Harville predicts, because a horse that fails to win has usually had a
# bad trip rather than merely been out-kicked. The standard fix is to raise the
# strengths to a power below one at each later stage, which flattens the field:
#
#     P(j second | i first) = p_j^lam / sum_{k != i} p_k^lam
#
# lam = 1 recovers Harville exactly, so every caller keeps working unchanged
# and the discount only applies once it has been fitted.
def _stage_weights(p: np.ndarray, power: float) -> np.ndarray:
    if power == 1.0:
        return p
    return np.power(np.clip(p, 1e-12, None), power)


def exacta_probs(p: np.ndarray, lam: float = 1.0) -> np.ndarray:
    """Matrix M[i, j] = P(i first, j second); diagonal is 0."""
    p = np.asarray(p, dtype=float)
    w = _stage_weights(p, lam)
    total = w.sum()
    denom = np.clip(total - w, 1e-12, None)          # weight left after i is removed
    m = np.outer(p / denom, w)
    np.fill_diagonal(m, 0.0)
    return m


def quinella_probs(p: np.ndarray, lam: float = 1.0) -> np.ndarray:
    m = exacta_probs(p, lam)
    return m + m.T


def trifecta_probs(p: np.ndarray, lam: float = 1.0, mu: Optional[float] = None) -> np.ndarray:
    """Tensor T[i, j, k] = P(i first, j second, k third); zero when indices repeat."""
    p = np.asarray(p, dtype=float)
    n = p.shape[0]
    mu = lam if mu is None else mu
    ex = exacta_probs(p, lam)
    w3 = _stage_weights(p, mu)
    total3 = w3.sum()
    denom = np.clip(total3 - w3[:, None] - w3[None, :], 1e-12, None)   # weight left after i and j
    t = ex[:, :, None] * (w3[None, None, :] / denom[:, :, None])
    idx = np.arange(n)
    t[idx, :, idx] = 0.0
    t[:, idx, idx] = 0.0
    t[idx, idx, :] = 0.0
    return t


def trio_probs(p: np.ndarray, lam: float = 1.0, mu: Optional[float] = None) -> np.ndarray:
    """Symmetric tensor with P({i, j, k} are the top three) in every permutation slot."""
    t = trifecta_probs(p, lam, mu)
    s = t + t.transpose(0, 2, 1) + t.transpose(1, 0, 2) + t.transpose(1, 2, 0) + t.transpose(2, 0, 1) + t.transpose(2, 1, 0)
    return s


def place_probs(p: np.ndarray, k: int = 3, lam: float = 1.0, mu: Optional[float] = None) -> np.ndarray:
    """P(entrant finishes in top-k).  Exact for k <= 3, Monte-Carlo otherwise."""
    p = np.asarray(p, dtype=float)
    n = p.shape[0]
    k = min(k, n)
    if k == 1:
        return p.copy()
    if k == 2:
        return p + exacta_probs(p, lam).sum(axis=0)
    if k == 3:
        return p + exacta_probs(p, lam).sum(axis=0) + trifecta_probs(p, lam, mu).sum(axis=(0, 1))
    return _place_probs_mc(p, k)


def _place_probs_mc(p: np.ndarray, k: int, n_samples: int = 20000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    log_p = np.log(np.clip(p, 1e-12, None))
    g = rng.gumbel(size=(n_samples, p.shape[0]))
    order = np.argsort(-(log_p[None, :] + g), axis=1)[:, :k]
    counts = np.zeros(p.shape[0])
    np.add.at(counts, order.ravel(), 1)
    return counts / n_samples


def sample_finish_order(p: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    log_p = np.log(np.clip(p, 1e-12, None))
    return np.argsort(-(log_p + rng.gumbel(size=p.shape[0])))


# ---------------------------------------------------------------------------
# temperature fitting
# ---------------------------------------------------------------------------
@dataclass
class PLTemperature:
    temperature: float = 1.0

    @staticmethod
    def _nll(temperature: float, groups: Sequence[np.ndarray], winners: Sequence[int]) -> float:
        nll = 0.0
        for s, w in zip(groups, winners):
            p = win_probs(s, temperature)
            nll -= np.log(max(p[w], 1e-12))
        return nll / max(len(groups), 1)

    @classmethod
    def fit(cls, groups: Sequence[np.ndarray], winners: Sequence[int], bounds: Tuple[float, float] = (0.05, 20.0)) -> "PLTemperature":
        """Maximum-likelihood temperature for P(winner) over many races."""
        if len(groups) == 0:
            return cls(1.0)
        res = minimize_scalar(lambda t: cls._nll(t, groups, winners), bounds=bounds, method="bounded")
        return cls(float(res.x))

    def to_dict(self) -> Dict[str, float]:
        return {"temperature": self.temperature}

    @classmethod
    def from_dict(cls, d: Dict[str, float]) -> "PLTemperature":
        return cls(float(d["temperature"]))


@dataclass
class RunDownDiscount:
    """The stage powers that turn Harville into the observed running-down.

    Fitted on races where second and third are known, by maximum likelihood on
    the conditional stages only - first place is already handled by the win
    probabilities and must not be re-fitted here.
    """
    lam: float = 1.0
    mu: float = 1.0
    fitted: bool = False
    n_races: int = 0

    @staticmethod
    def _stage_nll(power: float, groups: Sequence[np.ndarray], removed: Sequence[Sequence[int]],
                   chosen: Sequence[int]) -> float:
        nll = 0.0
        for p, gone, pick in zip(groups, removed, chosen):
            w = _stage_weights(np.asarray(p, dtype=float), power)
            mask = np.ones(w.shape[0], dtype=bool)
            mask[list(gone)] = False
            denom = w[mask].sum()
            nll -= np.log(max(w[pick] / denom, 1e-12)) if denom > 0 else 0.0
        return nll / max(len(groups), 1)

    @classmethod
    def fit(cls, probs: Sequence[np.ndarray], first: Sequence[int], second: Sequence[int],
            third: Optional[Sequence[int]] = None, bounds: Tuple[float, float] = (0.2, 1.6)) -> "RunDownDiscount":
        if not probs:
            return cls()
        lam_res = minimize_scalar(
            lambda x: cls._stage_nll(x, probs, [[f] for f in first], second), bounds=bounds, method="bounded")
        lam = float(lam_res.x)
        mu = lam
        if third is not None and len(third) == len(probs):
            mu_res = minimize_scalar(
                lambda x: cls._stage_nll(x, probs, [[f, s] for f, s in zip(first, second)], third),
                bounds=bounds, method="bounded")
            mu = float(mu_res.x)
        return cls(lam=lam, mu=mu, fitted=True, n_races=len(probs))

    def to_dict(self) -> Dict[str, float]:
        return {"lam": self.lam, "mu": self.mu, "fitted": self.fitted, "n_races": self.n_races}

    @classmethod
    def from_dict(cls, d: Dict) -> "RunDownDiscount":
        return cls(float(d.get("lam", 1.0)), float(d.get("mu", 1.0)), bool(d.get("fitted", False)),
                   int(d.get("n_races", 0)))


def race_win_probs_from_scores(df, score_col: str, temperature: float, race_col: str = "race_id") -> np.ndarray:
    """Vectorised per-race softmax for a long DataFrame."""
    s = df[score_col].to_numpy(dtype=float) / temperature
    grp = df[race_col].to_numpy()
    out = np.empty_like(s)
    # groups are contiguous when df is sorted by race_id; handle generally anyway
    import pandas as pd

    codes, _ = pd.factorize(grp)
    order = np.argsort(codes, kind="stable")
    s_sorted, codes_sorted = s[order], codes[order]
    bounds = np.flatnonzero(np.diff(codes_sorted)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(s)]])
    res_sorted = np.empty_like(s_sorted)
    for a, b in zip(starts, ends):
        res_sorted[a:b] = softmax(s_sorted[a:b])
    out[order] = res_sorted
    return out
