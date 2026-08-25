"""SNS投稿の共通処理。X と Threads で同じ仕組みを使う。

投稿の選び方:
  data/state/<platform>_posted.json の履歴を見て、
  「min_repost_interval_days 以内に投稿していないもの」の中から
  最後に投稿した時刻が最も古いものを選ぶ。文面テンプレートも順番に回す。

リンク先は自サイトに統一している（data/*_templates.yml の link_target）。
理由は docs/distribution.md を参照。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from urllib.parse import urlencode

from common import DATA, amazon_url, has_associate_tag, load_yaml, url_for

# X の重み付き文字数：ラテン文字などは1、日本語などは2として数える
LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
URL_RE = re.compile(r"https?://\S+")
TCO_LEN = 23


def state_path(platform: str):
    return DATA / "state" / f"{platform}_posted.json"


def load_state(platform: str) -> dict:
    path = state_path(platform)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"history": []}


def save_state(platform: str, state: dict) -> None:
    path = state_path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def weighted_length(text: str) -> int:
    """X の数え方に合わせた文字数。

    ・URL は実際の長さに関係なく t.co の固定長（23）として数える
    ・日本語などの全角文字は 2 文字として数える（つまり日本語のみなら実質140字）
    """
    total = URL_RE.subn("", text)[1] * TCO_LEN
    for char in URL_RE.sub("", text):
        code = ord(char)
        total += 1 if any(lo <= code <= hi for lo, hi in LIGHT_RANGES) else 2
    return total


def plain_length(text: str) -> int:
    """Threads など、単純な文字数で数える場合。"""
    return len(text)


def add_utm(url: str, site: dict, source: str, campaign: str) -> str:
    """流入元を計測できるようにする。site.yml の utm: false で無効化できる。"""
    if not site.get("utm", True):
        return url
    query = urlencode({
        "utm_source": source,
        "utm_medium": "social",
        "utm_campaign": campaign,
    })
    return url + ("&" if "?" in url else "?") + query


def build_candidates(site: dict, config: dict, source: str) -> list[dict]:
    """商品と比較記事を、投稿候補の共通形式に整える。"""
    products = load_yaml("products.yml", []) or []
    comparisons = load_yaml("comparisons.yml", []) or []
    link_target = config.get("link_target", "site")
    candidates: list[dict] = []

    for c in comparisons:
        if c.get("no_post"):
            continue
        link = add_utm(url_for(site, f"/compare/{c['slug']}/"), site, source, c["slug"])
        scene1 = (c.get("scene") or "").strip().splitlines()
        criteria = c.get("criteria") or []
        complaints = c.get("complaints") or []
        candidates.append({
            "key": f"compare:{c['slug']}",
            "kind": "comparison",
            "url": link,
            "vars": {
                "title": c.get("title", ""),
                "short_title": c.get("title", "").split("｜")[0],
                "keyword": c.get("keyword", ""),
                "scene1": scene1[0] if scene1 else "",
                "criteria1": criteria[0].get("name", "") if criteria else "",
                "criteria1_why": criteria[0].get("why", "") if criteria else "",
                "not_for1": (c.get("not_for") or [""])[0],
                "complaint1": complaints[0].get("complaint", "") if complaints else "",
                "fix1": complaints[0].get("fix", "") if complaints else "",
                "count": len(c.get("items", [])),
                "url": link,
            },
        })

    for p in products:
        if p.get("no_post"):
            continue
        site_url = add_utm(url_for(site, f"/items/{p['slug']}/"), site, source, p["slug"])
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
                "cons1": (p.get("cons") or [""])[0],
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
    eligible.sort(key=lambda c: (last_posted.get(c["key"], never),
                                 0 if c["kind"] == "comparison" else 1))
    for c in eligible:
        c["post_count"] = post_count.get(c["key"], 0)
    return eligible[:count]


def compose(candidate: dict, config: dict, limit: int, measure) -> str:
    """テンプレートを1つ選んで文面を作る。同じ対象には毎回違うテンプレートが当たる。"""
    key = "comparison_templates" if candidate["kind"] == "comparison" else "templates"
    templates = config.get(key) or config.get("templates") or ["{title}\n{url}"]
    template = templates[candidate.get("post_count", 0) % len(templates)]

    variables = dict(candidate["vars"])
    variables["ad_label"] = config.get("ad_label", "#PR")
    text = template.format(**{k: variables.get(k, "") for k in _fields(template)})

    # 空欄の変数が残した空行を畳む
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line and (not cleaned or not cleaned[-1]):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned).strip()

    if measure(text) > limit:
        text = trim(text, limit, measure)
    return text


def _fields(template: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", template))


def trim(text: str, limit: int, measure) -> str:
    """長すぎる場合、URLと広告表示を残したまま本文を削る。"""
    lines = text.splitlines()
    tail = [ln for ln in lines if "http" in ln]
    body = [ln for ln in lines if "http" not in ln]
    while body and measure("\n".join(body + tail)) > limit:
        longest = max(range(len(body)), key=lambda i: len(body[i]))
        if len(body[longest]) <= 12:
            body.pop(longest)
        else:
            body[longest] = body[longest][: len(body[longest]) - 8].rstrip() + "…"
    return "\n".join(body + tail).strip()


def record(state: dict, key: str, text: str, post_id: str = "") -> None:
    state.setdefault("history", []).append({
        "key": key,
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "post_id": post_id,
        "text": text,
    })
    # 履歴は直近500件だけ残す
    state["history"] = state["history"][-500:]
