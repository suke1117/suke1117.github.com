from src.data.sources.base import DataSource
from src.data.sources.jravan_csv import JRAVanCSVSource
from src.data.sources.synthetic import SyntheticSource

SOURCE_REGISTRY = {
    "jravan_csv": JRAVanCSVSource,
    "synthetic": SyntheticSource,
    # "keirin_csv": KeirinCSVSource,   # future
    # "kyotei_csv": KyoteiCSVSource,   # future
}

__all__ = ["DataSource", "JRAVanCSVSource", "SyntheticSource", "SOURCE_REGISTRY"]
