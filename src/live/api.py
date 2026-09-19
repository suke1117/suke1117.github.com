#!/usr/bin/env python
"""Local HTTP API, and the dashboard served from the same origin.

    python src/live/api.py            # http://127.0.0.1:8787

Serving the page and the JSON from one origin is what makes the dashboard
live: the page fetches ``/api/...`` with no cross-origin problem and no
configuration.  The published Artifact is the same page with a static snapshot
baked in, so it keeps working without this server - it just cannot show
anything newer than the build.

Endpoints
    GET /api/health              versions and what data is present
    GET /api/slips               race days that have a priced slip
    GET /api/today[?date=]       the slip: every runner with probability, odds, EV, stake
    GET /api/ledger[?limit=]     paper bets, newest first
    GET /api/summary             bankroll, recovery rate, per-day history
    GET /api/config              the policy in force

Binds to 127.0.0.1 by default. This server has no authentication, so do not
put it on a public interface; ``--host`` exists for containers, not for the
open internet.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import pandas as pd  # noqa: E402

from src.common.config import DEFAULT_BANKROLL_YEN  # noqa: E402
from src.common.logging_utils import get_logger  # noqa: E402
from src.live.ledger import Ledger  # noqa: E402

log = get_logger("api")

STATIC: Dict[str, Tuple[str, str]] = {
    "/": ("web/index.html", "text/html; charset=utf-8"),
    "/index.html": ("web/index.html", "text/html; charset=utf-8"),
    "/data.json": ("web/data.json", "application/json; charset=utf-8"),
}


class Config:
    def __init__(self, root: Path, ledger_path: str, slip_dir: str, start_bankroll: float):
        self.root = root
        self.ledger_path = root / ledger_path
        self.slip_dir = root / slip_dir
        self.start_bankroll = start_bankroll

    def ledger(self) -> Ledger:
        return Ledger(self.ledger_path, self.start_bankroll)

    def slip_dates(self) -> list:
        if not self.slip_dir.exists():
            return []
        return sorted(p.stem for p in self.slip_dir.glob("*.json"))

    def slip(self, date: Optional[str]) -> Optional[Dict]:
        dates = self.slip_dates()
        if not dates:
            return None
        pick = date or dates[-1]
        path = self.slip_dir / f"{pick}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "EquineAlpha"
    cfg: Config = None  # set by serve()

    def log_message(self, fmt: str, *args) -> None:  # quieter than the default
        log.info("%s %s", self.address_string(), fmt % args)

    # -- helpers -----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, message: str, hint: str = "") -> None:
        self._json({"error": message, "hint": hint}, code)

    # -- routes ------------------------------------------------------------
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        url = urlparse(self.path)
        q = parse_qs(url.query)
        route = url.path.rstrip("/") or "/"
        try:
            if route in STATIC:
                rel, ctype = STATIC[route]
                path = self.cfg.root / rel
                if not path.exists():
                    return self._error(404, f"{rel} has not been built",
                                       "run: python src/web/export_dashboard.py --output web/data.json")
                return self._send(200, path.read_bytes(), ctype)
            if route == "/api/health":
                return self._json({
                    "ok": True,
                    "time": pd.Timestamp.now("UTC").isoformat(),
                    "ledger": str(self.cfg.ledger_path),
                    "ledger_exists": self.cfg.ledger_path.exists(),
                    "slips": len(self.cfg.slip_dates()),
                    "dashboard_data": (self.cfg.root / "web/data.json").exists(),
                })
            if route == "/api/slips":
                return self._json({"dates": self.cfg.slip_dates()})
            if route == "/api/today":
                slip = self.cfg.slip(q.get("date", [None])[0])
                if slip is None:
                    return self._error(404, "no slip for that date",
                                       "run: python src/live/paper_trader.py bet --provider demo")
                return self._json(slip)
            if route == "/api/ledger":
                rows = self.cfg.ledger().current()[::-1]
                limit = int(q.get("limit", ["200"])[0])
                return self._json({"count": len(rows), "bets": rows[:max(0, limit)]})
            if route == "/api/summary":
                led = self.cfg.ledger()
                return self._json({"summary": led.summary(), "daily": led.daily()})
            if route == "/api/config":
                slip = self.cfg.slip(None) or {}
                return self._json({"policy": slip.get("policy"), "provider": slip.get("provider"),
                                   "start_bankroll": self.cfg.start_bankroll})
            return self._error(404, f"no route {route}", "see /api/health")
        except Exception as exc:  # keep the server up; a bad request is not a crash
            log.exception("request failed: %s", route)
            return self._error(500, str(exc), "check the server log")


@click.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="keep this on loopback; there is no auth")
@click.option("--port", default=8787, show_default=True, type=int)
@click.option("--root", default=".", show_default=True, help="repository root")
@click.option("--ledger", "ledger_path", default="live_data/ledger.jsonl", show_default=True)
@click.option("--slips", "slip_dir", default="live_data/slips", show_default=True)
@click.option("--start_bankroll", type=float, default=DEFAULT_BANKROLL_YEN, show_default=True)
def main(host, port, root, ledger_path, slip_dir, start_bankroll):
    Handler.cfg = Config(Path(root).resolve(), ledger_path, slip_dir, start_bankroll)
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("binding to %s: this server has no authentication", host)
    srv = ThreadingHTTPServer((host, port), Handler)
    click.echo(f"dashboard  http://{host}:{port}/")
    click.echo(f"api        http://{host}:{port}/api/health")
    click.echo("ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nstopped")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
