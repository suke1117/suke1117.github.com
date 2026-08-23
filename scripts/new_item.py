#!/usr/bin/env python3
"""products.yml に商品の雛形を追記する。

  python scripts/new_item.py --asin B0XXXXXXXX --slug my-item --title "商品名" --category "ガジェット"

PA-API のキーがあれば --asin だけで title / brand が埋まる。
レビュー本文は雛形のまま残るので、あとから手で書く。
"""

from __future__ import annotations

import argparse
import sys

from common import DATA, load_yaml, slugify

TEMPLATE = """
- slug: "{slug}"
  asin: "{asin}"
  title: "{title}"
  brand: "{brand}"
  category: "{category}"
  tags: []
  price_range: ""
  rating:
  image: ""
  summary: "（1行で。どんな人の何を解決するか）"
  pros:
    - "（良かった点）"
  cons:
    - "（気になった点。必ず1つは書く）"
  best_for: "（向いている人）"
  review: |
    （本文。実際に使った感想を書く）
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asin", required=True)
    parser.add_argument("--slug")
    parser.add_argument("--title", default="")
    parser.add_argument("--brand", default="")
    parser.add_argument("--category", default="その他")
    args = parser.parse_args()

    title, brand = args.title, args.brand
    if not title:
        try:
            import pa_api
            fetched = pa_api.get_items([args.asin]).get(args.asin)
            if fetched:
                title = fetched["title"]
                brand = brand or fetched["brand"]
                print(f"PA-APIから取得: {title}")
        except Exception as e:  # キーが無い場合など。手入力にフォールバックする
            print(f"PA-APIからの取得をスキップします（{e}）", file=sys.stderr)

    if not title:
        parser.error("--title を指定するか、PA-API のキーを設定してください")

    slug = args.slug or slugify(title)
    existing = {p.get("slug") for p in (load_yaml("products.yml", []) or [])}
    if slug in existing:
        parser.error(f"slug が既に存在します: {slug}")

    entry = TEMPLATE.format(
        slug=slug, asin=args.asin, title=title.replace('"', "'"),
        brand=brand, category=args.category,
    )
    path = DATA / "products.yml"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
    print(f"追記しました: {path.name} -> {slug}")
    print(f"編集後、python scripts/build_site.py でプレビューを確認してください。")


if __name__ == "__main__":
    main()
