"""Global constants for EquineAlpha.

Hard-coded risk limits live here on purpose: the betting layer imports them and
refuses to run outside these bounds (see CLAUDE.md, rule 4).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Risk-management hard limits ("negai" = losing-averse logic)
# ---------------------------------------------------------------------------
DEFAULT_KELLY_ALPHA: float = 0.10   # fraction of full Kelly used by default
MAX_KELLY_ALPHA: float = 0.25       # anything above this is rejected outright
MIN_KELLY_ALPHA: float = 0.01
DEFAULT_EV_THRESHOLD: float = 1.05  # expected value = p * decimal_odds must exceed this
MAX_RACE_EXPOSURE: float = 0.05     # max fraction of bankroll staked on one race
MAX_SINGLE_BET_FRACTION: float = 0.03  # max fraction of bankroll on one ticket
MIN_WIN_PROB_TO_BET: float = 0.02   # do not bet on extreme long-shots (model noise)
MAX_STAKE_YEN: float = 1_000_000.0  # liquidity cap: larger tickets move pari-mutuel odds against you

# ---------------------------------------------------------------------------
# Betting units (JRA: tickets are sold in 100-yen units)
# ---------------------------------------------------------------------------
BET_UNIT_YEN: int = 100
DEFAULT_BANKROLL_YEN: float = 1_000_000.0

# ---------------------------------------------------------------------------
# Modelling
# ---------------------------------------------------------------------------
RANDOM_SEED: int = 20240101
MAX_RELEVANCE: int = 17             # label = clip(n_runners - finish_position, 0, MAX_RELEVANCE)
LGBM_DEFAULT_PARAMS: dict = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "eval_at": [1, 3],
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "lambdarank_truncation_level": 10,
    "label_gain": list(range(MAX_RELEVANCE + 1)),  # linear gain avoids 2^17 blow-up
    "verbosity": -1,
    "seed": RANDOM_SEED,
    "num_threads": 4,
}
LGBM_NUM_BOOST_ROUND: int = 600
LGBM_EARLY_STOPPING_ROUNDS: int = 50

# Takeout (控除率) used by the synthetic data generator for win tickets.
JRA_WIN_TAKEOUT: float = 0.20
