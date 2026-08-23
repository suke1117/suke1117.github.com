#!/usr/bin/env python3
"""X（旧Twitter）への自動投稿。

  python scripts/post_to_x.py --dry-run      # 投稿内容を表示するだけ
  python scripts/post_to_x.py                # 実際に投稿する

必要な環境変数（X Developer Portal で取得。Free プランでも投稿は可能）:
  X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET

いずれかが欠けている場合は自動的に dry-run になる。

投稿対象の選び方:
  data/state/x_posted.json の履歴を見て、
  「min_repost_interval_days 以内に投稿していないもの」の中から
  最後に投稿した時刻が最も古いものを選ぶ。文面テンプレートも順番に回す。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

from common import DATA, ROOT, amazon_url, has_associate_tag, load_site, load_yaml, url_for

STATE_PATH = DATA / "state" / "x_posted.json"
X_ENDPOINT = "https://api.x.com/2/tweets"
MAX_LEN = 280
# t.co により、URLは実際の長さに関係なく一律この文字数として計算される
TCO_LEN = 23


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"history": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# X の重み付き文字数：ラテン文字などは1、日本語などは2として数える
LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
URL_RE = re.compile(r"https?://\S+")


def tweet_length(text: str) -> int:
    """X の数え方に合わせた文字数。

    ・URL は実際の長さに関係なく t.co の固定長（23）として数える
    ・日本語などの全角文字は 2 文字として数える（つまり日本語のみなら実質140字）
    """
    total = URL_RE.subn("", text)[1] * TCO_LEN
    for char in URL_RE.sub("", text):
        code = ord(char)
        total += 1 if any(lo <= code <= hi for lo, hi in LIGHT_RANGES) else 2
    return total


def build_candidates(site: dict, config: dict) -> list[dict]:
    """商品とまとめ記事を、投稿候補の共通形式に整える。"""
    products = load_yaml("products.yml", []) or []
    comparisons = load_yaml("comparisons.yml", []) or []
    link_target = config.get("link_target", "site")
    candidates: list[dict] = []

    for p in products:
        if p.get("no_post"):
            continue
        site_url = url_for(site, f"/items/{p['slug']}/")
        if link_target == "amazon" and has_associate_tag(site) and p.get("asin"):
            link = amazon_url(p["asin"], site)
        else:
            link = site_url
        candidates.append({
            "key": f"item:{p['slug']}",
            "kind": "item",
            "url": link,
            "vars": {
                "title": p.get("title", ""),
                "summary": p.get("summary", ""),
                "best_for": p.get("best_for", ""),
                "price_range": p.get("price_range", ""),
                "category": p.get("category", ""),
                "pros1": (p.get("pros") or [""])[0],
                "url": link,
            },
        })

    for c in comparisons:
        if c.get("no_post"):
            continue
        link = url_for(site, f"/compare/{c['slug']}/")
        scene1 = (c.get("scene") or "").strip().splitlines()
        criteria = c.get("criteria") or []
        candidates.append({
            "key": f"compare:{c['slug']}",
            "kind": "comparison",
            "url": link,
            "vars": {
                "title": c.get("title", ""),
                "keyword": c.get("keyword", ""),
                "scene1": scene1[0] if scene1 else "",
                "criteria1": criteria[0].get("name", "") if criteria else "",
                "not_for1": (c.get("not_for") or [""])[0],
                "count": len(c.get("items", [])),
                "url": link,
            },
        })

    return candidates


def pick(candidates: list[dict], state: dict, config: dict, count: int) -> list[dict]:
    """最近投稿していないものを、古い順に count 件選ぶ。"""
    interval = dt.timedelta(days=int(config.get("min_repost_interval_days", 7)))
    now = dt.datetime.now(dt.timezone.utc)

    last_posted: dict[str, dt.datetime] = {}
    post_count: dict[str, int] = {}
    for entry in state.get("history", []):
        ts = dt.datetime.fromisoformat(entry["posted_at"])
        key = entry["key"]
        if key not in last_posted or ts > last_posted[key]:
            last_posted[key] = ts
        post_count[key] = post_count.get(key, 0) + 1

    eligible = [
        c for c in candidates
        if c["key"] not in last_posted or now - last_posted[c["key"]] >= interval
    ]
    # 未投稿どうしが並んだときは比較記事を先に出す（収益の主力がこちらのため）
    never = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    eligible.sort(key=lambda c: (last_posted.get(c["key"], never), 0 if c["kind"] == "comparison" else 1))
    for c in eligible:
        c["post_count"] = post_count.get(c["key"], 0)
    return eligible[:count]


def compose(candidate: dict, config: dict) -> str:
    """テンプレートを1つ選んで文面を作る。同じ商品には毎回違うテンプレートが当たる。"""
    key = "comparison_templates" if candidate["kind"] == "comparison" else "templates"
    templates = config.get(key) or config.get("templates") or ["{title}\n{url}"]
    template = templates[candidate.get("post_count", 0) % len(templates)]

    variables = dict(candidate["vars"])
    variables["ad_label"] = config.get("ad_label", "#PR")
    text = template.format(**variables)

    # 空欄の変数が残した空行を畳む
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line and (not cleaned or not cleaned[-1]):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned).strip()

    if tweet_length(text) > MAX_LEN:
        text = trim(text, config)
    return text


def trim(text: str, config: dict) -> str:
    """長すぎる場合、URLと広告表示を残したまま本文を削る。"""
    lines = text.splitlines()
    tail = [ln for ln in lines if "http" in ln]
    body = [ln for ln in lines if "http" not in ln]
    while body and tweet_length("\n".join(body + tail)) > MAX_LEN:
        longest = max(range(len(body)), key=lambda i: len(body[i]))
        if len(body[longest]) <= 12:
            body.pop(longest)
        else:
            body[longest] = body[longest][: len(body[longest]) - 8].rstrip() + "…"
    return "\n".join(body + tail).strip()


def credentials() -> dict[str, str] | None:
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    values = {k: os.environ.get(k, "").strip() for k in keys}
    if all(values.values()):
        return values
    return None


def post(text: str, creds: dict[str, str]) -> dict:
    import requests
    from requests_oauthlib import OAuth1

    auth = OAuth1(
        creds["X_API_KEY"],
        creds["X_API_SECRET"],
        creds["X_ACCESS_TOKEN"],
        creds["X_ACCESS_TOKEN_SECRET"],
    )
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
    state = load_state()

    count = args.count if args.count is not None else int(config.get("posts_per_run", 1))
    candidates = build_candidates(site, config)
    if not candidates:
        print("投稿できる商品がありません（products.yml が空です）")
        return

    targets = pick(candidates, state, config, count)
    if not targets:
        interval = config.get("min_repost_interval_days", 7)
        print(f"投稿対象なし: すべての商品が直近{interval}日以内に投稿済みです。")
        print("商品を追加するか、min_repost_interval_days を短くしてください。")
        return

    creds = credentials()
    dry = args.dry_run or creds is None
    if creds is None and not args.dry_run:
        print("X の認証情報が未設定のため dry-run で実行します。", file=sys.stderr)

    for target in targets:
        text = compose(target, config)
        print("─" * 50)
        print(text)
        print("─" * 50)
        print(f"({tweet_length(text)}/{MAX_LEN}文字, key={target['key']})")

        if dry:
            print("[dry-run] 投稿はしていません。")
            continue

        result = post(text, creds)
        tweet_id = (result.get("data") or {}).get("id", "")
        print(f"投稿しました: https://x.com/i/status/{tweet_id}")
        state.setdefault("history", []).append({
            "key": target["key"],
            "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "tweet_id": tweet_id,
            "text": text,
        })
        # 履歴は直近500件だけ残す
        state["history"] = state["history"][-500:]
        save_state(state)


if __name__ == "__main__":
    main()
