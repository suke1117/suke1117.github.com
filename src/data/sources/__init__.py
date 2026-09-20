from src.data.sources.base import DataSource
from src.data.sources.combined import CombinedSource
from src.data.sources.jravan_csv import JRAVanCSVSource
from src.data.sources.synthetic import SyntheticSource

SOURCE_REGISTRY = {
    "combined": CombinedSource,
    "jravan_csv": JRAVanCSVSource,
    "synthetic": SyntheticSource,
    # "keirin_csv": KeirinCSVSource,   # future
    # "kyotei_csv": KyoteiCSVSource,   # future
}

__all__ = ["DataSource", "CombinedSource", "JRAVanCSVSource", "SyntheticSource", "SOURCE_REGISTRY"]
