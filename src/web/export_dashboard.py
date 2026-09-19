#!/usr/bin/env python
"""Bundle backtest, model and sweep outputs into one JSON file for the dashboard.

The web dashboard is a static page; this CLI produces the data it reads, so the
page can be regenerated after any backtest instead of being hand-maintained.

    python src/web/export_dashboard.py --backtest_dir backtest_results/ \
        --model_dir artifacts/ --output web/data.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from src.common.logging_utils import get_logger  # noqa: E402

log = get_logger("export")

ODDS_BUCKETS = [(1.0, 5.0), (5.0, 10.0), (10.0, 20.0), (20.0, 50.0), (50.0, 1e9)]
ODDS_LABELS = ["1-5倍", "5-10倍", "10-20倍", "20-50倍", "50倍+"]


def _num(x) -> Optional[float]:
    """JSON-safe float (NaN / inf become null)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _read_csv(path: Path, **kw) -> pd.DataFrame:
    return pd.read_csv(path, **kw) if path.exists() else pd.DataFrame()


def equity_series(daily: pd.DataFrame) -> List[Dict]:
    if daily.empty:
        return []
    d = daily.copy()
    d["race_date"] = pd.to_datetime(d["race_date"])
    peak = d["bankroll"].cummax()
    d["drawdown"] = d["bankroll"] / peak - 1.0
    return [{"date": r.race_date.strftime("%Y-%m-%d"), "bankroll": _num(r.bankroll), "drawdown": _num(r.drawdown)}
            for r in d.itertuples()]


def monthly_series(bets: pd.DataFrame) -> List[Dict]:
    if bets.empty:
        return []
    b = bets.copy()
    b["race_date"] = pd.to_datetime(b["race_date"])
    g = b.groupby(b["race_date"].dt.to_period("M"))
    rows = []
    for period, sub in g:
        staked = float(sub["stake"].sum())
        profit = float(sub["profit"].sum())
        rows.append({"month": str(period), "n_bets": int(len(sub)), "staked": _num(staked), "profit": _num(profit),
                     "recovery": _num((staked + profit) / staked if staked else np.nan),
                     "hit_rate": _num(sub["won"].mean())})
    return rows


def odds_breakdown(bets: pd.DataFrame) -> List[Dict]:
    """Where the edge actually came from, by odds band."""
    if bets.empty:
        return []
    rows = []
    for (lo, hi), label in zip(ODDS_BUCKETS, ODDS_LABELS):
        sub = bets[(bets["odds"] >= lo) & (bets["odds"] < hi)]
        if sub.empty:
            rows.append({"band": label, "n_bets": 0, "hit_rate": None, "recovery": None, "profit": 0.0,
                         "staked": 0.0, "avg_prob": None})
            continue
        staked, profit = float(sub["stake"].sum()), float(sub["profit"].sum())
        rows.append({"band": label, "n_bets": int(len(sub)), "hit_rate": _num(sub["won"].mean()),
                     "recovery": _num((staked + profit) / staked if staked else np.nan), "profit": _num(profit),
                     "staked": _num(staked), "avg_prob": _num(sub["prob"].mean())})
    return rows


def forecast_block(archive_dir: Path, embed_days: int) -> Dict:
    """The 予想 page's data: the day index, plus the most recent days in full.

    Only a few days are embedded so the published page stays small; the local
    API serves the rest, and the page falls back to it when a date is missing.
    """
    index_path = archive_dir / "index.json"
    if not index_path.exists():
        return {}
    index = (json.loads(index_path.read_text(encoding="utf-8")) or {}).get("days", [])
    days = {}
    for row in index[:max(0, embed_days)]:
        path = archive_dir / f"{row['date']}.json"
        if path.exists():
            days[row["date"]] = json.loads(path.read_text(encoding="utf-8"))
    return {"index": index, "days": days, "embedded": sorted(days), "archive_dir": str(archive_dir)}


def _data_note(meta: Dict) -> str:
    if meta.get("is_synthetic", True):
        return ("合成データによるデモです。実際のレース結果・オッズではありません。"
                "エッジの大きさは生成器の設定で決まるため、実運用の成績を示すものではありません。")
    return "実データによる結果です。"


def ticket_breakdown(bets: pd.DataFrame) -> List[Dict]:
    """Where the money came from, by ticket type."""
    if bets.empty or "bet_type" not in bets.columns:
        return []
    rows = []
    for t, g in bets.groupby("bet_type", sort=True):
        staked, profit = float(g["stake"].sum()), float(g["profit"].sum())
        rows.append({"ticket": t, "n_bets": int(len(g)), "hit_rate": _num(g["won"].mean()),
                     "avg_odds": _num(g["odds"].mean()), "avg_ev": _num(g["ev"].mean()),
                     "staked": _num(staked), "profit": _num(profit),
                     "recovery": _num((staked + profit) / staked if staked else np.nan)})
    return sorted(rows, key=lambda r: -(r["staked"] or 0))


def period_rows(periods: pd.DataFrame) -> List[Dict]:
    if periods.empty:
        return []
    p = periods.copy()
    for c in ("period_start", "period_end"):
        p[c] = pd.to_datetime(p[c]).dt.strftime("%Y-%m-%d")
    return [{"start": r.period_start, "end": r.period_end, "n_bets": int(r.n_bets), "staked": _num(r.staked),
             "profit": _num(r.profit), "recovery": _num(r.recovery_rate), "bankroll_end": _num(r.bankroll_end),
             "blend_a": _num(r.blend_a), "blend_b": _num(r.blend_b), "pl_temperature": _num(r.pl_temperature)}
            for r in p.itertuples()]


def sweep_rows(grid: pd.DataFrame) -> List[Dict]:
    if grid.empty:
        return []
    return [{"alpha": _num(r.alpha), "ev_threshold": _num(r.ev_threshold), "max_bets": int(r.max_bets_per_race),
             "n_bets": int(r.n_bets), "recovery": _num(r.roi_recovery_rate), "drawdown": _num(r.max_drawdown),
             "sharpe": _num(r.sharpe_daily_annualised), "growth": _num(r.log_growth_per_day),
             "total_return": _num(r.total_return)} for r in grid.itertuples()]


#: YAML folded scalars join wrapped lines with a space, which shows up as a gap
#: mid-sentence in Japanese. Drop a space only when both neighbours are non-ASCII.
_CJK_GAP = re.compile(r"(?<=[^\x00-\x7F])[ \t]+(?=[^\x00-\x7F])")
TEXT_FIELDS = ("title", "why", "done", "impact", "blocked_by", "result")


def normalize_text(value):
    return _CJK_GAP.sub("", value.strip()) if isinstance(value, str) else value


PRIORITY_ORDER = ["P0", "P1", "P2", "P3"]
REQUIRED_TASK_FIELDS = ("id", "title", "category", "priority", "effort", "status", "impact", "why", "done")


def load_roadmap(path: Path) -> Dict:
    """Read the improvement backlog and check it before the page has to render it.

    A malformed entry is a broken card on the dashboard, so the errors are
    raised here where the message is readable rather than in the browser.
    """
    if not path.exists():
        log.warning("%s not found; the dashboard will hide the roadmap section", path)
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tasks = doc.get("tasks", [])
    ids = {t.get("id") for t in tasks}
    for t in tasks:
        missing = [f for f in REQUIRED_TASK_FIELDS if not t.get(f)]
        if missing:
            raise ValueError(f"roadmap task {t.get('id', '?')} is missing: {missing}")
        if t["priority"] not in PRIORITY_ORDER:
            raise ValueError(f"roadmap task {t['id']} has unknown priority '{t['priority']}'")
        unknown = [d for d in (t.get("depends") or []) if d not in ids]
        if unknown:
            raise ValueError(f"roadmap task {t['id']} depends on unknown ids: {unknown}")
    if len(ids) != len(tasks):
        raise ValueError("roadmap contains duplicate task ids")
    for t in tasks:
        for f in TEXT_FIELDS:
            if f in t:
                t[f] = normalize_text(t[f])
    meta = {k: normalize_text(v) for k, v in (doc.get("meta") or {}).items()}
    evidence = [{k: normalize_text(v) for k, v in e.items()} for e in (doc.get("evidence") or [])]
    tasks = sorted(tasks, key=lambda t: (PRIORITY_ORDER.index(t["priority"]), t["id"]))
    counts: Dict[str, int] = {}
    for t in tasks:
        counts[t["priority"]] = counts.get(t["priority"], 0) + 1
    return {"meta": meta, "evidence": evidence, "tasks": tasks, "counts": counts,
            "categories": sorted({t["category"] for t in tasks})}


def ablation_rows(df: pd.DataFrame) -> List[Dict]:
    """What each feature family is worth, from src/models/ablation.py --each."""
    if df.empty:
        return []
    rows = []
    for r in df.itertuples():
        name = str(r.variant)
        rows.append({"family": name[2:] if name.startswith("- ") else name,
                     "is_baseline": not name.startswith("- "),
                     "n_features": int(r.n_features), "logloss": _num(r.winner_logloss_p_win_model),
                     "sd": _num(r.logloss_sd), "delta": _num(r.delta_logloss),
                     "top1": _num(r.top1_hit_rate), "ece": _num(r.ece_p_win_model)})
    return sorted(rows, key=lambda x: -(x["delta"] or 0))


def model_block(metrics: Dict) -> Dict:
    test = metrics.get("test", {})
    return {
        "pl_temperature": _num(metrics.get("pl_temperature")),
        "best_iteration": metrics.get("best_iteration"),
        "market_blend": metrics.get("market_blend", {}),
        "split": metrics.get("split", {}),
        "top1_hit_rate": _num(test.get("top1_hit_rate")),
        "favourite_hit_rate": _num(test.get("favourite_hit_rate")),
        "top3_contains_winner": _num(test.get("top3_contains_winner")),
        "n_races": test.get("n_races"),
        "logloss": {k: _num(test.get(f"winner_logloss_{k}")) for k in ("p_win_raw", "p_win_model", "p_win", "market")},
        "ece": {k: _num(test.get(f"ece_{k}")) for k in ("p_win_raw", "p_win_model", "p_win")},
        "reliability": [{"p_mean": _num(r["p_mean"]), "obs_rate": _num(r["obs_rate"]), "n": int(r["n"])}
                        for r in test.get("reliability_p_win", [])],
    }


@click.command()
@click.option("--backtest_dir", default="backtest_results/", show_default=True)
@click.option("--model_dir", default="artifacts/", show_default=True)
@click.option("--sweep_dir", default="backtest_results/sweep/", show_default=True)
@click.option("--roadmap", "roadmap_path", default="docs/roadmap.yml", show_default=True)
@click.option("--explained", "explained_dir", default="live_data/explained", show_default=True)
@click.option("--embed_days", type=int, default=3, show_default=True,
              help="how many explained race days to bake into the page")
@click.option("--output", default="web/data.json", show_default=True)
@click.option("--data_note", default=None, help="one line describing the data source shown on the dashboard")
def main(backtest_dir: str, model_dir: str, sweep_dir: str, roadmap_path: str, explained_dir: str, embed_days: int,
         output: str, data_note: Optional[str]) -> None:
    bt, md, sw = Path(backtest_dir), Path(model_dir), Path(sweep_dir)
    summary = json.loads((bt / "summary.json").read_text()) if (bt / "summary.json").exists() else {}
    metrics = json.loads((md / "metrics.json").read_text()) if (md / "metrics.json").exists() else {}
    sweep_summary = json.loads((sw / "sweep_summary.json").read_text()) if (sw / "sweep_summary.json").exists() else {}
    bets = _read_csv(bt / "bets.csv")
    daily = _read_csv(bt / "equity_curve.csv")
    periods = _read_csv(bt / "periods.csv")
    grid = _read_csv(sw / "sweep_grid.csv")
    ablation = _read_csv(bt / "ablation.csv")
    plan_path = bt / "target_plan.json"
    target_plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
    features = _read_csv(md / "feature_importance.csv", index_col=0)
    processed_meta = {}
    pm = Path("processed/features.json")
    if pm.exists():
        processed_meta = json.loads(pm.read_text())

    payload = {
        "generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "data_note": data_note or _data_note(processed_meta),
        # If preprocess did not say, assume synthetic: claiming real results
        # from generated data is a far worse error than the reverse.
        "is_synthetic": bool(processed_meta.get("is_synthetic", True)),
        "dataset": {"source": processed_meta.get("source"), "n_races": processed_meta.get("n_races"),
                    "n_rows": processed_meta.get("n_rows"), "date_min": processed_meta.get("date_min"),
                    "date_max": processed_meta.get("date_max"),
                    "n_features": len(processed_meta.get("feature_columns", []))},
        "significance": {"recovery_ci": summary.get("recovery_ci"), "recovery_per_bet": _num(summary.get("recovery_per_bet")),
                         "recovery_per_bet_ci": summary.get("recovery_per_bet_ci"),
                         "t_stat": _num(summary.get("per_bet_t_stat")), "per_bet_sd": _num(summary.get("per_bet_sd")),
                         "significant_at_95": bool(summary.get("significant_at_95")),
                         "bets_needed_for_15pt_edge": _num(summary.get("bets_needed_for_15pt_edge"))},
        "kpi": {k: _num(summary.get(k)) for k in ("roi_recovery_rate", "max_drawdown", "sharpe_daily_annualised",
                                                  "hit_rate", "total_return", "final_bankroll", "start_bankroll",
                                                  "total_staked", "total_profit", "avg_odds_bet", "avg_ev_bet",
                                                  "avg_stake_fraction")}
        | {"n_bets": summary.get("n_bets"), "n_races_bet": summary.get("n_races_bet"),
           "n_race_days": summary.get("n_race_days")},
        "policy": summary.get("policy", {}),
        "backtest_window": summary.get("period", {}),
        "odds_haircut": _num(summary.get("odds_haircut")),
        "compound": summary.get("compound"),
        "equity": equity_series(daily),
        "monthly": monthly_series(bets),
        "odds_bands": odds_breakdown(bets),
        "tickets": ticket_breakdown(bets),
        "periods": period_rows(periods),
        "model": model_block(metrics),
        "features": [{"name": str(i), "gain": _num(g)} for i, g in features["gain"].head(15).items()]
        if not features.empty else [],
        "sweep": {"grid": sweep_rows(grid), "summary": sweep_summary},
        "ablation": ablation_rows(ablation),
        "target_plan": target_plan,
        "forecast": forecast_block(Path(explained_dir), embed_days),
        "roadmap": load_roadmap(Path(roadmap_path)),
    }

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    log.info("wrote %s (%.1f KB) | equity %d pts | sweep %d pts | bets %d | roadmap %d tasks | 予想 %d/%d 日",
             out, out.stat().st_size / 1024, len(payload["equity"]), len(payload["sweep"]["grid"]), len(bets),
             len(payload["roadmap"].get("tasks", [])), len(payload["forecast"].get("embedded", [])),
             len(payload["forecast"].get("index", [])))


if __name__ == "__main__":
    main()
