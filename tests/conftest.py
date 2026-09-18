import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.features import build_features  # noqa: E402
from src.data.sources.synthetic import SyntheticSource  # noqa: E402


@pytest.fixture(scope="session")
def small_raw():
    races, entries = SyntheticSource(start="2019-01-05", end="2020-06-28", n_horses=900, seed=7).load_validated()
    return races, entries


@pytest.fixture(scope="session")
def small_table(small_raw):
    races, entries = small_raw
    table, feature_cols = build_features(races, entries)
    return table, feature_cols
