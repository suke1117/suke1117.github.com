from src.live.providers.base import LiveProvider, ProviderError
from src.live.providers.csv_card import CsvCardProvider
from src.live.providers.demo import DemoProvider
from src.live.providers.http_json import HttpJsonProvider
from src.live.providers.jravan import JraVanProvider

PROVIDERS = {
    "csv": CsvCardProvider,
    "demo": DemoProvider,
    "http": HttpJsonProvider,
    "jravan": JraVanProvider,
}


def get_provider(name: str, **kwargs) -> LiveProvider:
    try:
        cls = PROVIDERS[name]
    except KeyError as exc:
        raise ProviderError(f"unknown provider '{name}'; choose from {sorted(PROVIDERS)}") from exc
    return cls(**kwargs)


__all__ = ["LiveProvider", "ProviderError", "CsvCardProvider", "DemoProvider", "HttpJsonProvider", "JraVanProvider",
           "PROVIDERS", "get_provider"]
