"""Turn the model's reasoning into sentences a tipster can hand to a customer.

The explanation layer produces facts: a feature, a value, and how much it moved
the score. Those are diagnostics. What gets sold is a sentence, and a sentence
can lie in ways a number cannot, so two rules hold throughout:

* **Every phrase is a restatement of a measured value.** "このコースでの騎手成績が
  出走馬中 3 位" is a fact from the data. "鉄板" is not, and never appears.
* **The downside is written too.** A tip that only lists reasons to bet is an
  advertisement. Factors that pushed the runner down are reported in the same
  sentence structure as the ones that pushed it up, and a pass gets the same
  care as a play.

Confidence comes from the Kelly fraction, which already combines the edge and
the probability, rather than from a separate score invented for display.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

TICKET_JA = {"win": "単勝", "place": "複勝", "quinella": "馬連"}
SURFACE_JA = {"turf": "芝", "dirt": "ダート"}

#: Grades are **relative to the day**: the share of the largest Kelly fraction
#: found that day. An absolute scale would be a claim about real-world edge
#: that nothing here supports, while "how does this compare to the best thing I
#: found today" is exactly the question a card is worked through to answer.
GRADE_BANDS = ((0.70, "主力"), (0.35, "対抗"), (0.0, "押さえ"))
GRADE_NOTE = {"主力": "この日で最も厚く配分された部類", "対抗": "主力に次ぐ配分",
              "押さえ": "配分は小さい"}


def _pos_from_rank(pct: Optional[float], n_runners: int) -> Optional[int]:
    """Rank features are stored as a percentile where low is always better."""
    if pct is None or n_runners <= 0:
        return None
    return max(1, min(n_runners, int(round(pct * n_runners)) or 1))


def _norm_pos_word(v: float) -> str:
    if v < 0.30:
        return "上位で安定"
    if v < 0.45:
        return "平均より上"
    if v < 0.60:
        return "平均並み"
    return "苦戦気味"


def _pct(v: float) -> str:
    """One decimal below 10%, where a whole number throws the reading away."""
    pct = v * 100
    return f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"


def factor_phrase(feature: str, label: str, value, direction: str, n_runners: int) -> Optional[str]:
    """One clause describing what this feature actually says about the runner."""
    if value is None:
        return None
    numeric = isinstance(value, (int, float))

    if feature.endswith("_rank") and numeric:
        pos = _pos_from_rank(float(value), n_runners)
        base = re.sub(r"の出走馬内順位$", "", label)
        return f"{base}が出走馬中 {pos} 番目" if pos else None
    if feature.endswith("_starts") and numeric:
        n = int(value)
        if n == 0:
            return f"{label}なし"
        return f"{label} {n} 回"
    if feature.endswith("_days_since") and numeric:
        return f"{label} {int(value)} 日"
    if feature.endswith("_resid") and numeric:
        return f"{label}は" + ("期待を上回る" if float(value) < 0 else "期待を下回る")
    if feature.endswith("_norm_pos") and numeric:
        return f"{label}は{_norm_pos_word(float(value))}"
    if (feature.endswith("_win_rate") or feature.endswith("_place_rate") or feature.endswith("_win")) and numeric:
        return f"{label} {_pct(float(value))}"
    if feature == "sw_same_jockey":
        return "前走と同じ騎手" if float(value) >= 0.5 else "乗り替わり"
    if feature == "sw_first_time_pair":
        return "この騎手とは初コンビ" if float(value) >= 0.5 else "コンビ経験あり"
    if feature == "ent_dist_change" and numeric:
        d = int(value)
        return "前走と同じ距離" if d == 0 else f"前走から{'延長' if d > 0 else '短縮'} {abs(d)}m"
    if feature == "ent_class_change" and numeric:
        d = float(value)
        return "同クラス" if d == 0 else ("昇級" if d > 0 else "降級")
    if feature == "ent_last_pos" and numeric:
        return f"前走 {int(value)} 着"
    if feature in ("body_weight", "weight_carried") and numeric:
        return f"{label} {float(value):.0f}kg"
    if feature == "body_weight_diff" and numeric:
        d = float(value)
        return "馬体重増減なし" if d == 0 else f"馬体重 {d:+.0f}kg"
    if feature == "age" and numeric:
        return f"{int(value)} 歳"
    if feature == "distance_m" and numeric:
        return f"{int(value)}m 戦"
    if feature in ("post_position", "draw") and numeric:
        return f"{label} {int(value)}"
    if not numeric:
        return f"{label} {SURFACE_JA.get(str(value), value)}"
    return f"{label}が{'有利' if direction == 'up' else '不利'}に働いた"


def runner_reasons(runner: Dict, n_runners: int) -> Dict[str, List[str]]:
    """Reasons for and against, in the order the model weighted them."""
    out: Dict[str, List[str]] = {"for": [], "against": []}
    for f in runner.get("factors") or []:
        phrase = factor_phrase(f["feature"], f["label"], f.get("value"), f["direction"], n_runners)
        if phrase:
            out["for" if f["direction"] == "up" else "against"].append(phrase)
    return out


def confidence(fraction: float, day_max_fraction: float) -> Dict:
    """Grade a bet against the biggest Kelly fraction of the same day."""
    share = 0.0 if day_max_fraction <= 0 else min(1.0, fraction / day_max_fraction)
    grade = next(g for lo, g in GRADE_BANDS if share >= lo)
    return {"grade": grade, "share": round(share, 4), "note": GRADE_NOTE[grade], "relative": True}


def _label(runner: Optional[Dict]) -> str:
    """How a runner is named in prose: "3番" or "3番 <名前>", never its id."""
    if not runner:
        return "—"
    if runner.get("label"):
        return str(runner["label"])
    post = runner.get("post_position")
    return f"{int(post)}番" if post else str(runner.get("entrant_id", "—"))


def _leg_labels(bet: Dict, race: Dict) -> List[str]:
    """Label each leg of a ticket, falling back to the runner list if needed."""
    if bet.get("selection_label"):
        return [str(x) for x in bet["selection_label"]]
    by_id = {r.get("entrant_id"): r for r in race["runners"]}
    return [_label(by_id.get(leg)) for leg in bet["selection"]]


def _lead_runner(race: Dict) -> Dict:
    backed = [r for r in race["runners"] if r["decision"]["action"] == "bet"]
    return backed[0] if backed else race["runners"][0]


def race_narrative(race: Dict, ev_threshold: float) -> str:
    """The tipster's own read of the race, in one paragraph."""
    n = race.get("n_runners") or len(race["runners"])
    top = race["runners"][0]
    lead = _lead_runner(race)
    reasons = runner_reasons(lead, n)
    bits: List[str] = []

    edge_pt = (lead["p_win"] - lead["market_p"]) * 100
    bits.append(f"本命は{_label(lead)}。予測勝率 {_pct(lead['p_win'])} に対し市場は "
                f"{_pct(lead['market_p'])} で、{abs(edge_pt):.1f} ポイント"
                + ("高く見ています。" if edge_pt >= 0 else "低く見ています。"))
    if reasons["for"]:
        bits.append("評価の根拠は" + "、".join(reasons["for"][:3]) + "。")
    if reasons["against"]:
        bits.append("割引材料は" + "、".join(reasons["against"][:2]) + "。")

    if race["bets"]:
        b = race["bets"][0]
        name = TICKET_JA.get(b["ticket"], b["ticket"])
        prob_word = "勝率" if b["ticket"] == "win" else f"{name}圏内に入る確率"
        bits.append(f"{name} {b['odds']:.1f} 倍。{prob_word} {_pct(b['prob'])} との積で期待値 {b['ev']:.2f} となり、"
                    f"閾値 {ev_threshold:.2f} を超えたため {b['stake']:,.0f}円 を配分しました。")
        if b["ticket"] != "win":
            bits.append("勝ち切る見込みが薄くても、圏内に入る確率に対してオッズが割高なら買い目になります。")
        if len(race["bets"]) > 1:
            bits.append(f"ほかに {len(race['bets']) - 1} 点。")
    else:
        bits.append(f"ただし最も期待値の高い {_label(top)} でも {top['ev']:.2f} で、"
                    f"閾値 {ev_threshold:.2f} に届きません。控除率のあるレースで優位が無いまま買えば、"
                    "長期的には確実に負けるため見送ります。")
    return "".join(bits)


def race_tip(race: Dict, ev_threshold: float) -> str:
    """The block a tipster copies and sends. Plain text, no markup."""
    head = f"【{race['title']}】{race['conditions']}"
    if not race["bets"]:
        top = race["runners"][0]
        return (f"{head}\n見送り\n"
                f"理由: 最高でも期待値 {top['ev']:.2f} (閾値 {ev_threshold:.2f})。"
                "オッズに対して勝率が足りません。")

    n = race.get("n_runners") or len(race["runners"])
    lines = [head]
    for b in race["bets"]:
        lines.append(f"◎ {TICKET_JA.get(b['ticket'], b['ticket'])} {' + '.join(_leg_labels(b, race))}  "
                     f"{b['odds']:.1f}倍  {b['stake']:,.0f}円")
    lead = _lead_runner(race)
    reasons = runner_reasons(lead, n)
    if reasons["for"]:
        lines.append("根拠: " + "、".join(reasons["for"][:3]) + "。")
    if reasons["against"]:
        lines.append("懸念: " + "、".join(reasons["against"][:2]) + "。")
    b0 = race["bets"][0]
    name0 = TICKET_JA.get(b0["ticket"], b0["ticket"])
    prob_label = "予測勝率" if b0["ticket"] == "win" else f"{name0}圏内の確率"
    lines.append(f"{prob_label} {_pct(b0['prob'])} / 単勝オッズから見た市場評価 {_pct(lead['market_p'])} / "
                 f"期待値 {b0['ev']:.2f}")
    return "\n".join(lines)


def day_tip(day: Dict, top_n: int = 5) -> str:
    """A day's picks as one message, strongest first."""
    plays = []
    for r in day["races"]:
        for b in r["bets"]:
            plays.append((b.get("confidence", {}).get("share", 0.0), r, b))
    plays.sort(key=lambda x: -x[0])
    header = (f"{day['date']} の予想\n"
              f"{day['n_races']} レース中 {day['n_races_bet']} レースで勝負、{day['n_bets']} 点 / "
              f"{day['total_stake']:,.0f}円")
    if plays:
        _, hr, hb = plays[0]
        header += (f"\n主力は {hr['title']} の"
                   f"{TICKET_JA.get(hb['ticket'], hb['ticket'])} (期待値 {hb['ev']:.2f})")
    if not plays:
        return header + "\n\n本日は期待値が閾値を超える買い目がありませんでした。見送りです。"
    lines = [header, ""]
    for share, r, b in plays[:top_n]:
        grade = b.get("confidence", {}).get("grade", "")
        lines.append(f"[{grade}] {r['title']}  {TICKET_JA.get(b['ticket'], b['ticket'])} "
                     f"{' + '.join(_leg_labels(b, r))}  "
                     f"{b['odds']:.1f}倍  期待値 {b['ev']:.2f}  {b['stake']:,.0f}円")
    if len(plays) > top_n:
        lines.append(f"ほか {len(plays) - top_n} 点")
    lines.append("")
    lines.append("期待値が閾値を超えた買い目だけを、ケリー基準で資金配分しています。"
                 "見送りのレースは優位が無いと判断したものです。")
    return "\n".join(lines)


def _bet_fraction(bet: Dict, day: Dict) -> float:
    frac = bet.get("fraction")
    if frac is None:
        frac = (bet["stake"] / day["bankroll"]) if day.get("bankroll") else 0.0
    return float(frac)


def enrich_day(day: Dict) -> Dict:
    """Attach grades and sentences to an explained day, in place."""
    ev_threshold = float((day.get("policy") or {}).get("ev_threshold", 1.0))
    day_max = max((_bet_fraction(b, day) for r in day["races"] for b in r["bets"]), default=0.0)
    best: Tuple[float, Optional[Dict]] = (-1.0, None)
    for race in day["races"]:
        n = race.get("n_runners") or len(race["runners"])
        for b in race["bets"]:
            b["confidence"] = confidence(_bet_fraction(b, day), day_max)
        for r in race["runners"]:
            r["reasons"] = runner_reasons(r, n)
        race["narrative"] = race_narrative(race, ev_threshold)
        race["tip_text"] = race_tip(race, ev_threshold)
        # by share, not by ``max`` over the grade strings: 主力/対抗/押さえ do not
        # sort by codepoint in the order they rank.
        lead_bet = max(race["bets"], key=lambda x: x["confidence"]["share"], default=None)
        share = lead_bet["confidence"]["share"] if lead_bet else 0.0
        race["confidence"] = lead_bet["confidence"]["grade"] if lead_bet else None
        race["confidence_share"] = share
        # a day with nothing worth backing has no headline; ``share`` is 0.0 for
        # those races and would otherwise win against the sentinel.
        if race["bets"] and share > best[0]:
            best = (share, race)
    day["headline_race_id"] = best[1]["race_id"] if best[1] else None
    day["tip_text"] = day_tip(day)
    return day
