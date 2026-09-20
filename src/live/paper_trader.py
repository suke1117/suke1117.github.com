#!/usr/bin/env python
"""Run the paper-betting loop for one race day.

Same code path as the backtest: the card is appended to the stored history,
the as-of features are rebuilt, the calibrated probabilities go through the
same Plackett-Luce and isotonic layers, and the same Fractional Kelly sizing
decides the stake.  Nothing about live betting relaxes the rules - the history
is trimmed to days strictly before the card, so a runner's features cannot see
its own race.

    python src/live/paper_trader.py template --date 2026-09-20
    python src/live/paper_trader.py bet      --date 2026-09-20 --provider csv
    python src/live/paper_trader.py settle   --date 2026-09-20 --provider csv
    python src/live/paper_trader.py status

``--provider demo`` replays a stored day so the loop can be exercised before a
data subscription exists.
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

from src.betting.strategy import BetPolicy, select_bets  # noqa: E402
from src.common.config import (  # noqa: E402
    DEFAULT_BANKROLL_YEN,
    DEFAULT_EV_THRESHOLD,
    DEFAULT_KELLY_ALPHA,
    MAX_DAILY_EXPOSURE,
)
from src.common.logging_utils import get_logger  # noqa: E402
from src.data.features import build_features  # noqa: E402
from src.data.schema import DEFAULT_ORGANIZER, RESULT_COLUMNS  # noqa: E402
from src.live.explain import explain_day, runner_label, write_day  # noqa: E402
from src.live.ledger import Ledger, PaperBet  # noqa: E402
from src.live.providers import ProviderError, get_provider  # noqa: E402
from src.live.providers.base import (CARD_RACE_COLUMNS, OPTIONAL_CARD_ENTRY_COLUMNS,  # noqa: E402
                                     OPTIONAL_CARD_RACE_COLUMNS)
from src.models.predict import Predictor  # noqa: E402

log = get_logger("paper")

LEDGER_PATH = "live_data/ledger.jsonl"
def _slip_dir(live_root: str) -> Path:
    """Slips and explained days live under the same root as the cards.

    Otherwise two books run side by side (JRA and 地方, or a test against the
    real one) write their slips into the same file and overwrite each other.
    """
    return Path(live_root) / "slips"


def _archive_dir(live_root: str) -> Path:
    return Path(live_root) / "explained"


def _provider(name: str, processed: str, root: str, config: str):
    kwargs = {"demo": {"processed_dir": processed}, "csv": {"root": root}, "http": {"config_path": config},
              "jravan": {}}[name]
    return get_provider(name, **kwargs)


def load_history(processed: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    d = Path(processed)
    rp, ep = d / "races.csv", d / "entries.csv"
    if not rp.exists() or not ep.exists():
        raise click.ClickException(f"{rp} / {ep} not found. Run `python src/data/preprocess.py` first.")
    races = pd.read_csv(rp, dtype={"race_id": str}, parse_dates=["race_date"])
    entries = pd.read_csv(ep, dtype={"race_id": str, "entrant_id": str, "jockey_id": str, "trainer_id": str},
                          parse_dates=["race_date"])
    return races, entries


def default_policy(alpha: Optional[float], ev: Optional[float], max_bets: Optional[int],
                   sweep_path: str = "backtest_results/sweep/sweep_summary.json",
                   ticket_types: Tuple[str, ...] = ("win",)) -> Tuple[BetPolicy, str]:
    """Use the parameters the walk-forward search selected, unless overridden."""
    chosen, source = {}, "defaults"
    p = Path(sweep_path)
    if p.exists():
        sel = (json.loads(p.read_text()) or {}).get("selected") or {}
        if sel:
            chosen = {"alpha": sel.get("alpha"), "ev_threshold": sel.get("ev_threshold"),
                      "max_bets_per_race": sel.get("max_bets_per_race")}
            source = "sweep"
    policy = BetPolicy(
        alpha=alpha if alpha is not None else chosen.get("alpha") or DEFAULT_KELLY_ALPHA,
        ev_threshold=ev if ev is not None else chosen.get("ev_threshold") or DEFAULT_EV_THRESHOLD,
        max_bets_per_race=max_bets if max_bets is not None else chosen.get("max_bets_per_race") or 3,
        ticket_types=ticket_types,
    )
    if any(v is not None for v in (alpha, ev, max_bets)):
        source = "command line" if source == "defaults" else "sweep + command line"
    return policy, source


def build_today(history: Tuple[pd.DataFrame, pd.DataFrame], races_today: pd.DataFrame, entries_today: pd.DataFrame,
                odds: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """Feature rows for ``date``, built from history strictly before it."""
    hist_races, hist_entries = history
    date = pd.Timestamp(date).normalize()
    keep = hist_races["race_date"] < date
    hist_races = hist_races[keep]
    hist_entries = hist_entries[hist_entries["race_id"].isin(set(hist_races["race_id"]))]
    if hist_races.empty:
        raise click.ClickException(f"no history before {date:%Y-%m-%d}; the model has nothing to stand on")

    today = entries_today.merge(odds[["race_id", "entrant_id", "win_odds"]], on=["race_id", "entrant_id"], how="left")
    missing = today["win_odds"].isna().sum()
    if missing:
        log.warning("%d runners have no odds and cannot be priced", missing)
    for col in RESULT_COLUMNS:
        if col not in today.columns:
            today[col] = np.nan
    today["race_date"] = date

    # Optional race columns become trained features once the data has them, so
    # a card that drops one silently disagrees with the model.
    extra = [c for c in OPTIONAL_CARD_RACE_COLUMNS if c in hist_races.columns]
    for c in extra:
        if c not in races_today.columns:
            races_today = races_today.assign(**{c: DEFAULT_ORGANIZER if c == "organizer" else None})
    cols_r = CARD_RACE_COLUMNS + extra
    races = pd.concat([hist_races[cols_r], races_today[cols_r]], ignore_index=True)
    cols = [c for c in hist_entries.columns if c in today.columns or c in RESULT_COLUMNS]
    entries = pd.concat([hist_entries[cols], today.reindex(columns=cols)], ignore_index=True)

    table, _, _ = build_features(races, entries)
    out = table[table["race_date"] == date].copy()
    if out.empty:
        raise click.ClickException(f"the card for {date:%Y-%m-%d} produced no feature rows")

    # Names are display-only, so today's card keeps its own even when the
    # history has none - which is the normal case, since a history export and a
    # daily card rarely come from the same place.
    names = [c for c in OPTIONAL_CARD_ENTRY_COLUMNS if c in today.columns and c not in out.columns]
    if names:
        out = out.merge(today[["race_id", "entrant_id", *names]], on=["race_id", "entrant_id"], how="left")
    return out


def price_card(today: pd.DataFrame, model_dir: str):
    """Returns (priced card, the fitted run-down discount used for exotic tickets)."""
    predictor = Predictor.load(Path(model_dir))
    need = [c for c in predictor.ranker.feature_cols if c not in today.columns]
    if need:
        raise click.ClickException(
            f"the model expects features this build did not produce: {need[:6]}. "
            "Re-run preprocess and train so the model and the feature builder are the same version.")
    priced = predictor.predict(today)
    priced["ev"] = priced["p_win"] * priced["win_odds"]
    return priced, (predictor.run_down.lam, predictor.run_down.mu)


def slip_payload(date: pd.Timestamp, provider: str, priced: pd.DataFrame, races_today: pd.DataFrame,
                 bets: List[PaperBet], policy: BetPolicy, policy_source: str, bankroll: float,
                 max_daily_exposure: float = MAX_DAILY_EXPOSURE) -> Dict:
    meta = races_today.set_index("race_id").to_dict("index")
    # A multi-leg ticket is stored under "H1+H2", so a per-runner lookup on the
    # whole id finds nothing and the race reads as unbet. Credit every leg.
    staked: Dict[Tuple[str, str], float] = {}
    n_bets_by_race: Dict[str, int] = {}
    for b in bets:
        for leg in str(b.entrant_id).split("+"):
            staked[(b.race_id, leg)] = staked.get((b.race_id, leg), 0.0) + b.stake
        n_bets_by_race[b.race_id] = n_bets_by_race.get(b.race_id, 0) + 1
    races = []
    for race_id, g in priced.sort_values(["race_id", "p_win"], ascending=[True, False]).groupby("race_id", sort=True):
        m = meta.get(race_id, {})
        runners = [{
            "entrant_id": r.entrant_id, "post_position": int(r.post_position) if "post_position" in priced else None,
            "label": runner_label(getattr(r, "entrant_name", ""),
                                  int(r.post_position) if "post_position" in priced else None, r.entrant_id),
            "name": getattr(r, "entrant_name", None) or None,
            "jockey_id": getattr(r, "jockey_id", None),
            "jockey_name": getattr(r, "jockey_name", None) or None, "p_win": round(float(r.p_win), 5),
            "win_odds": None if pd.isna(r.win_odds) else float(r.win_odds),
            "ev": None if pd.isna(r.ev) else round(float(r.ev), 4),
            "stake": staked.get((race_id, r.entrant_id), 0.0),
        } for r in g.itertuples()]
        races.append({"race_id": race_id, "venue": m.get("venue"), "race_no": int(m.get("race_no", 0)),
                      "distance_m": int(m.get("distance_m", 0)), "surface": m.get("surface"), "going": m.get("going"),
                      "race_class": m.get("race_class"), "n_runners": len(runners),
                      "n_bets": n_bets_by_race.get(race_id, 0), "runners": runners})
    return {
        "date": f"{pd.Timestamp(date):%Y-%m-%d}",
        "generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "provider": provider,
        "bankroll": bankroll,
        "policy": {"alpha": policy.alpha, "ev_threshold": policy.ev_threshold,
                   "max_bets_per_race": policy.max_bets_per_race, "source": policy_source,
                   "max_daily_exposure": max_daily_exposure, "ticket_types": list(policy.ticket_types)},
        "summary": {"n_races": len(races), "n_races_bet": sum(1 for r in races if r["n_bets"]),
                    "n_bets": len(bets), "total_stake": sum(b.stake for b in bets),
                    "avg_ev": round(float(np.mean([b.ev for b in bets])), 4) if bets else None,
                    "avg_odds": round(float(np.mean([b.odds_at_bet for b in bets])), 2) if bets else None},
        "bets": [{"race_id": b.race_id, "entrant_id": b.entrant_id, "prob": round(b.prob, 5),
                  "odds": b.odds_at_bet, "ev": round(b.ev, 4), "stake": b.stake} for b in bets],
        "races": races,
    }


# ---------------------------------------------------------------------------
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Paper betting: build a slip for a race day, then settle it."""


common = [
    click.option("--processed", default="processed/", show_default=True),
    click.option("--live_root", default="live_data/", show_default=True),
    click.option("--provider_config", default="live_data/provider.yml", show_default=True),
    click.option("--ledger", "ledger_path", default=LEDGER_PATH, show_default=True),
]


def with_common(f):
    for opt in reversed(common):
        f = opt(f)
    return f


@cli.command()
@click.option("--date", required=True)
@click.option("--live_root", default="live_data/", show_default=True)
def template(date: str, live_root: str) -> None:
    """Write an empty card CSV with the required header."""
    from src.live.providers.base import (CARD_ENTRY_COLUMNS, CARD_RACE_COLUMNS,
                                         OPTIONAL_CARD_ENTRY_COLUMNS)

    # the name columns are optional, but filling them is what turns "H00918"
    # into something a reader recognises, so the template offers them
    cols = (CARD_RACE_COLUMNS + [c for c in CARD_ENTRY_COLUMNS if c != "race_id"]
            + OPTIONAL_CARD_ENTRY_COLUMNS)
    path = Path(live_root) / "cards" / f"{pd.Timestamp(date):%Y-%m-%d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=cols).to_csv(path, index=False)
    odds = Path(live_root) / "odds" / f"{pd.Timestamp(date):%Y-%m-%d}.csv"
    odds.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=["race_id", "entrant_id", "win_odds"]).to_csv(odds, index=False)
    click.echo(f"wrote {path}\nwrote {odds}\nFill both in, then: python src/live/paper_trader.py bet --date {date}")


@cli.command()
@click.option("--date", default=None, help="race day; defaults to today, or the provider's latest for --provider demo")
@click.option("--provider", "provider_name", type=click.Choice(["csv", "demo", "http", "jravan"]), default="csv",
              show_default=True)
@click.option("--model_dir", default="artifacts/", show_default=True)
@click.option("--alpha", type=float, default=None, help="override the Kelly fraction (hard max 0.25)")
@click.option("--ev_threshold", type=float, default=None)
@click.option("--max_bets_per_race", type=int, default=None)
@click.option("--bankroll", type=float, default=None, help="override the ledger's current bankroll")
@click.option("--start_bankroll", type=float, default=DEFAULT_BANKROLL_YEN, show_default=True)
@click.option("--tickets", default="win", show_default=True, help="comma-separated: win, place, quinella")
@click.option("--max_daily_exposure", type=float, default=MAX_DAILY_EXPOSURE, show_default=True,
              help="stop betting once this fraction of the bankroll is committed today")
@click.option("--dry_run", is_flag=True, help="print the slip without writing it to the ledger")
@with_common
def bet(date, provider_name, model_dir, alpha, ev_threshold, max_bets_per_race, bankroll, start_bankroll, tickets,
        max_daily_exposure, dry_run, processed, live_root, provider_config, ledger_path):
    """Price today's card and record the bets the policy fires on."""
    try:
        prov = _provider(provider_name, processed, live_root, provider_config)
    except ProviderError as exc:
        raise click.ClickException(str(exc))
    if date is None:
        date = prov.latest_date() if provider_name == "demo" else pd.Timestamp.now().normalize()
    date = pd.Timestamp(date).normalize()

    led = Ledger(ledger_path, start_bankroll)
    if led.has_date(f"{date:%Y-%m-%d}") and not dry_run:
        raise click.ClickException(
            f"the ledger already has bets for {date:%Y-%m-%d}. Betting twice on one day would double the exposure; "
            "use --dry_run to re-price it.")
    bank = bankroll if bankroll is not None else led.available()
    policy, policy_source = default_policy(alpha, ev_threshold, max_bets_per_race,
                                           ticket_types=tuple(t.strip() for t in tickets.split(",") if t.strip()))

    try:
        races_today, entries_today = prov.fetch_card(date)
        odds = prov.fetch_odds(date)
    except ProviderError as exc:
        raise click.ClickException(str(exc))
    log.info("%s: %d races / %d runners from provider '%s'", f"{date:%Y-%m-%d}", len(races_today), len(entries_today),
             prov.name)

    today = build_today(load_history(processed), races_today, entries_today, odds, date)
    priced, run_down = price_card(today, model_dir)

    if not (0 < max_daily_exposure <= MAX_DAILY_EXPOSURE):
        raise click.BadParameter(f"must be in (0, {MAX_DAILY_EXPOSURE}]", param_hint="--max_daily_exposure")
    # A backtest day self-limits because the bankroll moves between races. A live
    # day settles all at once, so the whole card is at risk simultaneously and
    # needs its own ceiling.
    daily_budget = bank * max_daily_exposure
    bets: List[PaperBet] = []
    running, committed, skipped = bank, 0.0, 0
    for race_id, race in priced.groupby("race_id", sort=True):
        race = race.dropna(subset=["win_odds"]).reset_index(drop=True)
        if race.empty:
            continue
        lam, mu = run_down
        for b in select_bets(race, running, policy, run_down=(lam, mu)):
            if committed + b.stake > daily_budget:
                skipped += 1
                continue
            bets.append(PaperBet.new(race_date=f"{date:%Y-%m-%d}", race_id=b.race_id, entrant_id=b.entrant_id,
                                     bet_type=b.bet_type, prob=b.prob, odds_at_bet=b.odds, ev=b.ev,
                                     fraction=b.fraction, stake=b.stake))
            running -= b.stake
            committed += b.stake
    if skipped:
        log.warning("%d bets dropped: the daily exposure cap of %.0f%% was reached", skipped,
                    max_daily_exposure * 100)

    payload = slip_payload(date, prov.name, priced, races_today, bets, policy, policy_source, bank,
                           max_daily_exposure)
    slip_path = _slip_dir(live_root) / f"{date:%Y-%m-%d}.json"
    slip_path.parent.mkdir(parents=True, exist_ok=True)
    slip_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Today's card goes into the same archive the 予想 page reads, so there is
    # one screen for "what to bet and why" rather than one for today and
    # another for every other day.
    from src.models.predict import Predictor as _P

    explained = explain_day(priced, _P.load(Path(model_dir)), policy, bank, with_result=False)
    explained_path = write_day(explained, str(_archive_dir(live_root)))
    if not dry_run:
        led.place(bets)

    s = payload["summary"]
    click.echo("=" * 74)
    click.echo(f"{date:%Y-%m-%d}  provider={prov.name}  bankroll={bank:,.0f}円"
               f"{'  [DRY RUN - nothing recorded]' if dry_run else ''}")
    click.echo(f"policy: alpha={policy.alpha} EV>={policy.ev_threshold} max {policy.max_bets_per_race}/race "
               f"({policy_source})")
    click.echo("-" * 74)
    if not bets:
        click.echo("No bet. No runner cleared the expected-value threshold, which is the system working,")
        click.echo("not failing: a day with no edge is a day to sit out.")
    else:
        label_of = {(r["race_id"], x["entrant_id"]): x["label"]
                    for r in payload["races"] for x in r["runners"]}
        click.echo(f"{'race':<16}  {'runner':<22}{'odds':>7}{'p':>8}{'EV':>7}{'stake':>10}")
        for b in bets:
            shown = " + ".join(label_of.get((b.race_id, leg), leg)
                               for leg in str(b.entrant_id).split("+"))
            click.echo(f"{b.race_id:<16}  {shown:<22}{b.odds_at_bet:>7.1f}{b.prob:>8.3f}{b.ev:>7.3f}"
                       f"{b.stake:>10,.0f}")
        click.echo("-" * 74)
        click.echo(f"{len(bets)} bets on {s['n_races_bet']}/{s['n_races']} races, "
                   f"{s['total_stake']:,.0f}円 at risk ({s['total_stake'] / bank:.2%} of bankroll, cap "
                   f"{max_daily_exposure:.0%}), avg EV {s['avg_ev']:.3f}")
        if skipped:
            click.echo(f"{skipped} further bets were dropped at the daily exposure cap.")
    click.echo(f"slip -> {slip_path}")
    click.echo(f"予想 -> {explained_path}")


@cli.command()
@click.option("--date", required=True)
@click.option("--provider", "provider_name", type=click.Choice(["csv", "demo", "http", "jravan"]), default="csv",
              show_default=True)
@click.option("--start_bankroll", type=float, default=DEFAULT_BANKROLL_YEN, show_default=True)
@with_common
def settle(date, provider_name, start_bankroll, processed, live_root, provider_config, ledger_path):
    """Close the day's open bets against the official results."""
    date = pd.Timestamp(date).normalize()
    try:
        prov = _provider(provider_name, processed, live_root, provider_config)
        results = prov.fetch_results(date)
    except ProviderError as exc:
        raise click.ClickException(str(exc))
    if results is None or results.empty:
        raise click.ClickException(f"provider '{provider_name}' has no results for {date:%Y-%m-%d} yet.")
    led = Ledger(ledger_path, start_bankroll)
    counts = led.settle(f"{date:%Y-%m-%d}", results)
    if not counts["settled"] and not counts["void"]:
        click.echo(f"nothing open on {date:%Y-%m-%d}")
        return
    s = led.summary()
    click.echo(f"{date:%Y-%m-%d}: settled {counts['settled']} ({counts['won']} won), voided {counts['void']}")
    click.echo(f"bankroll {s['bankroll']:,.0f}円  回収率 "
               f"{s['recovery_rate'] * 100:.1f}%" if s["recovery_rate"] else "")


@cli.command()
@click.option("--start_bankroll", type=float, default=DEFAULT_BANKROLL_YEN, show_default=True)
@click.option("--ledger", "ledger_path", default=LEDGER_PATH, show_default=True)
def status(start_bankroll, ledger_path):
    """Show the paper account."""
    s = Ledger(ledger_path, start_bankroll).summary()
    if not s["n_bets"]:
        click.echo("No paper bets yet. Start with: python src/live/paper_trader.py bet --provider demo")
        return
    click.echo(f"{s['first_bet']} .. {s['last_bet']}")
    click.echo(f"bankroll      {s['bankroll']:>14,.0f}円   (start {s['start_bankroll']:,.0f})")
    click.echo(f"available     {s['available']:>14,.0f}円   (at risk {s['at_risk']:,.0f})")
    click.echo(f"bets          {s['n_bets']:>14,}     settled {s['n_settled']}, open {s['n_open']}, void {s['n_void']}")
    if s["recovery_rate"] is not None:
        click.echo(f"回収率        {s['recovery_rate'] * 100:>13.1f}%   hit {s['hit_rate'] * 100:.1f}%")
        click.echo(f"損益          {s['total_profit']:>+14,.0f}円   on {s['total_staked']:,.0f} staked")


if __name__ == "__main__":
    cli()
