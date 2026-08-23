"""設定ファイルの読み込みと、Amazonリンク生成の共通処理。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_yaml(name: str, default=None):
    path = DATA / name
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or default


def load_site() -> dict:
    site = load_yaml("site.yml", {}) or {}
    # 環境変数で上書きできるようにしておく（Actions から差し込める）
    tag = os.environ.get("AMAZON_ASSOCIATE_TAG")
    if tag:
        site.setdefault("amazon", {})["associate_tag"] = tag
    base = os.environ.get("SITE_BASE_URL")
    if base:
        site["base_url"] = base.rstrip("/")
    # ローカルプレビュー用。base_path があるとサブディレクトリ配信前提のパスになり、
    # 手元の http.server では CSS などが 404 になるため空にできるようにしておく
    base_path = os.environ.get("SITE_BASE_PATH")
    if base_path is not None:
        site["base_path"] = base_path.rstrip("/")
    return site


def site_root(site: dict) -> str:
    """記事URLの先頭に付く 'https://example.com/path' 部分（末尾スラッシュなし）。"""
    return site.get("base_url", "").rstrip("/") + site.get("base_path", "").rstrip("/")


def url_for(site: dict, path: str) -> str:
    """サイト内絶対URLを返す。path は '/items/foo/' のようにスラッシュ始まり。"""
    return site_root(site) + path


def amazon_url(asin: str, site: dict) -> str:
    """アフィリエイトリンクを組み立てる。

    associate_tag が空のときは素のURLを返す。
    （アソシエイト審査前にタグ付きリンクを公開すると規約違反になり得るため）
    """
    amazon = site.get("amazon") or {}
    market = amazon.get("marketplace", "www.amazon.co.jp")
    tag = (amazon.get("associate_tag") or "").strip()
    url = f"https://{market}/dp/{asin}"
    if tag:
        url += f"?tag={quote(tag)}&linkCode=ll1&language=ja_JP"
    return url


def amazon_search_url(keyword: str, site: dict) -> str:
    amazon = site.get("amazon") or {}
    market = amazon.get("marketplace", "www.amazon.co.jp")
    tag = (amazon.get("associate_tag") or "").strip()
    url = f"https://{market}/s?k={quote(keyword)}"
    if tag:
        url += f"&tag={quote(tag)}"
    return url


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\-ぁ-んァ-ヶ一-龠]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def has_associate_tag(site: dict) -> bool:
    return bool(((site.get("amazon") or {}).get("associate_tag") or "").strip())
