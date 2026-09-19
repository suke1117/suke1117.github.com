import json

import numpy as np
import pandas as pd
import pytest

from src.web.export_dashboard import _num, equity_series, model_block, monthly_series, odds_breakdown, sweep_rows


def test_num_is_json_safe():
    assert _num(1.5) == 1.5
    assert _num(np.nan) is None and _num(np.inf) is None and _num(None) is None
    assert _num("x") is None
    json.dumps({"v": _num(float("nan"))})  # must not raise


def test_equity_series_computes_drawdown_from_the_running_peak():
    daily = pd.DataFrame({"race_date": pd.to_datetime(["2022-01-01", "2022-01-02", "2022-01-03"]),
                          "bankroll": [100.0, 150.0, 120.0]})
    out = equity_series(daily)
    assert [r["date"] for r in out] == ["2022-01-01", "2022-01-02", "2022-01-03"]
    assert out[0]["drawdown"] == 0.0 and out[1]["drawdown"] == 0.0
    assert out[2]["drawdown"] == pytest.approx(-0.2)


def _bets():
    return pd.DataFrame({
        "race_date": pd.to_datetime(["2022-01-01", "2022-01-15", "2022-02-05"]),
        "stake": [100.0, 100.0, 200.0],
        "profit": [500.0, -100.0, -200.0],
        "odds": [6.0, 12.0, 30.0],
        "prob": [0.2, 0.1, 0.05],
        "won": [1, 0, 0],
    })


def test_monthly_series_aggregates_by_calendar_month():
    rows = monthly_series(_bets())
    assert [r["month"] for r in rows] == ["2022-01", "2022-02"]
    assert rows[0]["n_bets"] == 2 and rows[0]["profit"] == 400.0
    assert rows[0]["recovery"] == pytest.approx(3.0)   # 200 staked -> 600 back
    assert rows[1]["recovery"] == pytest.approx(0.0)   # total loss


def test_odds_breakdown_covers_every_band_even_when_empty():
    rows = odds_breakdown(_bets())
    assert [r["band"] for r in rows] == ["1-5倍", "5-10倍", "10-20倍", "20-50倍", "50倍+"]
    assert rows[0]["n_bets"] == 0 and rows[0]["recovery"] is None
    assert rows[1]["n_bets"] == 1 and rows[1]["hit_rate"] == 1.0
    assert sum(r["n_bets"] for r in rows) == len(_bets())


def test_empty_inputs_produce_empty_sections():
    assert equity_series(pd.DataFrame()) == []
    assert monthly_series(pd.DataFrame()) == []
    assert odds_breakdown(pd.DataFrame()) == []
    assert sweep_rows(pd.DataFrame()) == []


def test_model_block_survives_a_missing_test_slice():
    b = model_block({"pl_temperature": 1.05, "market_blend": {"a": 0.6, "b": 0.4, "enabled": True}})
    assert b["top1_hit_rate"] is None and b["reliability"] == []
    json.dumps(b)


# --------------------------------------------------------------------------
# the one label the page must never get wrong
# --------------------------------------------------------------------------
def test_synthetic_is_assumed_unless_preprocess_says_otherwise():
    """A wrapper class once turned generated data into '実データによる結果です'."""
    from src.web.export_dashboard import _data_note

    assert "合成データ" in _data_note({})                       # nothing said -> assume synthetic
    assert "合成データ" in _data_note({"source": "CombinedSource"})
    assert "合成データ" in _data_note({"is_synthetic": True, "source": "CombinedSource"})
    assert "実データ" in _data_note({"is_synthetic": False})


def test_preprocess_detects_synthetic_through_a_wrapper():
    from src.data.preprocess import _is_synthetic
    from src.data.sources import CombinedSource, JRAVanCSVSource, SyntheticSource

    syn = SyntheticSource(start="2020-01-04", end="2020-01-12", n_horses=60)
    real = JRAVanCSVSource("raw_data/")
    assert _is_synthetic(syn) is True
    assert _is_synthetic(real) is False
    assert _is_synthetic(CombinedSource([syn])) is True
    assert _is_synthetic(CombinedSource([real, syn])) is True, "one generated source makes the whole set generated"
    assert _is_synthetic(CombinedSource([real])) is False


def test_ticket_breakdown_splits_by_type():
    from src.web.export_dashboard import ticket_breakdown

    bets = pd.DataFrame({"bet_type": ["win", "place", "place"], "stake": [100.0, 100.0, 100.0],
                         "profit": [-100.0, 200.0, -100.0], "odds": [5.0, 3.0, 4.0], "ev": [1.1, 1.2, 1.3],
                         "won": [0, 1, 0]})
    rows = {r["ticket"]: r for r in ticket_breakdown(bets)}
    assert rows["place"]["n_bets"] == 2 and rows["place"]["recovery"] == pytest.approx(1.5)
    assert rows["win"]["recovery"] == pytest.approx(0.0)
    assert ticket_breakdown(pd.DataFrame()) == []
