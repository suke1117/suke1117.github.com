"""JRA-VAN Data Lab. (JV-Link) provider.

This is the sanctioned realtime route in Japan. It needs, on the machine that
runs it:

1. A JRA-VAN Data Lab. subscription (paid, monthly).
2. Windows, with the JV-Link ActiveX component installed and its service key
   registered through ``JVSetUIProperties``.
3. ``pip install pywin32``.

``JVOpen`` pulls stored data by record type; ``JVRTOpen`` pulls realtime data,
which is where odds (``0B31`` win odds) and the day's card come from. Because
JV-Link is a Windows COM object, this module cannot run or be tested anywhere
else, and it says so rather than pretending.

If you want to run the model on Linux, the supported shape is to export from
JV-Link on Windows into the CSV layout ``CsvCardProvider`` reads, and keep the
rest of the pipeline where it is.
"""
from __future__ import annotations

import platform
from typing import Optional, Tuple

import pandas as pd

from src.live.providers.base import LiveProvider, ProviderError

SETUP = (
    "JV-Link is a Windows COM component. Required: a JRA-VAN Data Lab. subscription, "
    "Windows with JV-Link installed and its service key registered, and pywin32. "
    "On any other OS, export from JV-Link to CSV and use --provider csv."
)

#: realtime data specs used by this provider
RT_WIN_ODDS = "0B31"   # 単勝・複勝・枠連オッズ
RT_RACE_CARD = "0B12"  # 出馬表


class JraVanProvider(LiveProvider):
    name = "jravan"

    def __init__(self, sid: str = "UNKNOWN", ready: bool = True):
        if platform.system() != "Windows":
            raise ProviderError(f"this provider only runs on Windows. {SETUP}")
        try:  # pragma: no cover - Windows only
            import win32com.client  # noqa: F401
        except ImportError as exc:  # pragma: no cover - Windows only
            raise ProviderError(f"pywin32 is not installed. {SETUP}") from exc
        self.sid = sid
        self._link = None
        if ready:
            self._connect()

    def _connect(self) -> None:  # pragma: no cover - Windows only
        import win32com.client

        self._link = win32com.client.Dispatch("JVDTLab.JVLink")
        rc = self._link.JVInit(self.sid)
        if rc != 0:
            raise ProviderError(f"JVInit failed with {rc}. Register the service key via JVSetUIProperties. {SETUP}")

    def fetch_card(self, date: pd.Timestamp) -> Tuple[pd.DataFrame, pd.DataFrame]:  # pragma: no cover - Windows only
        raise ProviderError(
            "JVRTOpen card parsing is not implemented. The record layout is fixed-width and version-specific, "
            f"so it needs to be written against the JV-Data spec of your subscription. Spec to request: {RT_RACE_CARD}. "
            "Until then, export to CSV and use --provider csv."
        )

    def fetch_odds(self, date: pd.Timestamp) -> pd.DataFrame:  # pragma: no cover - Windows only
        raise ProviderError(
            f"JVRTOpen odds parsing is not implemented. Spec to request: {RT_WIN_ODDS}. Use --provider csv for now."
        )

    def fetch_results(self, date: pd.Timestamp) -> Optional[pd.DataFrame]:  # pragma: no cover - Windows only
        return None
