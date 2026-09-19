"""The sentences sold to a customer must say only what the numbers say.

A tip is the one artefact that leaves the system and reaches a person who
cannot check it, so these tests hold the narrative to the same standard as the
decision: every claim traceable to a value, the downside stated, and a pass
described as a decision rather than as silence.
"""
import pytest

from src.live.narrative import (
    GRADE_BANDS,
    confidence,
    day_tip,
    enrich_day,
    factor_phrase,
    race_narrative,
    race_tip,
    runner_reasons,
)


# --------------------------------------------------------------------------
# fixtures: a minimal explained day in the shape src/live/explain.py writes
# --------------------------------------------------------------------------
def _runner(eid, p_win, market_p, ev, action, stake=0.0, ticket="win", factors=None, post=1):
    return {
        "entrant_id": eid, "p_win": p_win, "market_p": market_p, "ev": ev,
        "post_position": post, "jockey_id": "J001", "odds": 1.0 / max(market_p, 1e-6),
        "place_odds": None, "edge": p_win - market_p,
        "pricing": {"score": 0.5, "p_plackett_luce": p_win, "p_calibrated": p_win, "p_final": p_win},
        "decision": {"action": action, "reason": "…", "stake": stake,
                     "tickets": [ticket] if action == "bet" else [], "ticket_ja": "単勝"},
        "factors": factors if factors is not None else [
            {"feature": "jky_win_rate", "label": "騎手の勝率", "value": 0.18,
             "contribution": 0.31, "direction": "up"},
            {"feature": "entc_dist_norm_pos", "label": "この馬の距離帯別の着順", "value": 0.62,
             "contribution": -0.12, "direction": "down"},
        ],
        "finish_position": None,
    }


def _race(race_id, bets, runners, n_runners=10):
    return {
        "race_id": race_id, "venue": "01", "venue_name": "札幌", "race_no": 1,
        "distance_m": 1800, "surface": "turf", "going": "good", "race_class": "maiden",
        "n_runners": n_runners, "organizer": "JRA",
        "title": "札幌 1R", "conditions": "1800m 芝 良 10頭", "summary": "…",
        "place_depth": 3, "base_score": 0.0,
        "n_bets": len(bets), "total_stake": sum(b["stake"] for b in bets),
        "bets": bets, "runners": runners,
    }


def _bet(ticket, selection, odds, prob, ev, stake, fraction):
    return {"ticket": ticket, "ticket_ja": {"win": "単勝", "place": "複勝", "quinella": "馬連"}[ticket],
            "selection": selection, "odds": odds, "prob": prob, "ev": ev,
            "stake": stake, "fraction": fraction, "won": None, "profit": None}


@pytest.fixture
def day():
    r1 = _race(
        "R1",
        [_bet("win", ["H1"], 4.0, 0.33, 1.32, 3000.0, 0.03)],
        [_runner("H1", 0.33, 0.25, 1.32, "bet", 3000.0),
         _runner("H2", 0.20, 0.30, 0.66, "pass", post=2)],
    )
    r2 = _race(
        "R2",
        [_bet("place", ["H3"], 3.0, 0.45, 1.35, 600.0, 0.006)],
        [_runner("H3", 0.12, 0.10, 0.90, "bet", 600.0, ticket="place"),
         _runner("H4", 0.09, 0.11, 0.81, "pass", post=2)],
    )
    r3 = _race("R3", [], [_runner("H5", 0.10, 0.12, 0.97, "pass"),
                          _runner("H6", 0.08, 0.10, 0.90, "pass", post=2)])
    return {"date": "2024-03-03", "n_races": 3, "n_races_bet": 2, "n_bets": 2,
            "total_stake": 3600.0, "settled": False, "profit": None, "recovery": None,
            "policy": {"alpha": 0.1, "ev_threshold": 1.15, "max_bets_per_race": 2,
                       "ticket_types": ["win", "place"]},
            "run_down": {"lam": 0.8, "mu": 0.9}, "bankroll": 100000.0,
            "venues": ["札幌"], "races": [r1, r2, r3]}


# --------------------------------------------------------------------------
# phrases restate measured values
# --------------------------------------------------------------------------
def test_a_rank_feature_becomes_a_position_out_of_the_field():
    """A percentile means nothing to a reader; "3 番目" is the same fact."""
    assert factor_phrase("ent_avg_norm_pos_rank", "通算の平均着順の出走馬内順位", 0.30, "up", 10) \
        == "通算の平均着順が出走馬中 3 番目"


def test_rank_phrases_never_claim_a_position_outside_the_field():
    for pct in (0.0, 0.01, 0.5, 0.999, 1.0):
        phrase = factor_phrase("x_rank", "着順の出走馬内順位", pct, "up", 8)
        pos = int(phrase.split("出走馬中 ")[1].split(" ")[0])
        assert 1 <= pos <= 8


def test_a_missing_value_produces_no_phrase_rather_than_a_guess():
    assert factor_phrase("jky_win_rate", "騎手の勝率", None, "up", 10) is None


def test_reasons_split_by_the_direction_the_model_actually_moved():
    r = _runner("H1", 0.3, 0.2, 1.3, "bet", 100.0)
    reasons = runner_reasons(r, 10)
    assert reasons["for"] == ["騎手の勝率 18%"]
    assert reasons["against"] == ["この馬の距離帯別の着順は苦戦気味"]


def test_no_phrase_asserts_certainty():
    """Nothing in the vocabulary promises an outcome."""
    banned = ["鉄板", "確実", "必勝", "絶対", "堅い"]
    for feat, label, val in [("jky_win_rate", "騎手の勝率", 0.9),
                             ("ent_avg_norm_pos_rank", "着順の出走馬内順位", 0.01),
                             ("sw_same_jockey", "同騎手", 1.0)]:
        phrase = factor_phrase(feat, label, val, "up", 12)
        assert not any(w in phrase for w in banned), phrase


# --------------------------------------------------------------------------
# grading is relative to the day, and ordered
# --------------------------------------------------------------------------
def test_grades_are_shares_of_the_days_largest_allocation():
    assert confidence(0.03, 0.03)["grade"] == "主力"
    assert confidence(0.015, 0.03)["grade"] == "対抗"
    assert confidence(0.002, 0.03)["grade"] == "押さえ"


def test_a_grade_never_claims_an_absolute_edge():
    """The same stake grades differently on a day with a bigger best bet."""
    assert confidence(0.01, 0.01)["grade"] == "主力"
    assert confidence(0.01, 0.05)["grade"] != "主力"
    assert confidence(0.01, 0.01)["relative"] is True


def test_grade_bands_are_declared_strongest_first():
    lows = [lo for lo, _ in GRADE_BANDS]
    assert lows == sorted(lows, reverse=True) and lows[-1] == 0.0


def test_a_races_grade_comes_from_its_strongest_bet_not_string_order():
    """主力/対抗/押さえ do not sort by codepoint, so ``max`` over grades lies."""
    race = _race("R", [_bet("win", ["H1"], 4.0, 0.33, 1.32, 3000.0, 0.03),
                       _bet("place", ["H2"], 2.0, 0.60, 1.20, 200.0, 0.002)],
                 [_runner("H1", 0.33, 0.25, 1.32, "bet", 3000.0),
                  _runner("H2", 0.20, 0.30, 1.20, "bet", 200.0, ticket="place", post=2)])
    day = {"date": "2024-01-01", "n_races": 1, "n_races_bet": 1, "n_bets": 2,
           "total_stake": 3200.0, "bankroll": 100000.0,
           "policy": {"ev_threshold": 1.15}, "races": [race]}
    enrich_day(day)
    assert race["confidence"] == "主力"


# --------------------------------------------------------------------------
# the paragraph and the copyable block
# --------------------------------------------------------------------------
def test_a_play_states_the_edge_the_price_and_the_stake(day):
    text = race_narrative(day["races"][0], 1.15)
    assert "1番" in text and "8.0 ポイント" in text
    assert "1.32" in text and "3,000円" in text and "1.15" in text


def test_prose_names_a_runner_by_its_number_never_by_its_registration_id(day):
    """"H1" identifies the horse to the database and to nobody else."""
    for race in day["races"]:
        text = race_narrative(race, 1.15) + race_tip(race, 1.15)
        assert not any(r["entrant_id"] in text for r in race["runners"]), text


def test_a_name_is_used_when_the_source_carries_one(day):
    race = day["races"][0]
    for run in race["runners"]:
        run["label"] = f"{run['post_position']}番 テスト馬{run['post_position']}"
    for b in race["bets"]:
        b["selection_label"] = ["1番 テスト馬1"]
    assert "1番 テスト馬1" in race_narrative(race, 1.15)
    assert "1番 テスト馬1" in race_tip(race, 1.15)


def test_a_non_win_ticket_never_quotes_the_win_probability(day):
    """A place bet priced off the win probability would misstate its own EV."""
    text = race_narrative(day["races"][1], 1.15)
    assert "45%" in text            # the place probability the bet was sized on
    assert "12" not in text.split("複勝")[1]


def test_a_pass_is_written_as_a_decision_with_its_number(day):
    text = race_narrative(day["races"][2], 1.15)
    assert "0.97" in text and "1.15" in text
    assert "見送" in text


def test_the_tip_block_carries_the_downside_too(day):
    text = race_tip(day["races"][0], 1.15)
    assert text.startswith("【札幌 1R】")
    assert "根拠:" in text and "懸念:" in text


def test_the_tip_block_prints_the_number_a_customer_would_key_in(day):
    """The 馬番 is what goes on the betting slip, so it is what gets printed."""
    text = race_tip(day["races"][0], 1.15)
    assert "◎ 単勝 1番" in text and "H1" not in text


def test_a_pass_still_produces_a_sendable_block(day):
    text = race_tip(day["races"][2], 1.15)
    assert "見送り" in text and "0.97" in text


# --------------------------------------------------------------------------
# the day payload
# --------------------------------------------------------------------------
def test_enrich_adds_the_fields_the_dashboard_reads(day):
    out = enrich_day(day)
    assert out["headline_race_id"] == "R1"
    assert out["tip_text"].startswith("2024-03-03 の予想")
    for race in out["races"]:
        assert race["narrative"] and race["tip_text"]
        assert "confidence" in race and "confidence_share" in race
        for run in race["runners"]:
            assert set(run["reasons"]) == {"for", "against"}


def test_the_headline_is_the_biggest_allocation_of_the_day(day):
    out = enrich_day(day)
    head = next(r for r in out["races"] if r["race_id"] == out["headline_race_id"])
    assert head["confidence_share"] == max(r["confidence_share"] for r in out["races"])
    assert head["confidence"] == "主力"


def test_a_race_with_no_bet_gets_no_grade(day):
    out = enrich_day(day)
    passed = next(r for r in out["races"] if r["race_id"] == "R3")
    assert passed["confidence"] is None and passed["confidence_share"] == 0.0


def test_the_day_message_reports_the_passes_as_well_as_the_plays(day):
    text = day_tip(enrich_day(day))
    assert "3 レース中 2 レースで勝負" in text
    assert "見送り" in text


def test_a_day_with_nothing_worth_backing_says_so():
    empty = {"date": "2024-03-04", "n_races": 2, "n_races_bet": 0, "n_bets": 0,
             "total_stake": 0.0, "bankroll": 100000.0, "policy": {"ev_threshold": 1.15},
             "races": [_race("R1", [], [_runner("H1", 0.1, 0.12, 0.9, "pass")])]}
    out = enrich_day(empty)
    assert out["headline_race_id"] is None
    assert "見送り" in out["tip_text"]


def test_grades_fall_back_to_stake_share_when_fraction_is_missing(day):
    """Archives written before fractions were recorded must still grade."""
    for race in day["races"]:
        for b in race["bets"]:
            b.pop("fraction")
    out = enrich_day(day)
    assert out["races"][0]["confidence"] == "主力"
    assert out["races"][1]["confidence"] in {"対抗", "押さえ"}
