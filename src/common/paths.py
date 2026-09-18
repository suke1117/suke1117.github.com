"""Repository-root bootstrap so CLI scripts can be executed directly.

Each CLI script does::

    from _bootstrap import ROOT  # noqa

which is not possible without a shared module; instead every script calls
``ensure_root_on_path()`` at import time via ``src.common.paths``.  Because the
scripts live two levels below the root, we simply compute it from ``__file__``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]


def ensure_root_on_path() -> Path:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return ROOT


RAW_DATA_DIR = ROOT / "raw_data"
PROCESSED_DIR = ROOT / "processed"
ARTIFACTS_DIR = ROOT / "artifacts"
BACKTEST_DIR = ROOT / "backtest_results"
