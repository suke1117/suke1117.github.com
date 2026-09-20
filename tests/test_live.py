"""The live path must obey the same rules as the backtest, or it is a new system."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.live.ledger import OPEN, SETTLED, VOID, Ledger, PaperBet
from src.live.paper_trader import build_today, default_policy
from src.live.providers import ProviderError, get_provider
from src.live.providers.base import CARD_ENTRY_COLUMNS, LiveProvider
from src.live.providers.csv_card import CsvCardProvider
from src.live.providers.demo import DemoProvider
from src.data.schema import RESULT_COLUMNS


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def processed_dir(tmp_path_factory, small_raw):
    """A tiny processed/ directory the demo provider can read."""
    races, entries = small_raw
    d = tmp_path_factory.mktemp("processed")
    races.to_csv(d / "races.csv", index=False)
    entries.to_csv(d / "entries.csv", index=False)
    return d


def test_demo_card_withholds_the_results(processed_dir):
    prov = DemoProvider(str(processed_dir))
    day = prov.latest_date()
    races, entries = prov.fetch_card(day)
    assert not races.empty and not entries.empty
    leaked = [c for c in RESULT_COLUMNS if c in entries.columns]
    assert leaked == [], f"the card handed over result columns: {leaked}"
    assert set(entries.columns) == set(CARD_ENTRY_COLUMNS)
    # the same day's results are available separately, which is the real sequence
    res = prov.fetch_results(day)
    assert res is not None and "finish_position" in res


def test_demo_odds_cover_the_card(processed_dir):
    prov = DemoProvider(str(processed_dir))
    day = prov.latest_date()
    _, entries = prov.fetch_card(day)
    odds = prov.fetch_odds(day)
    assert len(odds) == len(entries)
    assert (odds["win_odds"] > 1.0).all()


def test_demo_rejects_a_day_with_no_racing(processed_dir):
    with pytest.raises(ProviderError, match="no stored racing"):
        DemoProvider(str(processed_dir)).fetch_card(pd.Timestamp("1999-01-01"))


def test_card_validation_catches_a_broken_card():
    races = pd.DataFrame({"race_id": ["R1"], "race_date": ["2026-09-20"], "venue": ["05"], "race_no": [1],
                          "distance_m": [1600], "surface": ["turf"], "going": ["good"], "race_class": ["1win"],
                          "n_runners": [2]})
    entries = pd.DataFrame({"race_id": ["R1", "R1"], "entrant_id": ["H1", "H1"], "post_position": [1, 2],
                            "draw": [1, 1], "jockey_id": ["J1", "J2"], "trainer_id": ["T1", "T1"], "age": [4, 4],
                            "sex": ["M", "F"], "weight_carried": [55.0, 55.0], "body_weight": [480.0, 470.0],
                            "body_weight_diff": [0.0, 0.0]})
    with pytest.raises(ProviderError, match="same entrant twice"):
        LiveProvider.validate_card(races, entries)
    entries.loc[1, "entrant_id"] = "H2"
    entries.loc[1, "race_id"] = "R9"
    with pytest.raises(ProviderError, match="races not on the card"):
        LiveProvider.validate_card(races, entries)


def test_odds_validation_rejects_unusable_prices():
    with pytest.raises(ProviderError, match="missing columns"):
        LiveProvider.validate_odds(pd.DataFrame({"race_id": ["R1"]}))
    with pytest.raises(ProviderError, match="decimal odds"):
        LiveProvider.validate_odds(pd.DataFrame({"race_id": ["R1"], "entrant_id": ["H1"], "win_odds": [0.5]}))


def test_csv_provider_explains_itself_when_the_file_is_absent(tmp_path):
    prov = CsvCardProvider(str(tmp_path))
    with pytest.raises(ProviderError, match="template"):
        prov.fetch_card(pd.Timestamp("2026-09-20"))
    assert prov.fetch_results(pd.Timestamp("2026-09-20")) is None


def test_unknown_provider_lists_the_real_ones():
    with pytest.raises(ProviderError, match="csv"):
        get_provider("nope")


# --------------------------------------------------------------------------
# today's feature rows
# --------------------------------------------------------------------------
def test_build_today_uses_only_history_before_the_card(processed_dir, small_table):
    prov = DemoProvider(str(processed_dir))
    day = prov.latest_date()
    races_today, entries_today = prov.fetch_card(day)
    odds = prov.fetch_odds(day)
    hist = (pd.read_csv(processed_dir / "races.csv", dtype={"race_id": str}, parse_dates=["race_date"]),
            pd.read_csv(processed_dir / "entries.csv", dtype={"race_id": str, "entrant_id": str, "jockey_id": str,
                                                              "trainer_id": str}, parse_dates=["race_date"]))
    today = build_today(hist, races_today, entries_today, odds, day)

    assert (today["race_date"] == pd.Timestamp(day).normalize()).all()
    assert set(today["race_id"]) == set(races_today["race_id"])
    # the day's own results are not in the feature row: the reference table built
    # with the full history agrees with what today's build produced
    ref, _ = small_table
    ref_today = ref[ref["race_date"] == pd.Timestamp(day).normalize()]
    merged = today.merge(ref_today, on=["race_id", "entrant_id"], suffixes=("_live", "_ref"))
    assert len(merged) == len(today)
    for col in ("ent_starts", "jky_starts", "entc_venue_starts", "pair_starts"):
        assert (merged[f"{col}_live"] == merged[f"{col}_ref"]).all(), col


def test_build_today_refuses_a_date_with_no_history(processed_dir):
    prov = DemoProvider(str(processed_dir))
    day = pd.Timestamp(prov.available_dates().iloc[0])
    races_today, entries_today = prov.fetch_card(day)
    odds = prov.fetch_odds(day)
    empty = (pd.DataFrame(columns=["race_id", "race_date"]), pd.DataFrame(columns=["race_id", "entrant_id"]))
    hist = (pd.read_csv(processed_dir / "races.csv", dtype={"race_id": str}, parse_dates=["race_date"]),
            pd.read_csv(processed_dir / "entries.csv", dtype={"race_id": str, "entrant_id": str}, parse_dates=["race_date"]))
    import click
    with pytest.raises(click.ClickException, match="no history"):
        build_today(hist, races_today, entries_today, odds, day)


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------
def _bet(**kw):
    base = dict(race_date="2026-09-20", race_id="R1", entrant_id="H1", bet_type="win", prob=0.2,
                odds_at_bet=6.0, ev=1.2, fraction=0.005, stake=5000.0)
    base.update(kw)
    return PaperBet.new(**base)


def test_ledger_is_append_only_and_keeps_the_pre_result_decision(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", 1_000_000)
    led.place([_bet()])
    led.settle("2026-09-20", pd.DataFrame({"race_id": ["R1"], "entrant_id": ["H1"], "finish_position": [1]}))
    lines = [json.loads(x) for x in (tmp_path / "l.jsonl").read_text().splitlines()]
    assert len(lines) == 2, "settlement must append, not overwrite"
    assert lines[0]["status"] == OPEN and lines[0]["profit"] is None
    assert lines[1]["status"] == SETTLED and lines[1]["profit"] == 25000
    assert len(led.current()) == 1


def test_open_stakes_are_not_spendable_twice(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", 1_000_000)
    led.place([_bet(stake=30000.0)])
    assert led.bankroll() == 1_000_000
    assert led.available() == 970_000


def test_a_missing_result_voids_rather_than_loses(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", 1_000_000)
    led.place([_bet(entrant_id="H7")])
    counts = led.settle("2026-09-20", pd.DataFrame({"race_id": ["R1"], "entrant_id": ["H1"], "finish_position": [1]}))
    assert counts["void"] == 1 and counts["settled"] == 0
    row = led.current()[0]
    assert row["status"] == VOID and row["profit"] == 0.0
    assert led.bankroll() == 1_000_000


def test_settlement_applies_the_odds_haircut(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", 1_000_000)
    led.place([_bet(odds_at_bet=11.0, stake=1000.0)])
    led.settle("2026-09-20", pd.DataFrame({"race_id": ["R1"], "entrant_id": ["H1"], "finish_position": [1]}),
               odds_haircut=0.1)
    assert led.current()[0]["profit"] == pytest.approx(9000.0)   # 1 + 10 * 0.9 = 10.0


def test_daily_history_runs_the_bankroll_forward(tmp_path):
    led = Ledger(tmp_path / "l.jsonl", 1_000_000)
    led.place([_bet(race_date="2026-09-20"), _bet(race_date="2026-09-21", race_id="R2", entrant_id="H2")])
    led.settle("2026-09-20", pd.DataFrame({"race_id": ["R1"], "entrant_id": ["H1"], "finish_position": [1]}))
    led.settle("2026-09-21", pd.DataFrame({"race_id": ["R2"], "entrant_id": ["H2"], "finish_position": [4]}))
    days = led.daily()
    assert [d["date"] for d in days] == ["2026-09-20", "2026-09-21"]
    assert days[-1]["bankroll"] == 1_000_000 + 25000 - 5000


def test_ledger_rejects_a_corrupted_file(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('{"bet_id": "a"}\nnot json\n')
    with pytest.raises(ValueError, match="not valid JSON"):
        Ledger(p, 1_000_000)


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------
def test_live_policy_follows_the_walk_forward_search(tmp_path):
    p = tmp_path / "sweep.json"
    p.write_text(json.dumps({"selected": {"alpha": 0.02, "ev_threshold": 1.3, "max_bets_per_race": 1}}))
    policy, source = default_policy(None, None, None, str(p))
    assert (policy.alpha, policy.ev_threshold, policy.max_bets_per_race) == (0.02, 1.3, 1)
    assert source == "sweep"
    policy, source = default_policy(0.05, None, None, str(p))
    assert policy.alpha == 0.05 and "command line" in source


def test_live_policy_cannot_exceed_the_hard_kelly_cap(tmp_path):
    from src.betting.kelly_calculator import KellyAlphaError

    with pytest.raises(KellyAlphaError):
        default_policy(0.9, None, None, str(tmp_path / "missing.json"))


# --------------------------------------------------------------------------
# the slip must describe the bets it actually placed
# --------------------------------------------------------------------------
def test_a_multi_leg_ticket_still_counts_as_a_bet_on_its_race():
    """A quinella is stored under "H1+H2", which no single runner id matches.

    Keying the per-runner stakes on the whole id left those races reading as
    "no bet" in the slip, so the summary under-reported how many races were
    played while the bet list said otherwise.
    """
    from src.betting.strategy import BetPolicy
    from src.live.paper_trader import slip_payload

    priced = pd.DataFrame({
        "race_id": ["R1", "R1", "R2", "R2"],
        "entrant_id": ["H1", "H2", "H3", "H4"],
        "post_position": [1, 2, 1, 2],
        "jockey_id": ["J1", "J2", "J3", "J4"],
        "p_win": [0.4, 0.3, 0.5, 0.2],
        "win_odds": [3.0, 5.0, 2.5, 9.0],
        "ev": [1.2, 1.5, 1.25, 1.8],
    })
    races_today = pd.DataFrame({
        "race_id": ["R1", "R2"], "venue": ["01", "01"], "race_no": [1, 2],
        "distance_m": [1800, 1200], "surface": ["turf", "dirt"],
        "going": ["good", "good"], "race_class": ["maiden", "maiden"],
    })
    bets = [
        PaperBet.new(race_date="2024-01-01", race_id="R1", entrant_id="H1", bet_type="win",
                     prob=0.4, odds_at_bet=3.0, ev=1.2, fraction=0.01, stake=1000.0),
        PaperBet.new(race_date="2024-01-01", race_id="R2", entrant_id="H3+H4", bet_type="quinella",
                     prob=0.12, odds_at_bet=15.0, ev=1.8, fraction=0.004, stake=400.0),
    ]
    payload = slip_payload(pd.Timestamp("2024-01-01"), "demo", priced, races_today, bets,
                           BetPolicy(alpha=0.1), "default", 100000.0)

    assert payload["summary"]["n_races_bet"] == 2
    by_id = {r["race_id"]: r for r in payload["races"]}
    assert by_id["R2"]["n_bets"] == 1
    staked = {x["entrant_id"]: x["stake"] for x in by_id["R2"]["runners"]}
    assert staked == {"H3": 400.0, "H4": 400.0}


def test_a_second_book_does_not_overwrite_the_first_ones_slips(tmp_path):
    """--live_root chose where cards were read from but not where slips went.

    Running a 地方 book beside a JRA one, or a test beside the real account,
    wrote both days into live_data/slips/<date>.json and the second erased the
    first - including the explained day the 予想 page reads.
    """
    from src.live.paper_trader import _archive_dir, _slip_dir

    a, b = tmp_path / "jra", tmp_path / "nar"
    assert _slip_dir(str(a)) != _slip_dir(str(b))
    assert _archive_dir(str(a)) != _archive_dir(str(b))
    assert _slip_dir(str(a)) == a / "slips"
    assert _archive_dir(str(a)) == a / "explained"


def test_todays_card_keeps_its_names_when_the_history_has_none(processed_dir):
    """A history export and a daily card rarely come from the same place.

    Names are display-only, so dropping them because the *history* lacks the
    column left every screen saying "1番" on a card that had 馬名 in it.
    """
    prov = DemoProvider(str(processed_dir))
    day = prov.latest_date()
    races_today, entries_today = prov.fetch_card(day)
    entries_today = entries_today.assign(
        entrant_name=["ウマ" + str(i) for i in range(len(entries_today))])
    odds = prov.fetch_odds(day)
    hist = (pd.read_csv(processed_dir / "races.csv", dtype={"race_id": str}, parse_dates=["race_date"]),
            pd.read_csv(processed_dir / "entries.csv", dtype={"race_id": str, "entrant_id": str,
                                                              "jockey_id": str, "trainer_id": str},
                        parse_dates=["race_date"]))
    assert "entrant_name" not in hist[1].columns

    today = build_today(hist, races_today, entries_today, odds, day)
    assert "entrant_name" in today.columns
    assert today["entrant_name"].notna().all()


def test_a_card_with_foreign_headers_is_wired_by_a_mapping_file(tmp_path):
    """Retyping tomorrow's card because the headers differ stops it being used."""
    (tmp_path / "cards").mkdir()
    (tmp_path / "odds").mkdir()
    pd.DataFrame({
        "race_id": ["R1", "R1"], "race_date": ["2024-02-03"] * 2, "venue": ["05"] * 2,
        "race_no": [1, 1], "distance_m": [1600, 1600], "トラック": ["芝", "芝"],
        "馬場状態": ["良", "良"], "race_class": ["1勝"] * 2,
        "馬ID": ["H1", "H2"], "馬番": [1, 2], "draw": [1, 2], "騎手ID": ["J1", "J2"],
        "trainer_id": ["T1", "T2"], "age": [4, 5], "sex": ["牡", "牝"],
        "weight_carried": [55.0, 54.0], "body_weight": [480.0, 462.0],
        "body_weight_diff": [0.0, -2.0], "馬名": ["ウマA", "ウマB"],
    }).to_csv(tmp_path / "cards" / "2024-02-03.csv", index=False)
    pd.DataFrame({"race_id": ["R1", "R1"], "馬ID": ["H1", "H2"],
                  "win_odds": [2.4, 5.1]}).to_csv(tmp_path / "odds" / "2024-02-03.csv", index=False)
    (tmp_path / "mapping.yml").write_text(
        "entries:\n  馬ID: entrant_id\n  馬番: post_position\n  騎手ID: jockey_id\n"
        "  馬名: entrant_name\nraces:\n  トラック: surface\n  馬場状態: going\n", encoding="utf-8")

    races, entries = CsvCardProvider(str(tmp_path)).fetch_card(pd.Timestamp("2024-02-03"))
    assert list(races["surface"]) == ["turf"]          # 芝 translates with no mapping entry
    assert list(races["going"]) == ["good"]
    assert list(entries["entrant_id"]) == ["H1", "H2"]
    assert list(entries["entrant_name"]) == ["ウマA", "ウマB"]
    odds = CsvCardProvider(str(tmp_path)).fetch_odds(pd.Timestamp("2024-02-03"))
    assert list(odds["entrant_id"]) == ["H1", "H2"]


def test_a_card_missing_a_column_says_which_headers_it_did_find(tmp_path):
    (tmp_path / "cards").mkdir()
    pd.DataFrame({"race_id": ["R1"], "馬ID": ["H1"]}).to_csv(
        tmp_path / "cards" / "2024-02-03.csv", index=False)
    with pytest.raises(ProviderError) as exc:
        CsvCardProvider(str(tmp_path)).fetch_card(pd.Timestamp("2024-02-03"))
    assert "mapping.yml" in str(exc.value) and "馬ID" in str(exc.value)
