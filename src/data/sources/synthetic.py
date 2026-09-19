"""Synthetic horse-racing generator for tests and dry runs.

The generator has a *latent* structure the model can learn (horse ability that
drifts over a career, jockey and trainer skill, going/distance preferences) and
a *public* that prices tickets with noisy information about that structure.
Because the public's estimate is noisier than what the feature builder can
recover from past results, a well-calibrated model can find positive-EV
tickets - which is exactly the property the backtester needs to exercise.

The latent structure deliberately includes the effects the conditional feature
families target, because a feature can only be shown to work against a world
that contains the thing it looks for:

* **jockey skill varies by condition** - a venue affinity, a turf/dirt lean and
  a sprint/stayer lean, on top of the jockey's overall skill.
* **horse-jockey chemistry** - a low-rank interaction, so some pairs beat what
  either party's averages predict.
* **confounded booking** - better horses attract better jockeys, so a raw
  jockey win rate measures "gets good rides" as much as "rides well". This is
  what the residual-based jockey features exist to separate.
* **jockey continuity** - a horse tends to keep its regular jockey, so a switch
  carries information.

The public sees the overall jockey skill but only ``public_fine_awareness`` of
the conditional and chemistry terms, which is where the exploitable edge lives.
Nothing here says real racing works this way; it says the pipeline can recover
an effect of this shape without leaking.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from src.common.config import JRA_WIN_TAKEOUT, RANDOM_SEED
from src.common.sport import HORSE_RACING
from src.data.sources.base import DataSource

VENUES = ["05", "06", "09", "08", "07", "01", "03", "04", "10", "02"]  # 東京, 中山, 阪神, 京都, 中京, 札幌, 福島, 新潟, 小倉, 函館
CLASSES = ["maiden", "1win", "2win", "3win", "OP", "G3", "G2", "G1"]
CLASS_LEVEL = {c: i for i, c in enumerate(CLASSES)}
GOINGS = ["good", "yielding", "soft", "heavy"]
GOING_P = [0.70, 0.15, 0.10, 0.05]
DISTANCES = [1200, 1400, 1600, 1800, 2000, 2200, 2400]


def _plackett_luce_order(strength: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Sample a full finishing order from a Plackett-Luce model (Gumbel trick)."""
    g = rng.gumbel(size=strength.shape[0])
    return np.argsort(-(strength + g))


class SyntheticSource(DataSource):
    sport = HORSE_RACING

    def __init__(
        self,
        start: str = "2018-01-06",
        end: str = "2023-12-24",
        n_horses: int = 2500,
        n_jockeys: int = 120,
        n_trainers: int = 150,
        races_per_day: int = 24,
        seed: int = RANDOM_SEED,
        public_noise: float = 0.20,
        public_form_weight: float = 0.6,
        public_fine_awareness: float = 0.5,
        keep_jockey_prob: float = 0.62,
    ):
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        self.n_horses, self.n_jockeys, self.n_trainers = n_horses, n_jockeys, n_trainers
        self.races_per_day = races_per_day
        self.seed = seed
        self.public_noise = public_noise
        self.public_form_weight = public_form_weight
        self.public_fine_awareness = public_fine_awareness
        self.keep_jockey_prob = keep_jockey_prob

    def load(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        rng = np.random.default_rng(self.seed)
        H, J, T = self.n_horses, self.n_jockeys, self.n_trainers

        horse_ability = rng.normal(0, 1.0, H)
        horse_turf_pref = rng.normal(0, 0.35, H)      # + turf, - dirt
        horse_dist_pref = rng.normal(0, 0.35, H)      # + stayer, - sprinter
        horse_soft_pref = rng.normal(0, 0.25, H)
        horse_sex = rng.choice(["M", "F", "G"], H, p=[0.5, 0.42, 0.08])
        horse_birth_year = rng.integers(2014, 2021, H)
        horse_trainer = rng.integers(0, T, H)
        horse_body = rng.normal(470, 25, H)
        horse_last_run = np.full(H, pd.Timestamp("1900-01-01").value)
        horse_level = np.zeros(H, dtype=int)           # class the horse currently competes in
        horse_runs = np.zeros(H, dtype=int)
        horse_form = np.zeros(H)                       # public's exponentially-weighted memory of past results

        jockey_skill = rng.normal(0, 0.45, J)
        jockey_quality_bias = np.argsort(np.argsort(-jockey_skill))  # rank 0 = best
        trainer_skill = rng.normal(0, 0.30, T)

        # condition-specific jockey ability: the part a global win rate cannot express
        jockey_venue_skill = rng.normal(0, 0.25, (J, len(VENUES)))
        jockey_surface_skill = rng.normal(0, 0.20, J)   # + turf, - dirt
        jockey_dist_skill = rng.normal(0, 0.20, J)      # + stayer, - sprinter
        # horse-jockey chemistry as a low-rank interaction
        chem_dim = 3
        horse_chem = rng.normal(0, 1.0, (H, chem_dim))
        jockey_chem = rng.normal(0, 0.15, (J, chem_dim))
        # every horse has a regular jockey it tends to keep
        horse_regular_jockey = rng.integers(0, J, H)
        venue_index = {v: i for i, v in enumerate(VENUES)}

        race_days = [d for d in pd.date_range(self.start, self.end, freq="D") if d.dayofweek in (5, 6)]

        races, entries = [], []
        for day in race_days:
            venues = rng.choice(VENUES, 3, replace=False)
            for v in venues:
                for r in range(1, self.races_per_day // 3 + 1):
                    race_id = f"{day:%Y%m%d}{v}{r:02d}"
                    dist = int(rng.choice(DISTANCES))
                    surface = "turf" if rng.random() < 0.6 else "dirt"
                    going = str(rng.choice(GOINGS, p=GOING_P))
                    cls_level = int(np.clip(rng.integers(0, 4) + (r - 8) // 3, 0, len(CLASSES) - 1))
                    race_class = CLASSES[cls_level]
                    n = int(rng.integers(8, 19))

                    # eligible horses: rested >= 14 days, class within +-1 of horse level, age >= 2
                    age = day.year - horse_birth_year
                    rested = (day.value - horse_last_run) > pd.Timedelta(days=14).value
                    eligible = np.where(rested & (age >= 2) & (age <= 8) & (np.abs(horse_level - cls_level) <= 1))[0]
                    if eligible.size < n:
                        eligible = np.where(rested & (age >= 2) & (age <= 8))[0]
                        if eligible.size < n:
                            continue
                    field = rng.choice(eligible, n, replace=False)
                    jockeys = self._book_jockeys(field, horse_regular_jockey, horse_ability, jockey_skill, J, rng)

                    going_pen = {"good": 0.0, "yielding": 0.3, "soft": 0.6, "heavy": 1.0}[going]
                    dist_z = (dist - 1800) / 600.0
                    surf_sign = 1.0 if surface == "turf" else -1.0
                    # ability random-walk drift (career development / decline)
                    horse_ability[field] += rng.normal(0, 0.05, n) - 0.02 * (age[field] >= 6)
                    weight_carried = np.where(horse_sex[field] == "F", 54.0, 56.0) + (cls_level >= 4) * rng.integers(0, 3, n)
                    body_diff = rng.normal(0, 6, n).round()
                    horse_body[field] += body_diff

                    # the conditional / interaction part of jockey ability
                    vi = venue_index[v]
                    fine = (
                        jockey_venue_skill[jockeys, vi]
                        + surf_sign * jockey_surface_skill[jockeys]
                        + dist_z * jockey_dist_skill[jockeys]
                        + (horse_chem[field] * jockey_chem[jockeys]).sum(axis=1)
                    )
                    coarse_strength = (
                        horse_ability[field]
                        + surf_sign * horse_turf_pref[field]
                        + dist_z * horse_dist_pref[field]
                        + going_pen * horse_soft_pref[field]
                        + jockey_skill[jockeys]
                        + trainer_skill[horse_trainer[field]]
                        - 0.03 * (weight_carried - 55.0)
                    )
                    true_strength = coarse_strength + fine
                    order = _plackett_luce_order(true_strength * 1.3, rng)
                    finish = np.empty(n, dtype=int)
                    finish[order] = np.arange(1, n + 1)

                    base_time = dist / 16.5 + going_pen * 1.2
                    times = base_time - 0.35 * true_strength + rng.normal(0, 0.4, n)
                    times = np.sort(times)[np.argsort(np.argsort(finish))]  # consistent with finishing order

                    # public pricing: the crowd sees (a) a noisy glimpse of latent strength, (b) the same
                    # past-form signal the model can reconstruct, and (c) over-weights jockey fame.
                    # Because (b) is shared with the model, model and market errors are correlated,
                    # which keeps the exploitable edge realistically small.
                    # The crowd prices the coarse structure plus only part of the fine terms.
                    # The unseen remainder is the edge the conditional features are built to find.
                    w = self.public_form_weight
                    public_strength = coarse_strength + self.public_fine_awareness * fine
                    public_view = ((1 - w) * public_strength + w * (horse_form[field] + jockey_skill[jockeys])
                                   + rng.normal(0, self.public_noise, n) + 0.25 * (jockey_quality_bias[jockeys] < 10))
                    q = np.exp(1.3 * public_view)
                    q /= q.sum()
                    win_odds = np.maximum(1.1, np.round((1 - JRA_WIN_TAKEOUT) / q, 1))
                    popularity = np.argsort(np.argsort(win_odds)) + 1

                    races.append(dict(race_id=race_id, race_date=day, venue=v, race_no=r, distance_m=dist, surface=surface,
                                      going=going, race_class=race_class, n_runners=n))
                    for k, h in enumerate(field):
                        entries.append(dict(
                            race_id=race_id, entrant_id=f"H{h:05d}", post_position=k + 1, draw=(k // 2) + 1,
                            jockey_id=f"J{jockeys[k]:03d}", trainer_id=f"T{horse_trainer[h]:03d}",
                            age=int(age[h]), sex=horse_sex[h], weight_carried=float(weight_carried[k]),
                            body_weight=float(round(horse_body[h])), body_weight_diff=float(body_diff[k]),
                            finish_position=int(finish[k]), finish_time_sec=float(round(times[k], 1)),
                            win_odds=float(win_odds[k]), place_odds=float(np.round(1 + (win_odds[k] - 1) * 0.3, 1)),
                            popularity=int(popularity[k]),
                        ))
                    horse_last_run[field] = day.value
                    horse_runs[field] += 1
                    # update public form memory with this race's standardised result
                    result_signal = -(finish - (n + 1) / 2) / (n / 4)
                    horse_form[field] = 0.7 * horse_form[field] + 0.3 * result_signal
                    # a jockey who wins or places tends to keep the ride
                    kept = field[finish <= 3]
                    horse_regular_jockey[kept] = jockeys[finish <= 3]
                    # promotion / demotion
                    winners = field[finish == 1]
                    horse_level[winners] = np.minimum(horse_level[winners] + 1, len(CLASSES) - 1)
                    losers = field[finish >= n - 1]
                    horse_level[losers] = np.maximum(horse_level[losers] - (rng.random(losers.size) < 0.15), 0)

        return pd.DataFrame(races), pd.DataFrame(entries)

    def _book_jockeys(self, field, horse_regular_jockey, horse_ability, jockey_skill, J, rng) -> np.ndarray:
        """Assign one distinct jockey per runner, with continuity and confounding.

        Better horses book first and pull better jockeys, which is exactly the
        confounding that makes a raw jockey win rate a biased measure of skill.
        """
        n = field.shape[0]
        out = np.full(n, -1, dtype=int)
        available = np.ones(J, dtype=bool)
        noise = rng.gumbel(size=(n, J))
        keep_draw = rng.random(n)
        for slot, k in enumerate(np.argsort(-horse_ability[field])):
            h = field[k]
            regular = horse_regular_jockey[h]
            if keep_draw[slot] < self.keep_jockey_prob and available[regular]:
                j = int(regular)
            else:
                # a stronger horse is offered to stronger jockeys
                pull = 0.5 + 0.5 * np.tanh(horse_ability[h])
                logits = np.where(available, 2.0 * jockey_skill * pull, -np.inf)
                j = int(np.argmax(logits + noise[slot]))
            out[k] = j
            available[j] = False
        return out
