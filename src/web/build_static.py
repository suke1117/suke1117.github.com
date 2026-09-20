#!/usr/bin/env python
"""Inline web/data.json into web/index.html to produce a standalone page.

The repo page fetches ``./data.json`` so it works as a normal static site
(GitHub Pages).  Publishing surfaces that serve a single HTML file need the
data embedded, which is what this produces.

    python src/web/build_static.py --output web/dist/index.html
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402

from src.common.logging_utils import get_logger  # noqa: E402

log = get_logger("build")
MARKER = "<script>\n(function () {"


@click.command()
@click.option("--page", default="web/index.html", show_default=True)
@click.option("--data", "data_path", default="web/data.json", show_default=True)
@click.option("--output", default="web/dist/index.html", show_default=True)
def main(page: str, data_path: str, output: str) -> None:
    html = Path(page).read_text(encoding="utf-8")
    data = Path(data_path).read_text(encoding="utf-8")
    if "</script>" in data:
        raise click.ClickException("data.json contains '</script>' and cannot be inlined safely")
    if MARKER not in html:
        raise click.ClickException("could not find the boot script in the page; did its markup change?")
    block = f'<script id="dashboard-data" type="application/json">{data}</script>\n' + MARKER
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html.replace(MARKER, block, 1), encoding="utf-8")
    log.info("wrote %s (%.1f KB, data inlined)", out, out.stat().st_size / 1024)


if __name__ == "__main__":
    main()
