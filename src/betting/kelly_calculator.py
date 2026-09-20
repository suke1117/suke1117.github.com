#!/usr/bin/env python
"""Phase 3: Fractional Kelly stake sizing.

Single-outcome Kelly (decimal odds ``o``, win probability ``p``, ``b = o - 1``):

    f* = (b p - q) / b = p - q / b,   q = 1 - p
    EV = p * o                        (1.0 = break-even)

The betting layer NEVER stakes the full Kelly fraction.  The stake is
``alpha * f*`` with ``alpha`` capped at ``MAX_KELLY_ALPHA`` (0.25) and further
limited by ``MAX_SINGLE_BET_FRACTION`` of the bankroll.  Bets are only placed
when ``EV >= ev_threshold``.

CLI:
    python src/betting/kelly_calculator.py --prob 0.15 --odds 10.0 --alpha 0.1
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402

from src.common.config import (  # noqa: E402
    BET_UNIT_YEN,
    DEFAULT_BANKROLL_YEN,
    DEFAULT_EV_THRESHOLD,
    DEFAULT_KELLY_ALPHA,
    MAX_KELLY_ALPHA,
    MAX_SINGLE_BET_FRACTION,
    MIN_KELLY_ALPHA,
)


class KellyAlphaError(ValueError):
    """Raised when someone tries to bet more aggressively than the hard limit."""


def validate_alpha(alpha: float) -> float:
    if not (MIN_KELLY_ALPHA <= alpha <= MAX_KELLY_ALPHA):
        raise KellyAlphaError(
            f"alpha={alpha} outside hard limit [{MIN_KELLY_ALPHA}, {MAX_KELLY_ALPHA}]; full Kelly is forbidden (CLAUDE.md rule 4)")
    return float(alpha)


def expected_value(prob: float, odds: float) -> float:
    """Expected gross return per 1 yen staked (1.0 = break-even)."""
    return prob * odds


def full_kelly_fraction(prob: float, odds: float) -> float:
    """Unconstrained Kelly fraction for a single win bet (can be negative -> no bet)."""
    if odds <= 1.0:
        return 0.0
    b = odds - 1.0
    return prob - (1.0 - prob) / b


@dataclass(frozen=True)
class KellyResult:
    prob: float
    odds: float
    ev: float
    full_kelly: float
    alpha: float
    fraction: float       # alpha * full_kelly, after caps
    stake_yen: float      # rounded down to BET_UNIT_YEN
    bet: bool
    reason: str


def kelly_stake(prob: float, odds: float, bankroll: float, alpha: float = DEFAULT_KELLY_ALPHA,
                ev_threshold: float = DEFAULT_EV_THRESHOLD, bet_unit: int = BET_UNIT_YEN,
                max_fraction: float = MAX_SINGLE_BET_FRACTION) -> KellyResult:
    alpha = validate_alpha(alpha)
    if not (0.0 <= prob <= 1.0):
        raise ValueError("prob must be in [0, 1]")
    if odds <= 1.0:
        return KellyResult(prob, odds, 0.0, 0.0, alpha, 0.0, 0.0, False, "odds<=1")
    ev = expected_value(prob, odds)
    fk = full_kelly_fraction(prob, odds)
    if ev < ev_threshold:
        return KellyResult(prob, odds, ev, fk, alpha, 0.0, 0.0, False, f"EV {ev:.3f} < threshold {ev_threshold}")
    if fk <= 0:
        return KellyResult(prob, odds, ev, fk, alpha, 0.0, 0.0, False, "non-positive Kelly fraction")
    frac = min(alpha * fk, max_fraction)
    stake = int(frac * bankroll // bet_unit) * bet_unit
    if stake < bet_unit:
        return KellyResult(prob, odds, ev, fk, alpha, frac, 0.0, False, "stake below minimum unit")
    return KellyResult(prob, odds, ev, fk, alpha, frac, float(stake), True, "ok")


@click.command()
@click.option("--prob", type=float, required=True, help="calibrated win probability")
@click.option("--odds", type=float, required=True, help="decimal win odds (e.g. 10.0 = 1000 yen return per 100)")
@click.option("--alpha", type=float, default=DEFAULT_KELLY_ALPHA, show_default=True, help="fraction of Kelly (max 0.25)")
@click.option("--bankroll", type=float, default=DEFAULT_BANKROLL_YEN, show_default=True)
@click.option("--ev_threshold", type=float, default=DEFAULT_EV_THRESHOLD, show_default=True)
def main(prob: float, odds: float, alpha: float, bankroll: float, ev_threshold: float) -> None:
    try:
        r = kelly_stake(prob, odds, bankroll, alpha, ev_threshold)
    except KellyAlphaError as exc:
        raise click.BadParameter(str(exc), param_hint="--alpha")
    click.echo(f"prob={r.prob:.4f} odds={r.odds:.2f} EV={r.ev:.4f}")
    click.echo(f"full Kelly f*={r.full_kelly:.4f}  alpha={r.alpha}  fractional f={r.fraction:.4f} (cap {MAX_SINGLE_BET_FRACTION})")
    click.echo(f"bankroll={bankroll:,.0f} yen -> stake={r.stake_yen:,.0f} yen  bet={r.bet} ({r.reason})")


if __name__ == "__main__":
    main()
