#!/usr/bin/env python3
"""Threads への自動投稿。

  python scripts/post_to_threads.py --dry-run   # 投稿内容を表示するだけ
  python scripts/post_to_threads.py             # 実際に投稿する

必要な環境変数（Meta for Developers で Threads アプリを作って取得）:
  THREADS_USER_ID       Threads のユーザーID（数値）
  THREADS_ACCESS_TOKEN  長期アクセストークン（60日で失効するので更新が必要）

いずれかが欠けている場合は自動的に dry-run になる。

Threads API は2段階で投稿する。
  1. メディアコンテナを作る（POST /{user-id}/threads）
  2. そのコンテナを公開する（POST /{user-id}/threads_publish）
公式ドキュメントでは、1と2の間に少し待つことが推奨されている。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import social
from common import load_site, load_yaml

API = "https://graph.threads.net/v1.0"
MAX_LEN = 500
# コンテナ作成から公開までの待ち時間（公式の推奨に合わせる）
PUBLISH_DELAY_SECONDS = 5


def credentials() -> dict[str, str] | None:
    values = {
        "user_id": os.environ.get("THREADS_USER_ID", "").strip(),
        "token": os.environ.get("THREADS_ACCESS_TOKEN", "").strip(),
    }
    return values if all(values.values()) else None


def post(text: str, creds: dict[str, str], delay: int = PUBLISH_DELAY_SECONDS) -> str:
    import requests

    create = requests.post(
        f"{API}/{creds['user_id']}/threads",
        data={"media_type": "TEXT", "text": text, "access_token": creds["token"]},
        timeout=30,
    )
    if create.status_code != 200:
        raise RuntimeError(f"Threads API (コンテナ作成) {create.status_code}: {create.text[:500]}")
    container_id = create.json().get("id")
    if not container_id:
        raise RuntimeError(f"コンテナIDが返りませんでした: {create.text[:500]}")

    time.sleep(delay)

    publish = requests.post(
        f"{API}/{creds['user_id']}/threads_publish",
        data={"creation_id": container_id, "access_token": creds["token"]},
        timeout=30,
    )
    if publish.status_code != 200:
        raise RuntimeError(f"Threads API (公開) {publish.status_code}: {publish.text[:500]}")
    return publish.json().get("id", "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="投稿せずに文面だけ表示する")
    parser.add_argument("--count", type=int, default=None, help="投稿件数（既定は設定ファイルの値）")
    args = parser.parse_args()

    site = load_site()
    config = load_yaml("threads_templates.yml", {}) or {}
    state = social.load_state("threads")

    count = args.count if args.count is not None else int(config.get("posts_per_run", 1))
    candidates = social.build_candidates(site, config, "threads")
    if not candidates:
        print("投稿できる対象がありません（products.yml と comparisons.yml が空です）")
        return

    targets = social.pick(candidates, state, config, count)
    if not targets:
        interval = config.get("min_repost_interval_days", 10)
        print(f"投稿対象なし: すべて直近{interval}日以内に投稿済みです。")
        return

    creds = credentials()
    dry = args.dry_run or creds is None
    if creds is None and not args.dry_run:
        print("Threads の認証情報が未設定のため dry-run で実行します。", file=sys.stderr)

    for target in targets:
        text = social.compose(target, config, MAX_LEN, social.plain_length)
        print("─" * 50)
        print(text)
        print("─" * 50)
        print(f"({social.plain_length(text)}/{MAX_LEN}文字, key={target['key']})")

        if dry:
            print("[dry-run] 投稿はしていません。")
            continue

        post_id = post(text, creds)
        print(f"投稿しました: id={post_id}")
        social.record(state, target["key"], text, post_id)
        social.save_state("threads", state)


if __name__ == "__main__":
    main()
