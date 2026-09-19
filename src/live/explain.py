#!/usr/bin/env python
"""Why this bet, in terms a person can check.

A recommendation nobody can interrogate is a recommendation nobody should
follow. Every runner here carries three layers, and all three come from the
model that actually made the decision rather than from a story written
afterwards:

1. **決定** - bet or not, and which rule decided it. A pass is a decision with
   a reason, not an absence of one.
2. **値付け** - how the probability was built: the ranker's score, the
   Plackett-Luce normalisation, the isotonic calibration, and the blend with
   the market. Seeing where the number moved says more than the number.
3. **根拠** - which features pushed this runner up or down, from LightGBM's
   per-prediction contributions. They sum exactly to the score, so nothing is
   hidden in a remainder.

The contributions explain the **score**, not the final probability: the score
is what the trees produce, and the later stages reshape the whole field at
once. Saying otherwise would overstate what the attribution knows.

    python src/live/explain.py archive --start 2023-12-01 --end 2023-12-24
    python src/live/explain.py day --date 2023-12-24
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.betting.strategy import BetPolicy, bet_wins, place_payout_depth, select_bets  # noqa: E402
from src.common.logging_utils import get_logger  # noqa: E402
from src.live.narrative import enrich_day  # noqa: E402
from src.models.predict import Predictor  # noqa: E402

log = get_logger("explain")

ARCHIVE_DIR = "live_data/explained"
TOP_FACTORS = 4

VENUE_JA = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京", "06": "中山", "07": "中京",
    "08": "京都", "09": "阪神", "10": "小倉",
    "41": "門別", "42": "盛岡", "43": "浦和", "44": "船橋", "45": "大井", "46": "川崎", "47": "名古屋", "48": "園田",
}
SURFACE_JA = {"turf": "芝", "dirt": "ダート"}
GOING_JA = {"good": "良", "yielding": "稍重", "soft": "重", "heavy": "不良"}
TICKET_JA = {"win": "単勝", "place": "複勝", "quinella": "馬連"}

#: exact feature names that deserve their own wording
FEATURE_LABELS: Dict[str, str] = {
    "ent_starts": "出走経験", "ent_win_rate": "通算勝率", "ent_place_rate": "通算複勝率",
    "ent_avg_norm_pos": "通算の平均着順", "ent_norm_pos_last3": "直近3走の着順", "ent_speed_last3": "直近3走の速度",
    "ent_norm_pos_last10": "直近10走の着順", "ent_speed_last10": "直近10走の速度",
    "ent_last_pos": "前走の着順", "ent_last_norm_pos": "前走の相対着順", "ent_best_speed": "自己ベストの速度",
    "ent_days_since": "前走からの間隔", "ent_dist_change": "前走からの距離変化", "ent_class_change": "クラスの上下",
    "jky_starts": "騎手の騎乗数", "jky_win_rate": "騎手の勝率", "jky_place_rate": "騎手の複勝率",
    "jky_avg_norm_pos": "騎手の平均着順", "jky_win_rate_90d": "騎手の直近90日の勝率",
    "jky_norm_pos_90d": "騎手の直近90日の着順", "jky_starts_90d": "騎手の直近90日の騎乗数",
    "jky_days_since": "騎手の前騎乗からの間隔",
    "jky_resid": "騎手の上積み (馬の実力からの差)", "jky_resid_90d": "騎手の直近90日の上積み",
    "pair_starts": "この馬とのコンビ経験", "pair_norm_pos": "コンビでの着順", "pair_resid": "コンビの上積み",
    "sw_same_jockey": "前走と同じ騎手か", "sw_first_time_pair": "初コンビか",
    "sw_jockey_rate_delta": "乗り替わりでの騎手の格差",
    "trn_starts": "厩舎の出走数", "trn_win_rate": "厩舎の勝率", "trn_place_rate": "厩舎の複勝率",
    "trn_avg_norm_pos": "厩舎の平均着順", "trn_resid": "厩舎の上積み", "trn_days_since": "厩舎の前走からの間隔",
    "tj_starts": "厩舎と騎手の組み合わせ経験", "tj_win": "厩舎と騎手の組み合わせ勝率",
    "distance_m": "距離", "n_runners": "出走頭数", "post_position": "馬番", "draw": "枠番", "age": "年齢",
    "weight_carried": "斤量", "body_weight": "馬体重", "body_weight_diff": "馬体重の増減",
    "race_no": "レース番号", "month": "時期", "surface": "芝ダート", "going": "馬場状態",
    "race_class": "クラス", "venue": "競馬場", "sex": "性別", "organizer": "主催",
}
COND_JA = {"surf": "芝ダート", "dist": "距離帯", "venue": "コース", "going": "馬場状態", "class": "クラス"}


def feature_label(name: str) -> str:
    """A readable name for any feature, including the generated conditional ones."""
    if name in FEATURE_LABELS:
        return FEATURE_LABELS[name]
    base, _, suffix = name.partition("_rank")
    if suffix == "" and name.endswith("_rank"):
        return f"{feature_label(base)}の出走馬内順位"
    if name.startswith("entc_"):
        parts = name.split("_")
        cond = COND_JA.get(parts[1], parts[1])
        return f"この馬の{cond}別の出走数" if name.endswith("_starts") else f"この馬の{cond}別の着順"
    if name.startswith("jkyc_"):
        parts = name.split("_")
        cond = COND_JA.get(parts[1], parts[1])
        if name.endswith("_starts"):
            return f"騎手の{cond}別の騎乗数"
        return f"騎手の{cond}別の勝率" if name.endswith("_win") else f"騎手の{cond}別の着順"
    return name


def venue_name(code) -> str:
    """Venue codes are two digits; a CSV round-trip turns "04" into 4."""
    key = str(code).zfill(2) if str(code).isdigit() else str(code)
    return VENUE_JA.get(key, key)


def runner_label(name: Optional[str], post_position: Optional[int], entrant_id: str = "") -> str:
    """What to call a runner on screen.

    A 血統登録番号 like "H00918" identifies the horse to the database and to
    nobody else. What a person reads a card with is the 馬番 - it is the number
    on the saddlecloth and the number they key into a bet - so that leads, with
    the name after it when the source carries one. Names are never invented to
    fill the gap: on a page of real-looking numbers a made-up name reads as a
    real horse.
    """
    name = str(name or "").strip()
    if name in ("", "nan", "None"):
        name = ""
    if post_position:
        return f"{int(post_position)}番 {name}".strip()
    return name or str(entrant_id)


def race_title(race: Dict) -> str:
    return f"{venue_name(race.get('venue'))} {race.get('race_no')}R"


def race_conditions(race: Dict) -> str:
    return (f"{race.get('distance_m')}m {SURFACE_JA.get(race.get('surface'), race.get('surface') or '')}"
            f" {GOING_JA.get(race.get('going'), race.get('going') or '')} {race.get('n_runners')}頭").strip()


# ---------------------------------------------------------------------------
def score_contributions(predictor: Predictor, race: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """Per-runner feature contributions to the ranker score, plus the base value.

    They sum exactly to the score, so the explanation has no remainder.
    """
    x = predictor.ranker._matrix(race)
    contrib = predictor.ranker.booster.predict(
        x, pred_contrib=True, num_iteration=predictor.ranker.best_iteration or None)
    return np.asarray(contrib)[:, :-1], np.asarray(contrib)[:, -1]


def top_factors(contrib_row: np.ndarray, values: pd.Series, cols: List[str], k: int = TOP_FACTORS) -> List[Dict]:
    order = np.argsort(-np.abs(contrib_row))[:k]
    out = []
    for i in order:
        if abs(contrib_row[i]) < 1e-6:
            continue
        v = values.iloc[i]
        out.append({"feature": cols[i], "label": feature_label(cols[i]),
                    "contribution": round(float(contrib_row[i]), 4),
                    "direction": "up" if contrib_row[i] > 0 else "down",
                    "value": None if (isinstance(v, float) and not np.isfinite(v)) else (
                        round(float(v), 4) if isinstance(v, (int, float, np.floating)) else str(v))})
    return out


def decision_for(row: Dict, policy: BetPolicy, backing: Dict[str, List[Dict]], ranked_position: int) -> Dict:
    """Why this runner was or was not backed, named by the rule that decided it.

    A runner can be backed through a ticket other than the win pool, so this
    looks at every ticket the runner appears in. Reporting only the win
    decision would label a runner "見送り" on the same screen that shows money
    on it.
    """
    mine = backing.get(row["entrant_id"], [])
    if mine:
        parts = [f"{TICKET_JA.get(b['ticket'], b['ticket'])} {b['stake']:,.0f}円 (期待値 {b['ev']:.2f})" for b in mine]
        return {"action": "bet", "tickets": [b["ticket"] for b in mine],
                "ticket_ja": "・".join(TICKET_JA.get(b["ticket"], b["ticket"]) for b in mine),
                "stake": sum(b["stake"] for b in mine),
                "reason": "期待値が閾値 " + f"{policy.ev_threshold:.2f}" + " を超えたため購入: " + " / ".join(parts)}
    if row["odds"] is None or not np.isfinite(row["odds"]) or row["odds"] <= 1.0:
        return {"action": "skip", "reason": "オッズが取得できていない"}
    if row["ev"] < policy.ev_threshold:
        return {"action": "skip",
                "reason": f"単勝の期待値 {row['ev']:.2f} が閾値 {policy.ev_threshold:.2f} に届かず、"
                          "他の券種でも閾値を超えなかった"}
    if row["p_win"] < policy.min_prob:
        return {"action": "skip",
                "reason": f"予測勝率 {row['p_win'] * 100:.1f}% が下限 {policy.min_prob * 100:.0f}% 未満で、"
                          "推定が信用できる範囲を外れている"}
    if ranked_position >= policy.max_bets_per_race:
        return {"action": "skip",
                "reason": f"期待値は閾値を超えたが、同じレースでより配分の大きい上位 "
                          f"{policy.max_bets_per_race} 点に入らなかった"}
    return {"action": "skip", "reason": "ケリー配分が最低購入単位に満たなかった"}


def explain_race(race: pd.DataFrame, predictor: Predictor, policy: BetPolicy, bankroll: float,
                 with_result: bool = True, max_explained: int = 6) -> Dict:
    """One race: the card, the decision on every runner, and the reasoning."""
    race = race.reset_index(drop=True)
    cols = predictor.ranker.feature_cols
    contrib, base = score_contributions(predictor, race)
    lam, mu = predictor.run_down.lam, predictor.run_down.mu

    bets = select_bets(race, bankroll, policy, run_down=(lam, mu))
    backing: Dict[str, List[Dict]] = {}
    for b in bets:
        for leg in b.legs or (b.entrant_id,):
            backing.setdefault(str(leg), []).append({"ticket": b.bet_type, "stake": b.stake, "ev": b.ev})
    n_runners = int(race["n_runners"].iloc[0]) if "n_runners" in race else len(race)
    finishes = ({str(e): int(f) for e, f in zip(race["entrant_id"], race["finish_position"])}
                if with_result and "finish_position" in race else {})

    inv = 1.0 / race["win_odds"].clip(lower=1.01)
    market_p = (inv / inv.sum()).to_numpy()
    ev = (race["p_win"] * race["win_odds"]).to_numpy()
    order = np.argsort(-ev)
    rank_of = {int(i): pos for pos, i in enumerate(order)}

    runners = []
    for i in range(len(race)):
        r = race.iloc[i]
        row = {"entrant_id": str(r["entrant_id"]), "p_win": float(r["p_win"]),
               "odds": None if not np.isfinite(r["win_odds"]) else float(r["win_odds"]), "ev": float(ev[i])}
        post = int(r["post_position"]) if "post_position" in race else None
        name = str(r["entrant_name"]) if "entrant_name" in race else ""
        jockey_name = str(r["jockey_name"]) if "jockey_name" in race else ""
        runners.append({
            **row,
            "post_position": post,
            # what the screens print; the id stays for joining and settlement
            "label": runner_label(name, post, row["entrant_id"]),
            "name": name or None,
            "jockey_id": str(r.get("jockey_id", "")),
            "jockey_name": jockey_name or None,
            "place_odds": None if "place_odds" not in race or not np.isfinite(r["place_odds"]) else float(r["place_odds"]),
            "market_p": float(market_p[i]),
            "edge": float(r["p_win"] - market_p[i]),
            "pricing": {"score": float(r["score"]), "p_plackett_luce": float(r.get("p_win_raw", np.nan)),
                        "p_calibrated": float(r.get("p_win_model", np.nan)), "p_final": float(r["p_win"])},
            "decision": decision_for(row, policy, backing, rank_of[i]),
            "factors": top_factors(contrib[i], r[cols], cols),
            "finish_position": finishes.get(str(r["entrant_id"])),
        })
    runners.sort(key=lambda x: -x["p_win"])
    # Every runner keeps its decision - "why not this one" is half the product.
    # Feature attributions are the bulk of the payload, so they are kept for the
    # contenders and for anything actually backed.
    backed = set(backing)
    for pos, r in enumerate(runners):
        if pos >= max_explained and r["entrant_id"] not in backed:
            r["factors"] = []

    label_of = {r["entrant_id"]: r["label"] for r in runners}
    bet_rows = []
    for b in bets:
        won = bet_wins(b, finishes, n_runners) if finishes else None
        legs = list(b.legs) or [b.entrant_id]
        bet_rows.append({"ticket": b.bet_type, "ticket_ja": TICKET_JA.get(b.bet_type, b.bet_type),
                         "selection": legs,
                         "selection_label": [label_of.get(str(x), str(x)) for x in legs],
                         "prob": round(b.prob, 5), "odds": b.odds,
                         "ev": round(b.ev, 4), "stake": b.stake, "fraction": round(b.fraction, 6), "won": won,
                         "profit": (b.stake * (b.odds - 1.0) if won else -b.stake) if won is not None else None})

    meta = {c: (r0 if not isinstance(r0 := race[c].iloc[0], (np.integer, np.floating)) else r0.item())
            for c in ("venue", "race_no", "distance_m", "surface", "going", "race_class", "n_runners", "organizer")
            if c in race}
    meta["venue_name"] = venue_name(meta.get("venue"))
    top = runners[0]
    # "本命" is reserved for the runner actually backed (narrative.py). This line
    # is about the model's top-ranked runner, which is often a different horse.
    summary = (f"予測1位は{top['label']} (予測勝率 {top['p_win'] * 100:.1f}%、市場 {top['market_p'] * 100:.1f}%)。"
               + (f"{len(bet_rows)} 点購入、合計 {sum(b['stake'] for b in bet_rows):,.0f}円。"
                  if bet_rows else "期待値が閾値を超えた買い目が無く、見送り。"))
    return {"race_id": str(race["race_id"].iloc[0]), **meta, "title": race_title(meta),
            "conditions": race_conditions(meta), "summary": summary,
            "place_depth": place_payout_depth(n_runners), "base_score": float(base[0]),
            "n_bets": len(bet_rows), "total_stake": sum(b["stake"] for b in bet_rows),
            "bets": bet_rows, "runners": runners}


def write_day(payload: Dict, output_dir: str = ARCHIVE_DIR) -> Path:
    """Save one explained day and fold it into the index, newest first."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{payload['date']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    index_path = out / "index.json"
    rows = (json.loads(index_path.read_text(encoding="utf-8")) or {}).get("days", []) if index_path.exists() else []
    rows = [r for r in rows if r["date"] != payload["date"]]
    rows.append({k: payload[k] for k in ("date", "n_races", "n_races_bet", "n_bets", "total_stake",
                                         "settled", "profit", "recovery")})
    rows.sort(key=lambda r: r["date"], reverse=True)
    index_path.write_text(json.dumps({"days": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def explain_day(day: pd.DataFrame, predictor: Predictor, policy: BetPolicy, bankroll: float,
                with_result: bool = True, max_explained: int = 6) -> Dict:
    races = [explain_race(g, predictor, policy, bankroll, with_result, max_explained)
             for _, g in day.groupby("race_id", sort=True)]
    # Race number first: across venues that is roughly the running order, which
    # is the order a card is actually worked through. True off times need L1.
    races.sort(key=lambda r: (int(r.get("race_no") or 0), str(r.get("venue"))))
    settled = [b for r in races for b in r["bets"] if b["won"] is not None]
    staked = sum(b["stake"] for b in settled)
    profit = sum(b["profit"] for b in settled)
    payload = {
        "date": f"{pd.Timestamp(day['race_date'].iloc[0]):%Y-%m-%d}",
        "n_races": len(races), "n_races_bet": sum(1 for r in races if r["n_bets"]),
        "n_bets": sum(r["n_bets"] for r in races), "total_stake": sum(r["total_stake"] for r in races),
        "settled": bool(settled),
        "profit": profit if settled else None,
        "recovery": (staked + profit) / staked if settled and staked else None,
        "policy": {"alpha": policy.alpha, "ev_threshold": policy.ev_threshold,
                   "max_bets_per_race": policy.max_bets_per_race, "ticket_types": list(policy.ticket_types)},
        "run_down": predictor.run_down.to_dict(),
        "bankroll": bankroll,
        "venues": sorted({r.get("venue_name") or r.get("venue") for r in races}),
        "races": races,
    }
    return enrich_day(payload)


# ---------------------------------------------------------------------------
def _load_days(data_path: str, dates: List[pd.Timestamp]) -> pd.DataFrame:
    """Read only the requested race days out of the feature table."""
    wanted = {d.normalize() for d in dates}
    keep = []
    for chunk in pd.read_csv(data_path, chunksize=200_000, parse_dates=["race_date"],
                             dtype={"race_id": str, "entrant_id": str, "jockey_id": str, "trainer_id": str}):
        hit = chunk[chunk["race_date"].isin(wanted)]
        if not hit.empty:
            keep.append(hit)
    return pd.concat(keep, ignore_index=True) if keep else pd.DataFrame()


def _policy(alpha, ev, max_bets, tickets) -> BetPolicy:
    from src.live.paper_trader import default_policy

    pol, _ = default_policy(alpha, ev, max_bets,
                            ticket_types=tuple(t.strip() for t in tickets.split(",") if t.strip()))
    return pol


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Build the explained race archive the 予想 page reads."""


@cli.command()
@click.option("--data", "data_path", default="processed/train.csv", show_default=True)
@click.option("--model_dir", default="artifacts/", show_default=True)
@click.option("--start", default=None, help="first race day; default is --days back from the last one")
@click.option("--end", default=None, help="last race day; default is the latest in the data")
@click.option("--days", type=int, default=7, show_default=True, help="how many race days back from --end")
@click.option("--alpha", type=float, default=None)
@click.option("--ev_threshold", type=float, default=None)
@click.option("--max_bets_per_race", type=int, default=None)
@click.option("--tickets", default="win,place,quinella", show_default=True)
@click.option("--bankroll", type=float, default=1_000_000.0, show_default=True)
@click.option("--max_explained", type=int, default=6, show_default=True,
              help="keep feature attributions for this many contenders per race (backed runners always keep theirs)")
@click.option("--output_dir", default=ARCHIVE_DIR, show_default=True)
def archive(data_path, model_dir, start, end, days, alpha, ev_threshold, max_bets_per_race, tickets, bankroll,
            max_explained, output_dir):
    """Explain a range of stored race days, results included."""
    meta_path = Path(data_path).with_name("features.json")
    if not meta_path.exists():
        raise click.ClickException(f"{meta_path} not found; run src/data/preprocess.py first")
    all_dates = pd.to_datetime(pd.read_csv(data_path, usecols=["race_date"])["race_date"]).drop_duplicates()
    all_dates = all_dates.sort_values()
    last = pd.Timestamp(end) if end else all_dates.iloc[-1]
    if start:
        picked = [d for d in all_dates if pd.Timestamp(start) <= d <= last]
    else:
        picked = [d for d in all_dates if d <= last][-max(1, days):]
    if not picked:
        raise click.ClickException("no race days in that range")

    predictor = Predictor.load(Path(model_dir))
    policy = _policy(alpha, ev_threshold, max_bets_per_race, tickets)
    log.info("explaining %d race days (%s .. %s) with %s", len(picked), picked[0].date(), picked[-1].date(),
             "/".join(policy.ticket_types))
    table = _load_days(data_path, picked)
    if table.empty:
        raise click.ClickException("those race days are not in the feature table")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for day, g in table.groupby("race_date", sort=True):
        priced = predictor.predict(g)
        payload = explain_day(priced, predictor, policy, bankroll, with_result=True, max_explained=max_explained)
        write_day(payload, str(out))
        index.append({k: payload[k] for k in ("date", "n_races", "n_races_bet", "n_bets", "total_stake",
                                              "settled", "profit", "recovery")})
        click.echo(f"{payload['date']}  {payload['n_races']:3d} レース  購入 {payload['n_bets']:3d} 点  "
                   f"{payload['total_stake']:>9,.0f}円  "
                   + (f"回収率 {payload['recovery'] * 100:.1f}%" if payload["recovery"] else "未確定"))
    click.echo(f"wrote {len(index)} days -> {out}/")


@cli.command()
@click.option("--date", required=True)
@click.option("--output_dir", default=ARCHIVE_DIR, show_default=True)
def day(date, output_dir):
    """Print one stored day's reasoning as text."""
    path = Path(output_dir) / f"{pd.Timestamp(date):%Y-%m-%d}.json"
    if not path.exists():
        raise click.ClickException(f"{path} not found; run `archive` first")
    d = json.loads(path.read_text(encoding="utf-8"))
    click.echo(f"{d['date']}  {d['n_races']} レース / 購入 {d['n_bets']} 点 / {d['total_stake']:,.0f}円")
    for r in d["races"]:
        click.echo("-" * 78)
        click.echo(f"{r['title']}  {r['conditions']}")
        if not r.get("narrative"):
            click.echo(f"  {r['summary']}")
        for b in r["bets"]:
            mark = "" if b["won"] is None else ("  的中" if b["won"] else "  外れ")
            click.echo(f"  → {b['ticket_ja']} {'+'.join(b['selection_label'])} {b['odds']:.1f}倍 "
                       f"EV{b['ev']:.2f} {b['stake']:,.0f}円{mark}")
        if r.get("narrative"):
            click.echo("  " + r["narrative"])


if __name__ == "__main__":
    cli()
