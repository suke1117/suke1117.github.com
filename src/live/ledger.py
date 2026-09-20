"""Append-only ledger of paper bets.

Append-only on purpose: a betting record you can edit is a betting record you
will edit after a loss.  Every line is one bet; settlement rewrites nothing,
it appends the settled version, and the current state is the last line per
bet id.  That keeps the full history of what was decided *before* the result
was known, which is the only version worth grading.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from src.common.config import DEFAULT_BANKROLL_YEN

OPEN, SETTLED, VOID = "open", "settled", "void"


@dataclass
class PaperBet:
    bet_id: str
    placed_at: str
    race_date: str
    race_id: str
    entrant_id: str
    bet_type: str
    prob: float
    odds_at_bet: float
    ev: float
    fraction: float
    stake: float
    status: str = OPEN
    finish_position: Optional[int] = None
    payout_odds: Optional[float] = None
    profit: Optional[float] = None
    settled_at: Optional[str] = None
    note: str = ""

    @staticmethod
    def new(**kw) -> "PaperBet":
        return PaperBet(bet_id=uuid.uuid4().hex[:12], placed_at=pd.Timestamp.now("UTC").isoformat(), **kw)


@dataclass
class Ledger:
    path: Path
    start_bankroll: float = DEFAULT_BANKROLL_YEN
    _rows: List[Dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rows = self._read()

    def _read(self) -> List[Dict]:
        if not self.path.exists():
            return []
        rows = []
        for i, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{self.path}:{i} is not valid JSON: {exc}") from exc
        return rows

    def _append(self, rows: Iterable[Dict]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                self._rows.append(r)

    # -- state -------------------------------------------------------------
    def current(self) -> List[Dict]:
        """Latest version of every bet, in the order they were first placed."""
        latest: Dict[str, Dict] = {}
        order: List[str] = []
        for r in self._rows:
            if r["bet_id"] not in latest:
                order.append(r["bet_id"])
            latest[r["bet_id"]] = r
        return [latest[b] for b in order]

    def has_date(self, race_date: str) -> bool:
        return any(r["race_date"] == race_date for r in self._rows)

    def bankroll(self) -> float:
        """Cash after settled results. Open stakes are already committed."""
        settled = sum(r.get("profit") or 0.0 for r in self.current() if r["status"] == SETTLED)
        return self.start_bankroll + settled

    def available(self) -> float:
        """Bankroll minus money riding on races that have not been settled."""
        at_risk = sum(r["stake"] for r in self.current() if r["status"] == OPEN)
        return self.bankroll() - at_risk

    # -- writes ------------------------------------------------------------
    def place(self, bets: List[PaperBet]) -> None:
        self._append(asdict(b) for b in bets)

    def settle(self, race_date: str, results: pd.DataFrame, odds_haircut: float = 0.0) -> Dict[str, int]:
        """Close every open bet on ``race_date`` using ``results``.

        A runner missing from the results is voided rather than counted as a
        loss: an unknown outcome is not a losing one.
        """
        lookup = {(str(r.race_id), str(r.entrant_id)): int(r.finish_position) for r in results.itertuples()}
        out, counts = [], {"settled": 0, "void": 0, "won": 0}
        now = pd.Timestamp.now("UTC").isoformat()
        for r in self.current():
            if r["status"] != OPEN or r["race_date"] != race_date:
                continue
            key = (r["race_id"], r["entrant_id"])
            row = dict(r)
            row["settled_at"] = now
            if key not in lookup:
                row["status"] = VOID
                row["profit"] = 0.0
                row["note"] = "no result for this runner; stake returned"
                counts["void"] += 1
            else:
                pos = lookup[key]
                payout = 1.0 + (r["odds_at_bet"] - 1.0) * (1.0 - odds_haircut)
                won = pos == 1
                row["status"] = SETTLED
                row["finish_position"] = pos
                row["payout_odds"] = round(payout, 3)
                row["profit"] = round(r["stake"] * (payout - 1.0), 1) if won else -r["stake"]
                counts["settled"] += 1
                counts["won"] += int(won)
            out.append(row)
        self._append(out)
        return counts

    # -- reporting ---------------------------------------------------------
    def summary(self) -> Dict:
        cur = self.current()
        settled = [r for r in cur if r["status"] == SETTLED]
        open_ = [r for r in cur if r["status"] == OPEN]
        staked = sum(r["stake"] for r in settled)
        profit = sum(r.get("profit") or 0.0 for r in settled)
        return {
            "start_bankroll": self.start_bankroll,
            "bankroll": self.bankroll(),
            "available": self.available(),
            "n_bets": len(cur),
            "n_settled": len(settled),
            "n_open": len(open_),
            "n_void": sum(1 for r in cur if r["status"] == VOID),
            "at_risk": sum(r["stake"] for r in open_),
            "total_staked": staked,
            "total_profit": profit,
            "recovery_rate": (staked + profit) / staked if staked else None,
            "hit_rate": (sum(1 for r in settled if (r.get("profit") or 0) > 0) / len(settled)) if settled else None,
            "first_bet": min((r["race_date"] for r in cur), default=None),
            "last_bet": max((r["race_date"] for r in cur), default=None),
        }

    def daily(self) -> List[Dict]:
        """Settled profit per race day, oldest first, with a running bankroll."""
        by_day: Dict[str, Dict[str, float]] = {}
        for r in self.current():
            if r["status"] != SETTLED:
                continue
            d = by_day.setdefault(r["race_date"], {"staked": 0.0, "profit": 0.0, "n": 0, "won": 0})
            d["staked"] += r["stake"]
            d["profit"] += r.get("profit") or 0.0
            d["n"] += 1
            d["won"] += int((r.get("profit") or 0) > 0)
        rows, bank = [], self.start_bankroll
        for day in sorted(by_day):
            v = by_day[day]
            bank += v["profit"]
            rows.append({"date": day, "n_bets": int(v["n"]), "n_won": int(v["won"]), "staked": v["staked"],
                         "profit": v["profit"],
                         "recovery": (v["staked"] + v["profit"]) / v["staked"] if v["staked"] else None,
                         "bankroll": bank})
        return rows
