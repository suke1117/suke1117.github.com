#!/usr/bin/env python3
"""商品リンクの生存確認。廃番になった商品を見つける。

  python scripts/check_links.py

アマゾンアフィリエイト特有の落とし穴で、商品が廃番になるとリンク先が消える。
読者は「この記事は古い」と判断して離れるので、月1回は確認する。

PA-API のキーがあればそちらで確認する（確実で、ブロックされない）。
無い場合は商品ページへのHTTPリクエストで確認する
（アマゾン側にボットとして弾かれることがあるため、判定できない場合は「不明」を返す）。
"""

from __future__ import annotations

import sys
import time

import requests

from common import amazon_url, load_site, load_yaml

UA = "Mozilla/5.0 (compatible; link-check/1.0)"


def check_with_pa_api(asins: list[str]) -> dict[str, str] | None:
    try:
        import pa_api
        found = pa_api.get_items(asins)
    except Exception as e:  # キーが無い、レート制限など
        print(f"PA-APIでの確認をスキップします（{e}）", file=sys.stderr)
        return None
    return {a: ("生存" if a in found else "取得できず") for a in asins}


def check_with_http(asins: list[str], site: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for asin in asins:
        url = amazon_url(asin, site)
        try:
            response = requests.get(
                url, headers={"User-Agent": UA}, timeout=20, allow_redirects=True
            )
            if response.status_code == 404:
                result[asin] = "404（廃番の可能性）"
            elif response.status_code in (503, 403, 429):
                result[asin] = "不明（アマゾン側にブロックされました）"
            elif "/dp/" not in response.url and "/gp/product/" not in response.url:
                result[asin] = f"別ページに転送されました -> {response.url[:80]}"
            else:
                result[asin] = "生存"
        except requests.RequestException as e:
            result[asin] = f"不明（{type(e).__name__}）"
        time.sleep(1.5)   # 連続アクセスを避ける
    return result


def main() -> None:
    site = load_site()
    products = load_yaml("products.yml", []) or []
    asins = [p["asin"] for p in products if p.get("asin")]
    by_asin = {p["asin"]: p for p in products if p.get("asin")}
    if not asins:
        print("確認するASINがありません")
        return

    result = check_with_pa_api(asins) or check_with_http(asins, site)

    problems = []
    for asin, status in result.items():
        slug = by_asin[asin].get("slug", "?")
        line = f"{status:<40} {asin}  {slug}"
        print(line)
        if status != "生存" and not status.startswith("不明"):
            problems.append(line)

    unknown = sum(1 for s in result.values() if s.startswith("不明"))
    print(f"\n確認 {len(result)}件 / 要対応 {len(problems)}件 / 判定できず {unknown}件")

    if problems:
        print("\n要対応の商品:")
        for line in problems:
            print(f"  {line}")
        sys.exit(1)


if __name__ == "__main__":
    main()
