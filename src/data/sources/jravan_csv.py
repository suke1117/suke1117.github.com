"""Real racing data from CSV, JV-Data shaped or not.

JV-Link itself is a Windows COM component; on that machine you export the
record types below to CSV (one file per record type, UTF-8 or CP932) and drop
them into ``raw_data/``.  Column naming follows the JV-Data specification
field names as commonly exported by JV-Link tooling.

Expected files (any of these names; case-insensitive):

    RA*.csv   レース詳細        -> races
    SE*.csv   馬毎レース情報    -> entries (results, odds, weights)
    UM*.csv   競走馬マスタ      -> optional, used for sex / birth year if SE lacks them
    KS*.csv   騎手マスタ        -> optional, not needed for features (ids are enough)

**Any other CSV works too.** JV-Link is not the only way to get real racing
data, and a source that needs a code change to try is a source nobody tries.
Drop a ``mapping.yml`` next to the files naming which file is which and how its
columns and values translate, and the same loader reads it:

    files:
      races: "*race*.csv"
      entries: "*result*.csv"
    races:
      開催年月日: race_date
      競馬場コード: venue
    entries:
      馬番: post_position
    values:
      surface: {"芝": turf, "ダ": dirt}

``preprocess.py --check`` reads the files without importing them and prints
what mapped, what is missing and the mapping.yml lines that would fix it, so
finding out whether a source is usable costs one command instead of a session.

Minimal required columns after mapping are those in ``src.data.schema``.
"""
from __future__ import annotations

import glob
import os
from typing import Dict, List, Optional, Tuple

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
    "n_runners": "n_runners", "organizer": "organizer",
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
    # display-only names, mapped here so a source that already uses these
    # headers needs no mapping.yml at all
    "entrant_name": "entrant_name", "jockey_name": "jockey_name", "horse_name": "entrant_name",
    "Bamei": "entrant_name", "馬名": "entrant_name", "騎手名": "jockey_name",
}

#: Value translations applied after the column mapping. The Japanese spellings
#: are here by default because every non-JV-Data export of Japanese racing uses
#: them, and a source that needs a code change to try is a source nobody tries.
DEFAULT_VALUE_MAPS: Dict[str, Dict[str, str]] = {
    "surface": {"芝": "turf", "ダ": "dirt", "ダート": "dirt", "障": "other", "障害": "other",
                "turf": "turf", "dirt": "dirt"},
    "going": {"良": "good", "稍": "yielding", "稍重": "yielding", "重": "soft", "不良": "heavy",
              "good": "good", "yielding": "yielding", "soft": "soft", "heavy": "heavy"},
}

#: What each table needs once the mapping has been applied. The alternatives are
#: the JV-Data raw fields the parser can derive the canonical column from.
REQUIRED: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "races": {"race_id": (), "race_date": ("_year", "_monthday"), "venue": (), "race_no": (),
              "distance_m": (), "surface": ("_track_cd",), "going": ("_siba_baba", "_dirt_baba"),
              # n_runners is counted from the card when the file has no such column
              "race_class": (), "n_runners": ("_syusso", "__counted__")},
    "entries": {"race_id": (), "entrant_id": (), "post_position": (), "draw": (), "jockey_id": (),
                "trainer_id": (), "age": (), "sex": (), "weight_carried": (), "finish_position": (),
                "win_odds": ()},
}
#: Missing these costs a feature family, not the run.
OPTIONAL: Dict[str, Tuple[str, ...]] = {
    "races": ("organizer",),
    "entries": ("body_weight", "body_weight_diff", "finish_time_sec", "place_odds", "popularity",
                "entrant_name", "jockey_name"),
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


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    """Numeric column, or an all-NaN column of the right length when absent.

    ``df.get(col)`` returns None for a missing column and ``pd.to_numeric``
    turns that into a scalar, which then fails on the next Series call. Sources
    outside JV-Data routinely lack the optional fields, so this is the common
    path, not the edge case.
    """
    if col not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _targets(mapping: Dict[str, object], col: str) -> List[str]:
    """A source column may feed several canonical ones.

    地方競馬の出馬表には馬IDが無く馬名しかないので、同じ列を entrant_id と
    entrant_name の両方に使うことになる。YAML で同じキーを二度書くと後勝ちで
    黙って片方が消えるため、リストで書けるようにしてある。
    """
    v = mapping.get(col)
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


def _apply_columns(df: pd.DataFrame, mapping: Dict[str, object],
                   derive: Optional[Dict[str, object]] = None) -> pd.DataFrame:
    """Rename into canonical columns, then build the ones that are composed.

    Unmapped columns are kept, because the JV-Data parser reads raw fields
    (``_year``, ``_track_cd``) that no mapping names.
    """
    out = df.copy()
    for src in df.columns:
        for dst in _targets(mapping, src):
            if dst != src:
                out[dst] = df[src]
    for dst, parts in (derive or {}).items():
        parts = [parts] if isinstance(parts, str) else list(parts)
        missing = [c for c in parts if c not in out.columns and c not in df.columns]
        if missing:
            raise ValueError(f"cannot derive {dst}: no column {missing}")
        pieces = [(out[c] if c in out.columns else df[c]).astype(str).str.strip() for c in parts]
        joined = pieces[0]
        for piece in pieces[1:]:
            joined = joined + "-" + piece
        out[dst] = joined
    return out


def _rename(df: pd.DataFrame, mapping: Dict[str, object]) -> pd.DataFrame:
    return _apply_columns(df, mapping)


MAPPING_FILE = "mapping.yml"


def load_mapping(input_dir: str) -> Dict:
    """Read ``<input_dir>/mapping.yml`` if it exists, else return an empty one."""
    path = os.path.join(input_dir, MAPPING_FILE)
    if not os.path.exists(path):
        return {}
    import yaml

    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} must be a mapping of sections, got {type(cfg).__name__}")
    unknown = set(cfg) - {"files", "races", "entries", "values", "derive"}
    if unknown:
        raise ValueError(f"{path} has unknown sections {sorted(unknown)}; "
                         "expected files / races / entries / values / derive")
    return cfg


def _column_map(cfg: Dict, table: str) -> Dict[str, object]:
    """Built-in JV-Data names, with the user's own file taking precedence."""
    base: Dict[str, object] = dict(RA_MAP if table == "races" else SE_MAP)
    base.update({str(k): v for k, v in (cfg.get(table) or {}).items()})
    return base


def _derive_map(cfg: Dict) -> Dict[str, object]:
    """Columns built by joining others, for sources with no key of their own.

    地方競馬のファイルには race_id が無い。競馬場・競走年月日・レース番号を
    つないで作るしかなく、それは対応表では書けない。
    """
    return {str(k): v for k, v in (cfg.get("derive") or {}).items()}


def _locate(cfg: Dict, table: str, input_dir: str) -> Optional[str]:
    """The file for one table: the mapping's glob if given, else the JV-Data prefix."""
    pattern = (cfg.get("files") or {}).get(table)
    if pattern:
        hits = sorted(glob.glob(os.path.join(input_dir, pattern)))
        return hits[0] if hits else None
    return _find("RA" if table == "races" else "SE", input_dir)


def _value_maps(cfg: Dict) -> Dict[str, Dict[str, str]]:
    out = {k: dict(v) for k, v in DEFAULT_VALUE_MAPS.items()}
    for field, pairs in (cfg.get("values") or {}).items():
        out.setdefault(field, {}).update({str(k): str(v) for k, v in (pairs or {}).items()})
    return out


def diagnose(input_dir: str) -> Dict:
    """What a directory of CSVs would and would not give the pipeline.

    This never imports anything. The point is that finding out whether a data
    source is usable costs one command rather than a debugging session, so it
    reports the gap and the exact mapping.yml lines that would close it.
    """
    cfg = load_mapping(input_dir)
    report: Dict = {"input_dir": input_dir, "mapping_file": os.path.join(input_dir, MAPPING_FILE)
                    if cfg else None, "tables": {}, "usable": True}
    for table in ("races", "entries"):
        path = _locate(cfg, table, input_dir)
        info: Dict = {"path": path}
        if path is None:
            info["error"] = ((f"{(cfg.get('files') or {}).get(table)} にも" if cfg.get("files") else "")
                             + f"{'RA' if table == 'races' else 'SE'} で始まる CSV が見つかりません")
            report["usable"] = False
            report["tables"][table] = info
            continue
        df = _read_csv(path)
        colmap = _column_map(cfg, table)
        derive = _derive_map(cfg)
        try:
            renamed = set(_apply_columns(df.head(0), colmap, derive).columns)
        except ValueError as exc:
            info["error"] = str(exc)
            report["usable"] = False
            report["tables"][table] = info
            continue
        info["n_rows"] = len(df)
        info["mapped"] = sorted(c for c in renamed if c in REQUIRED[table] or c in OPTIONAL[table])
        missing = []
        for col, alts in REQUIRED[table].items():
            if col in renamed:
                continue
            if "__counted__" in alts:
                continue  # counted from the entries themselves
            if alts and all(a in renamed for a in alts):
                continue  # the parser derives it from the raw JV-Data fields
            missing.append(col)
        info["missing_required"] = missing
        info["missing_optional"] = [c for c in OPTIONAL[table] if c not in renamed]
        info["unmapped_columns"] = [c for c in df.columns if not _targets(colmap, c)]
        info["warnings"] = _warnings(cfg, table, renamed)
        if missing:
            report["usable"] = False
        report["tables"][table] = info
    return report


#: Column headers that hold a runner's own past record. Sources differ on
#: whether these are as-of the race or include it, and the difference is
#: invisible in the file. Mapping one into a feature would be a leak that no
#: test catches, because the value looks reasonable either way.
SUSPECT_ASOF_HEADERS = ("成績", "通算", "最高タイム", "勝率", "連対率", "複勝率")


def _warnings(cfg: Dict, table: str, renamed: set) -> List[str]:
    """Hazards that are not missing columns but will still produce wrong numbers."""
    out: List[str] = []
    user = {str(k): v for k, v in (cfg.get(table) or {}).items()}
    if table == "entries":
        # A name is not an id: 改名 splits one horse in two, and 同名馬 merges
        # two into one. Both corrupt every as-of aggregate keyed on the id.
        by_target: Dict[str, List[str]] = {}
        for src in user:
            for dst in _targets(user, src):
                by_target.setdefault(dst, []).append(src)
        for id_col, name_col in (("entrant_id", "entrant_name"), ("jockey_id", "jockey_name")):
            shared = set(by_target.get(id_col, [])) & set(by_target.get(name_col, []))
            if shared:
                out.append(f"{id_col} に名前の列 ({', '.join(sorted(shared))}) を割り当てています。"
                           "改名で同一の主体が分断され、同名で別の主体が合算されます。"
                           "過去成績の集計はすべてこのキーで束ねるので、精度に直接効きます。")
    leaky = sorted({src for src in user if any(h in str(src) for h in SUSPECT_ASOF_HEADERS)})
    if leaky:
        out.append(f"{', '.join(leaky)} を対応づけています。これらは主体の過去成績で、"
                   "当該レースを含むかどうかがファイルから判別できません。"
                   "含んでいればリークです。過去成績は履歴から自分で as-of 集計するので、"
                   "対応づけないでください。")
    return out


class JRAVanCSVSource(DataSource):
    sport = HORSE_RACING

    def __init__(self, input_dir: str, mapping: Optional[Dict] = None):
        self.input_dir = input_dir
        self.mapping = load_mapping(input_dir) if mapping is None else mapping

    @staticmethod
    def has_data(input_dir: str) -> bool:
        cfg = load_mapping(input_dir)
        return all(_locate(cfg, t, input_dir) is not None for t in ("races", "entries"))

    def load(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        cfg = self.mapping
        ra_path, se_path = _locate(cfg, "races", self.input_dir), _locate(cfg, "entries", self.input_dir)
        if ra_path is None or se_path is None:
            raise FileNotFoundError(
                f"race / entry CSVs not found in {self.input_dir}. Name them RA*.csv and SE*.csv, "
                f"or point at them from {self.input_dir}/{MAPPING_FILE}. "
                "`python src/data/preprocess.py --check` says what is missing.")
        values, derive = _value_maps(cfg), _derive_map(cfg)
        races = self._parse_races(
            _apply_columns(_read_csv(ra_path), _column_map(cfg, "races"), derive), values)
        entries = self._parse_entries(
            _apply_columns(_read_csv(se_path), _column_map(cfg, "entries"), derive))
        # A source with no field count of its own: count the card instead of
        # asking the file for a number it does not carry.
        if "n_runners" not in races.columns or races["n_runners"].le(0).all():
            counts = entries.groupby("race_id").size().rename("n_runners")
            races = races.drop(columns=["n_runners"], errors="ignore").merge(
                counts, left_on="race_id", right_index=True, how="left")
            races["n_runners"] = races["n_runners"].fillna(0).astype(int)
        return races, entries

    # -- parsing -----------------------------------------------------------
    @staticmethod
    def _parse_races(df: pd.DataFrame, values: Optional[Dict[str, Dict[str, str]]] = None) -> pd.DataFrame:
        values = values or DEFAULT_VALUE_MAPS
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
            out["surface"] = df["surface"].astype(str).str.strip().map(
                lambda v: values.get("surface", {}).get(v, v))
        else:
            out["surface"] = df["_track_cd"].astype(str).str[:2].map(TRACK_SURFACE).fillna("other")
        if "going" in df:
            out["going"] = df["going"].astype(str).str.strip().map(
                lambda v: values.get("going", {}).get(v, v))
        else:
            siba = df.get("_siba_baba", pd.Series("0", index=df.index)).astype(str)
            dirt = df.get("_dirt_baba", pd.Series("0", index=df.index)).astype(str)
            code = siba.where(out["surface"] == "turf", dirt)
            out["going"] = code.map(BABA_CODE).fillna("good")
        out["race_class"] = df["race_class"].astype(str)
        if "n_runners" in df:
            out["n_runners"] = pd.to_numeric(df["n_runners"], errors="coerce").fillna(0).astype(int)
        elif "_syusso" not in df:
            out["n_runners"] = 0   # filled by counting the card in load()
        else:
            out["n_runners"] = pd.to_numeric(df["_syusso"], errors="coerce").fillna(0).astype(int)
        if "organizer" in df:
            out["organizer"] = df["organizer"].astype(str)
        return out

    @staticmethod
    def _parse_entries(df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame()
        out["race_id"] = df["race_id"].astype(str)
        out["entrant_id"] = df["entrant_id"].astype(str)
        for col in ("post_position", "draw", "age", "finish_position", "popularity"):
            out[col] = _num(df, col).fillna(0).astype(int)
        out["jockey_id"] = df["jockey_id"].astype(str)
        out["trainer_id"] = df["trainer_id"].astype(str)
        out["sex"] = df["sex"].astype(str)
        out["weight_carried"] = _num(df, "weight_carried")
        # JV-Data Futan is in 0.1kg units when > 100
        out.loc[out["weight_carried"] > 100, "weight_carried"] /= 10.0
        out["body_weight"] = _num(df, "body_weight")
        out["body_weight_diff"] = _num(df, "body_weight_diff")
        t = df["finish_time_sec"] if "finish_time_sec" in df.columns else None
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
        odds = _num(df, "win_odds")
        # JV-Data Odds are in 0.1 units without decimal point (e.g. "0123" -> 12.3)
        seen = odds.dropna()
        if len(seen) and seen.gt(0).any() and (seen % 1 == 0).all() and seen.median() > 100:
            odds = odds / 10.0
        out["win_odds"] = odds
        out["place_odds"] = _num(df, "place_odds")
        # display-only, and only when the source actually has them: the screens
        # name a runner by its 馬番 otherwise, and never invent a name
        for col in ("entrant_name", "jockey_name"):
            if col in df:
                out[col] = df[col].astype(str).str.strip()
        return out
