#!/usr/bin/env python3
"""X（旧Twitter）への自動投稿。

  python scripts/post_to_x.py --dry-run      # 投稿内容を表示するだけ
  python scripts/post_to_x.py                # 実際に投稿する

必要な環境変数（X Developer Portal で取得。Free プランでも投稿は可能）:
  X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET

いずれかが欠けている場合は自動的に dry-run になる。

※ フォロワーが少ないうちは、この投稿はほとんど届きません。
   立ち上げ期の優先度は低いです（docs/distribution.md 参照）。
"""

from __future__ import annotations

import argparse
import os
import sys

import social
from common import load_site, load_yaml

X_ENDPOINT = "https://api.x.com/2/tweets"
MAX_LEN = 280


def credentials() -> dict[str, str] | None:
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    values = {k: os.environ.get(k, "").strip() for k in keys}
    return values if all(values.values()) else None


def post(text: str, creds: dict[str, str]) -> dict:
    import requests
    from requests_oauthlib import OAuth1

    auth = OAuth1(creds["X_API_KEY"], creds["X_API_SECRET"],
                  creds["X_ACCESS_TOKEN"], creds["X_ACCESS_TOKEN_SECRET"])
    response = requests.post(X_ENDPOINT, json={"text": text}, auth=auth, timeout=30)
    if response.status_code != 201:
        raise RuntimeError(f"X API {response.status_code}: {response.text[:500]}")
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="投稿せずに文面だけ表示する")
    parser.add_argument("--count", type=int, default=None, help="投稿件数（既定は設定ファイルの値）")
    args = parser.parse_args()

    site = load_site()
    config = load_yaml("x_templates.yml", {}) or {}
    state = social.load_state("x")

    count = args.count if args.count is not None else int(config.get("posts_per_run", 1))
    candidates = social.build_candidates(site, config, "x")
    if not candidates:
        print("投稿できる対象がありません（products.yml と comparisons.yml が空です）")
        return

    targets = social.pick(candidates, state, config, count)
    if not targets:
        interval = config.get("min_repost_interval_days", 7)
        print(f"投稿対象なし: すべて直近{interval}日以内に投稿済みです。")
        return

    creds = credentials()
    dry = args.dry_run or creds is None
    if creds is None and not args.dry_run:
        print("X の認証情報が未設定のため dry-run で実行します。", file=sys.stderr)

    for target in targets:
        text = social.compose(target, config, MAX_LEN, social.weighted_length)
        print("─" * 50)
        print(text)
        print("─" * 50)
        print(f"({social.weighted_length(text)}/{MAX_LEN}文字, key={target['key']})")

        if dry:
            print("[dry-run] 投稿はしていません。")
            continue

        result = post(text, creds)
        tweet_id = (result.get("data") or {}).get("id", "")
        print(f"投稿しました: https://x.com/i/status/{tweet_id}")
        social.record(state, target["key"], text, tweet_id)
        social.save_state("x", state)


if __name__ == "__main__":
    main()
