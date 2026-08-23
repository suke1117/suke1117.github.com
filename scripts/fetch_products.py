#!/usr/bin/env python3
"""PA-API から商品情報を取得して data/amazon_cache.json に保存する。

  python scripts/fetch_products.py --refresh              # products.yml の全ASINを更新
  python scripts/fetch_products.py --asin B0XXXXXXXX ...  # 指定ASINだけ更新
  python scripts/fetch_products.py --search "ワイヤレスイヤホン" --limit 5

products.yml は書き換えない（手書きのレビュー文を壊さないため）。
キャッシュにある title / image / price は、products.yml で未記入の項目だけ
ビルド時に自動で使われる。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pa_api
from common import DATA, load_yaml

CACHE_PATH = DATA / "amazon_cache.json"


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="products.yml の全ASINを更新")
    parser.add_argument("--asin", nargs="*", default=[], help="更新するASIN")
    parser.add_argument("--search", help="キーワード検索して結果を表示する")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    try:
        if args.search:
            for item in pa_api.search_items(args.search, args.limit):
                print(f"{item['asin']}  {item['price_display'] or '-':>10}  {item['title'][:60]}")
            return

        asins = list(args.asin)
        if args.refresh:
            asins += [p["asin"] for p in (load_yaml("products.yml", []) or []) if p.get("asin")]
        asins = sorted(set(a for a in asins if a))
        if not asins:
            parser.error("--refresh か --asin か --search のいずれかを指定してください")

        fetched = pa_api.get_items(asins)
    except pa_api.PaApiError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    cache = load_cache()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    for asin, data in fetched.items():
        cache[asin] = {**data, "fetched_at": stamp}
    save_cache(cache)

    missing = [a for a in asins if a not in fetched]
    print(f"{len(fetched)}件を更新しました -> {CACHE_PATH.relative_to(DATA.parent)}")
    if missing:
        print(f"取得できなかったASIN: {', '.join(missing)}")


if __name__ == "__main__":
    main()
