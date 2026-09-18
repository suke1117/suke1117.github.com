"""JRA-VAN (JV-Data) CSV adapter.

JV-Link itself is a Windows COM component; on that machine you export the
record types below to CSV (one file per record type, UTF-8 or CP932) and drop
them into ``raw_data/``.  Column naming follows the JV-Data specification
field names as commonly exported by JV-Link tooling; a mapping table lets you
rename columns from your own exporter without touching code.

Expected files (any of these names; case-insensitive):

    RA*.csv   レース詳細        -> races
    SE*.csv   馬毎レース情報    -> entries (results, odds, weights)
    UM*.csv   競走馬マスタ      -> optional, used for sex / birth year if SE lacks them
    KS*.csv   騎手マスタ        -> optional, not needed for features (ids are enough)

Minimal required columns after mapping are those in ``src.data.schema``.
"""
from __future__ import annotations

import glob
import os
from typing import Dict, Optional, Tuple

import pandas as pd

from src.common.sport import HORSE_RACING
from src.data.sources.base import DataSource

# JV-Data field name -> canonical column
RA_MAP: Dict[str, str] = {
    "RaceID": "race_id",
    "Year": "_year", "MonthDay": "_monthday", "JyoCD": "venue", "RaceNum": "race_no",
    "Kyori": "distance_m", "TrackCD": "_track_cd", "SibaBabaCD": "_siba_baba", "DirtBabaCD": "_dirt_baba",
    "JyokenCD5": "race_class", "TorokuTosu": "n_runners", "SyussoTosu": "_syusso",
    # common export names
    "race_id": "race_id", "race_date": "race_date", "venue": "venue", "race_no": "race_no",
    "distance_m": "distance_m", "surface": "surface", "going": "going", "race_class": "race_class",
    "n_runners": "n_runners",
}

SE_MAP: Dict[str, str] = {
    "RaceID": "race_id", "KettoNum": "entrant_id", "Umaban": "post_position", "Wakuban": "draw",
    "KisyuCode": "jockey_id", "ChokyosiCode": "trainer_id", "Barei": "age", "SexCD": "sex",
    "Futan": "weight_carried", "BaTaijyu": "body_weight", "ZogenSa": "body_weight_diff",
    "KakuteiJyuni": "finish_position", "Time": "finish_time_sec", "Odds": "win_odds", "Ninki": "popularity",
    # common export names
    "race_id": "race_id", "entrant_id": "entrant_id", "horse_id": "entrant_id", "post_position": "post_position",
    "draw": "draw", "jockey_id": "jockey_id", "trainer_id": "trainer_id", "age": "age", "sex": "sex",
    "weight_carried": "weight_carried", "body_weight": "body_weight", "body_weight_diff": "body_weight_diff",
    "finish_position": "finish_position", "finish_time_sec": "finish_time_sec", "win_odds": "win_odds",
    "place_odds": "place_odds", "popularity": "popularity",
}

TRACK_SURFACE = {  # TrackCD first digit heuristics (10-22 turf, 23-29 dirt, 51+ jump)
    **{str(i): "turf" for i in range(10, 23)},
    **{str(i): "dirt" for i in range(23, 30)},
}
BABA_CODE = {"1": "good", "2": "yielding", "3": "soft", "4": "heavy"}


def _find(pattern: str, input_dir: str) -> Optional[str]:
    hits = [p for p in glob.glob(os.path.join(input_dir, "*")) if os.path.basename(p).lower().startswith(pattern.lower())
            and p.lower().endswith(".csv")]
    return sorted(hits)[0] if hits else None


def _read_csv(path: str) -> pd.DataFrame:
    for enc in ("utf-8", "utf-8-sig", "cp932"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"could not decode {path}")


def _rename(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    cols = {c: mapping[c] for c in df.columns if c in mapping}
    return df.rename(columns=cols)


class JRAVanCSVSource(DataSource):
    sport = HORSE_RACING

    def __init__(self, input_dir: str):
        self.input_dir = input_dir

    @staticmethod
    def has_data(input_dir: str) -> bool:
        return _find("RA", input_dir) is not None and _find("SE", input_dir) is not None

    def load(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        ra_path, se_path = _find("RA", self.input_dir), _find("SE", self.input_dir)
        if ra_path is None or se_path is None:
            raise FileNotFoundError(f"RA*.csv / SE*.csv not found in {self.input_dir}")
        races = self._parse_races(_rename(_read_csv(ra_path), RA_MAP))
        entries = self._parse_entries(_rename(_read_csv(se_path), SE_MAP))
        return races, entries

    # -- parsing -----------------------------------------------------------
    @staticmethod
    def _parse_races(df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame()
        out["race_id"] = df["race_id"].astype(str)
        if "race_date" in df:
            out["race_date"] = pd.to_datetime(df["race_date"])
        else:
            out["race_date"] = pd.to_datetime(df["_year"].str.zfill(4) + df["_monthday"].str.zfill(4), format="%Y%m%d")
        out["venue"] = df["venue"].astype(str)
        out["race_no"] = pd.to_numeric(df["race_no"], errors="coerce").fillna(0).astype(int)
        out["distance_m"] = pd.to_numeric(df["distance_m"], errors="coerce").astype("Int64").astype(int)
        if "surface" in df:
            out["surface"] = df["surface"]
        else:
            out["surface"] = df["_track_cd"].astype(str).str[:2].map(TRACK_SURFACE).fillna("other")
        if "going" in df:
            out["going"] = df["going"]
        else:
            siba = df.get("_siba_baba", pd.Series("0", index=df.index)).astype(str)
            dirt = df.get("_dirt_baba", pd.Series("0", index=df.index)).astype(str)
            code = siba.where(out["surface"] == "turf", dirt)
            out["going"] = code.map(BABA_CODE).fillna("good")
        out["race_class"] = df["race_class"].astype(str)
        if "n_runners" in df:
            out["n_runners"] = pd.to_numeric(df["n_runners"], errors="coerce").fillna(0).astype(int)
        else:
            out["n_runners"] = pd.to_numeric(df["_syusso"], errors="coerce").fillna(0).astype(int)
        return out

    @staticmethod
    def _parse_entries(df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame()
        out["race_id"] = df["race_id"].astype(str)
        out["entrant_id"] = df["entrant_id"].astype(str)
        for col in ("post_position", "draw", "age", "finish_position", "popularity"):
            out[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(0).astype(int)
        out["jockey_id"] = df["jockey_id"].astype(str)
        out["trainer_id"] = df["trainer_id"].astype(str)
        out["sex"] = df["sex"].astype(str)
        out["weight_carried"] = pd.to_numeric(df["weight_carried"], errors="coerce")
        # JV-Data Futan is in 0.1kg units when > 100
        out.loc[out["weight_carried"] > 100, "weight_carried"] /= 10.0
        out["body_weight"] = pd.to_numeric(df.get("body_weight"), errors="coerce")
        out["body_weight_diff"] = pd.to_numeric(df.get("body_weight_diff"), errors="coerce")
        t = df.get("finish_time_sec")
        if t is not None:
            t = t.astype(str)
            # JV-Data Time is "MSSF" (minute, seconds, tenths) e.g. "1234" = 1:23.4
            jv = t.str.fullmatch(r"\d{4}")
            secs = pd.to_numeric(t, errors="coerce")
            jv_secs = t.str[0].astype(float, errors="ignore").where(jv)
            conv = pd.to_numeric(t.str[0], errors="coerce") * 60 + pd.to_numeric(t.str[1:3], errors="coerce") + pd.to_numeric(
                t.str[3], errors="coerce") / 10
            out["finish_time_sec"] = conv.where(jv.fillna(False), secs)
        else:
            out["finish_time_sec"] = float("nan")
        odds = pd.to_numeric(df.get("win_odds"), errors="coerce")
        # JV-Data Odds are in 0.1 units without decimal point (e.g. "0123" -> 12.3)
        if odds is not None and odds.dropna().gt(0).any() and (odds.dropna() % 1 == 0).all() and odds.dropna().median() > 100:
            odds = odds / 10.0
        out["win_odds"] = odds
        out["place_odds"] = pd.to_numeric(df.get("place_odds"), errors="coerce")
        return out
