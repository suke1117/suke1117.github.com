#!/usr/bin/env python3
"""生成後のサイトを走査して、内部リンクの切れを見つける。

  python scripts/build_site.py --local
  python scripts/check_internal_links.py

カテゴリ名を変えたのに参照が古いまま、slug を直したのにリンクが残っている、
といった壊れ方は validate.py では見つからない（YAMLとしては正しいため）。
生成物側で確認する。

外部リンク（Amazon など）は対象外。そちらは check_links.py が見る。
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin

from common import ROOT, load_site


class LinkFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in ("a", "link", "img", "script"):
            return
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.links.append(value)


def resolve(out: Path, page: Path, href: str, base_path: str) -> Path | None:
    """リンク先を public/ 以下の実ファイルに解決する。外部リンクは None。

    公開用ビルドでは href の先頭にサブディレクトリ（base_path）が付くので、
    それを取り除いてから public/ 以下を探す。
    """
    if href.startswith(("http://", "https://", "mailto:", "#", "data:")):
        return None
    href = unquote(href.split("#")[0].split("?")[0])
    if not href:
        return None

    if href.startswith("/"):
        if base_path and (href == base_path or href.startswith(base_path + "/")):
            href = href[len(base_path):] or "/"
        target = out / href.lstrip("/")
    else:
        # ページの位置からの相対パス。/a/b/index.html は /a/b/ を基準にする
        base = page.parent
        target = (base / href).resolve()

    if target.is_dir() or href.endswith("/"):
        target = target / "index.html"
    return target


def main() -> None:
    out = ROOT / "public"
    if not out.exists():
        print("public/ がありません。先に build_site.py を実行してください。", file=sys.stderr)
        sys.exit(1)

    base_path = (load_site().get("base_path") or "").rstrip("/")
    pages = sorted(out.rglob("*.html"))
    broken: list[tuple[str, str]] = []
    checked = 0

    for page in pages:
        finder = LinkFinder()
        finder.feed(page.read_text(encoding="utf-8"))
        for href in finder.links:
            target = resolve(out, page, href, base_path)
            if target is None:
                continue
            checked += 1
            if not target.exists():
                broken.append((str(page.relative_to(out)), href))

    print(f"{len(pages)}ページ / 内部リンク {checked}本を確認")
    if broken:
        print(f"\nリンク切れ {len(broken)}件:")
        for page, href in broken:
            print(f"  {page} -> {href}")
        sys.exit(1)
    print("リンク切れなし")


if __name__ == "__main__":
    main()
