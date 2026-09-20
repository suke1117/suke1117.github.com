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

from typing import Optional, Tuple

import numpy as np
import pandas as pd

from src.common.config import JRA_WIN_TAKEOUT, RANDOM_SEED
from src.common.sport import HORSE_RACING, NAR_RACING
from src.data.schema import DEFAULT_ORGANIZER
from src.data.sources.base import DataSource

VENUES = ["05", "06", "09", "08", "07", "01", "03", "04", "10", "02"]  # 東京, 中山, 阪神, 京都, 中京, 札幌, 福島, 新潟, 小倉, 函館
CLASSES = ["maiden", "1win", "2win", "3win", "OP", "G3", "G2", "G1"]
CLASS_LEVEL = {c: i for i, c in enumerate(CLASSES)}
GOINGS = ["good", "yielding", "soft", "heavy"]
GOING_P = [0.70, 0.15, 0.10, 0.05]
DISTANCES = [1200, 1400, 1600, 1800, 2000, 2200, 2400]

#: 地方競馬: weekday racing, mostly dirt, smaller fields, its own venues and
#: class ladder, and a higher takeout on a smaller pool.
NAR_VENUES = ["41", "42", "43", "44", "45", "46", "47", "48"]  # 門別, 盛岡, 浦和, 船橋, 大井, 川崎, 名古屋, 園田
NAR_CLASSES = ["nar_maiden", "nar_c3", "nar_c2", "nar_c1", "nar_b", "nar_a", "nar_open"]
NAR_DISTANCES = [800, 1000, 1200, 1400, 1600, 1800, 2000]

PRESETS = {
    "jra": {"venues": VENUES, "classes": CLASSES, "distances": DISTANCES, "race_days": (5, 6),
            "venues_per_day": 3, "turf_share": 0.6, "field_range": (8, 19), "takeout": JRA_WIN_TAKEOUT,
            "entrant_prefix": "H", "jockey_prefix": "J", "trainer_prefix": "T", "organizer": "JRA",
            "public_noise": 0.20, "rest_days": 14},
    # NAR runs most weekdays. Smaller pools mean a noisier crowd, which is where
    # any edge there would come from; it also means less liquidity, so the
    # stake caps matter more, not less.
    "nar": {"venues": NAR_VENUES, "classes": NAR_CLASSES, "distances": NAR_DISTANCES, "race_days": (0, 1, 2, 3, 4),
            "venues_per_day": 2, "turf_share": 0.02, "field_range": (7, 15), "takeout": 0.25,
            "entrant_prefix": "N", "jockey_prefix": "NJ", "trainer_prefix": "NT", "organizer": "NAR",
            "public_noise": 0.30, "rest_days": 8},
}


def _plackett_luce_order(strength: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Sample a full finishing order from a Plackett-Luce model (Gumbel trick)."""
    g = rng.gumbel(size=strength.shape[0])
    return np.argsort(-(strength + g))


def _discounted_order(strength: np.ndarray, rng: np.random.Generator, lam: float) -> np.ndarray:
    """Finishing order where the race for each later place is flatter than for first.

    Real racing does not run down the way plain Plackett-Luce says: once the
    best horse has failed to win, the remaining order is closer to a coin toss
    than its strengths imply, because not winning usually means something went
    wrong rather than being narrowly outrun. Scaling the strengths by ``lam``
    after the winner is drawn reproduces that, and it is exactly the effect the
    Stern / Henery discount corrects. ``lam = 1`` is plain Plackett-Luce.
    """
    n = strength.shape[0]
    order = np.empty(n, dtype=int)
    remaining = np.arange(n)
    for pos in range(n):
        scale = 1.0 if pos == 0 else lam
        g = rng.gumbel(size=remaining.shape[0])
        pick = int(np.argmax(strength[remaining] * scale + g))
        order[pos] = remaining[pick]
        remaining = np.delete(remaining, pick)
    return order


def _harville_place(p: np.ndarray, k: int) -> np.ndarray:
    """Top-k probability under plain Harville - how the crowd prices the place pool."""
    p = np.asarray(p, dtype=float)
    if k <= 1:
        return p.copy()
    denom1 = np.clip(1.0 - p, 1e-12, None)
    ex = np.outer(p / denom1, p)
    np.fill_diagonal(ex, 0.0)
    second = ex.sum(axis=0)
    if k == 2:
        return p + second
    denom2 = np.clip(1.0 - p[:, None] - p[None, :], 1e-12, None)
    third = (ex[:, :, None] * (p[None, None, :] / denom2[:, :, None])).sum(axis=(0, 1))
    return p + second + third


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
        public_noise: Optional[float] = None,
        public_form_weight: float = 0.6,
        public_fine_awareness: float = 0.5,
        keep_jockey_prob: float = 0.62,
        preset: str = "jra",
        run_down_lambda: float = 0.65,
    ):
        if preset not in PRESETS:
            raise ValueError(f"unknown preset '{preset}'; choose from {sorted(PRESETS)}")
        self.preset_name = preset
        self.cfg = PRESETS[preset]
        self.sport = HORSE_RACING if preset == "jra" else NAR_RACING
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        self.n_horses, self.n_jockeys, self.n_trainers = n_horses, n_jockeys, n_trainers
        self.races_per_day = races_per_day
        self.seed = seed
        self.public_noise = self.cfg["public_noise"] if public_noise is None else public_noise
        self.public_form_weight = public_form_weight
        self.public_fine_awareness = public_fine_awareness
        self.keep_jockey_prob = keep_jockey_prob
        self.run_down_lambda = run_down_lambda

    def load(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        cfg = self.cfg
        venue_pool, class_pool, dist_pool = cfg["venues"], cfg["classes"], cfg["distances"]
        lo_field, hi_field = cfg["field_range"]
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
        jockey_venue_skill = rng.normal(0, 0.25, (J, len(cfg["venues"])))
        jockey_surface_skill = rng.normal(0, 0.20, J)   # + turf, - dirt
        jockey_dist_skill = rng.normal(0, 0.20, J)      # + stayer, - sprinter
        # horse-jockey chemistry as a low-rank interaction
        chem_dim = 3
        horse_chem = rng.normal(0, 1.0, (H, chem_dim))
        jockey_chem = rng.normal(0, 0.15, (J, chem_dim))
        # every horse has a regular jockey it tends to keep
        horse_regular_jockey = rng.integers(0, J, H)
        venue_index = {v: i for i, v in enumerate(venue_pool)}

        race_days = [d for d in pd.date_range(self.start, self.end, freq="D") if d.dayofweek in cfg["race_days"]]

        races, entries = [], []
        for day in race_days:
            venues = rng.choice(venue_pool, min(cfg["venues_per_day"], len(venue_pool)), replace=False)
            for v in venues:
                for r in range(1, max(1, self.races_per_day // cfg["venues_per_day"]) + 1):
                    race_id = f"{day:%Y%m%d}{v}{r:02d}"   # venue codes are disjoint across organizers
                    dist = int(rng.choice(dist_pool))
                    surface = "turf" if rng.random() < cfg["turf_share"] else "dirt"
                    going = str(rng.choice(GOINGS, p=GOING_P))
                    cls_level = int(np.clip(rng.integers(0, 4) + (r - 8) // 3, 0, len(class_pool) - 1))
                    race_class = class_pool[cls_level]
                    n = int(rng.integers(lo_field, hi_field))

                    # eligible horses: rested >= 14 days, class within +-1 of horse level, age >= 2
                    age = day.year - horse_birth_year
                    rested = (day.value - horse_last_run) > pd.Timedelta(days=cfg["rest_days"]).value
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
                    order = _discounted_order(true_strength * 1.3, rng, self.run_down_lambda)
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
                    win_odds = np.maximum(1.1, np.round((1 - cfg["takeout"]) / q, 1))
                    popularity = np.argsort(np.argsort(win_odds)) + 1
                    # The crowd prices the place pool off its own win view with plain
                    # Harville. Because the race actually runs down at a discount, that
                    # pricing is systematically wrong - and that is the edge K3 targets.
                    n_place = 3 if n >= 8 else (2 if n >= 5 else 1)
                    pub_place = _harville_place(q, n_place)
                    place_odds = np.maximum(1.0, np.round((1 - cfg["takeout"]) / np.clip(pub_place, 1e-6, None), 1))

                    races.append(dict(race_id=race_id, race_date=day, venue=v, race_no=r, distance_m=dist,
                                      surface=surface, going=going, race_class=race_class, n_runners=n,
                                      organizer=cfg["organizer"]))
                    for k, h in enumerate(field):
                        entries.append(dict(
                            race_id=race_id, entrant_id=f"{cfg['entrant_prefix']}{h:05d}", post_position=k + 1,
                            draw=(k // 2) + 1, jockey_id=f"{cfg['jockey_prefix']}{jockeys[k]:03d}",
                            trainer_id=f"{cfg['trainer_prefix']}{horse_trainer[h]:03d}",
                            age=int(age[h]), sex=horse_sex[h], weight_carried=float(weight_carried[k]),
                            body_weight=float(round(horse_body[h])), body_weight_diff=float(body_diff[k]),
                            finish_position=int(finish[k]), finish_time_sec=float(round(times[k], 1)),
                            win_odds=float(win_odds[k]), place_odds=float(place_odds[k]),
                            popularity=int(popularity[k]), public_p=float(q[k]),
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
                    horse_level[winners] = np.minimum(horse_level[winners] + 1, len(class_pool) - 1)
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
