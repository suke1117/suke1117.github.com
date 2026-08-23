#!/usr/bin/env python3
"""data/*.yml と content/posts/*.md から静的サイトを public/ に生成する。

  python scripts/build_site.py [--out public]

出力:
  /                     トップ（新着・カテゴリ一覧）
  /items/<slug>/        商品レビューページ
  /roundups/            まとめ記事の一覧
  /roundups/<slug>/     まとめ記事
  /posts/               記事一覧
  /posts/<slug>/        Markdown記事
  /categories/<name>/   カテゴリ別一覧
  /about/ /disclosure/ /privacy/
  feed.xml  sitemap.xml  robots.txt
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path

import markdown as md
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from common import (
    ROOT,
    amazon_url,
    has_associate_tag,
    load_site,
    load_yaml,
    site_root,
    slugify,
    url_for,
)

TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
POSTS_DIR = ROOT / "content" / "posts"

FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def read_posts() -> list[dict]:
    """content/posts/*.md を読む。YAML front matter 付き Markdown。"""
    posts = []
    if not POSTS_DIR.exists():
        return posts
    for path in sorted(POSTS_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta: dict = {}
        m = FRONT_MATTER.match(raw)
        if m:
            meta = yaml.safe_load(m.group(1)) or {}
            raw = raw[m.end():]
        if meta.get("draft"):
            continue
        meta.setdefault("slug", path.stem)
        meta.setdefault("title", path.stem)
        meta.setdefault("date", dt.date.today().isoformat())
        meta["body_html"] = md.markdown(raw, extensions=["extra", "toc", "sane_lists"])
        meta["path"] = f"/posts/{meta['slug']}/"
        posts.append(meta)
    posts.sort(key=lambda p: str(p.get("date")), reverse=True)
    return posts


CACHE_PATH = ROOT / "data" / "amazon_cache.json"
# PA-API の規約上、取得した価格を表示できるのは24時間以内
PRICE_TTL = dt.timedelta(hours=24)


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def merge_cache(product: dict, cache: dict) -> None:
    """PA-API のキャッシュを、products.yml で未記入の項目にだけ流し込む。

    手書きした内容は常に優先する（自動取得で消えないようにするため）。
    """
    entry = cache.get(product.get("asin", ""))
    if not entry:
        return
    for field in ("title", "brand", "image"):
        if not product.get(field) and entry.get(field):
            product[field] = entry[field]
    if not product.get("features") and entry.get("features"):
        product["features"] = entry["features"][:5]
    product["availability"] = entry.get("availability", "")

    fetched_at = entry.get("fetched_at")
    if fetched_at and entry.get("price_display"):
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(fetched_at)
        if age < PRICE_TTL:
            product["price_exact"] = entry["price_display"]
            product["price_fetched_at"] = fetched_at


def prepare_products(site: dict) -> dict[str, dict]:
    """products.yml を読み、リンクなどの派生情報を足して slug -> product の辞書にする。"""
    products = load_yaml("products.yml", []) or []
    cache = load_cache()
    result: dict[str, dict] = {}
    for p in products:
        merge_cache(p, cache)
        slug = p.get("slug") or slugify(p.get("title", ""))
        p["slug"] = slug
        p["path"] = f"/items/{slug}/"
        p["url"] = url_for(site, p["path"])
        p["amazon_url"] = amazon_url(p["asin"], site) if p.get("asin") else ""
        p.setdefault("category", "その他")
        p.setdefault("tags", [])
        p.setdefault("pros", [])
        p.setdefault("cons", [])
        p["review_html"] = md.markdown(p.get("review", "") or "", extensions=["extra"])
        if slug in result:
            raise SystemExit(f"products.yml: slug が重複しています -> {slug}")
        result[slug] = p
    return result


def prepare_roundups(site: dict, products: dict[str, dict]) -> list[dict]:
    roundups = load_yaml("roundups.yml", []) or []
    prepared = []
    for r in roundups:
        r["path"] = f"/roundups/{r['slug']}/"
        r["url"] = url_for(site, r["path"])
        entries = []
        for item in r.get("items", []):
            slug = item["slug"]
            product = products.get(slug)
            if product is None:
                raise SystemExit(
                    f"roundups.yml '{r['slug']}': products.yml に存在しない slug -> {slug}"
                )
            entries.append({"product": product, "comment": item.get("comment", "")})
        r["entries"] = entries
        r["count"] = len(entries)
        r["lead_html"] = md.markdown(r.get("lead", "") or "", extensions=["extra"])
        r["closing_html"] = md.markdown(r.get("closing", "") or "", extensions=["extra"])
        r.setdefault("updated", dt.date.today().isoformat())
        prepared.append(r)
    prepared.sort(key=lambda r: str(r.get("updated")), reverse=True)
    return prepared


def write(out: Path, path: str, html: str) -> None:
    """'/items/foo/' -> out/items/foo/index.html に書き出す。"""
    if path.endswith("/"):
        target = out / path.strip("/") / "index.html"
    else:
        target = out / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")


def build(out: Path) -> None:
    site = load_site()
    products = prepare_products(site)
    roundups = prepare_roundups(site, products)
    posts = read_posts()

    categories: dict[str, list[dict]] = {}
    for p in products.values():
        categories.setdefault(p["category"], []).append(p)

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        site=site,
        base=site.get("base_path", "").rstrip("/"),
        site_root=site_root(site),
        categories=sorted(categories.keys()),
        now=dt.datetime.now(dt.timezone.utc),
        has_tag=has_associate_tag(site),
        slugify=slugify,
    )

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    product_list = sorted(products.values(), key=lambda p: p.get("title", ""))

    write(out, "/", env.get_template("index.html").render(
        products=product_list, roundups=roundups, posts=posts[:5],
        category_map=categories,
    ))

    for p in product_list:
        related = [q for q in categories[p["category"]] if q["slug"] != p["slug"]][:3]
        write(out, p["path"], env.get_template("item.html").render(
            product=p, related=related,
            in_roundups=[r for r in roundups if any(e["product"]["slug"] == p["slug"] for e in r["entries"])],
        ))

    write(out, "/roundups/", env.get_template("roundup_list.html").render(roundups=roundups))
    for r in roundups:
        write(out, r["path"], env.get_template("roundup.html").render(roundup=r))

    write(out, "/posts/", env.get_template("post_list.html").render(posts=posts))
    for post in posts:
        write(out, post["path"], env.get_template("post.html").render(post=post))

    for name, items in categories.items():
        write(out, f"/categories/{slugify(name)}/", env.get_template("category.html").render(
            category=name, products=sorted(items, key=lambda p: p.get("title", "")),
            roundups=[r for r in roundups if r.get("category") == name],
        ))

    for page in ("about", "disclosure", "privacy"):
        write(out, f"/{page}/", env.get_template(f"page_{page}.html").render())

    # フィード / サイトマップ / robots
    all_urls = (
        [{"loc": url_for(site, "/"), "lastmod": dt.date.today().isoformat()}]
        + [{"loc": p["url"], "lastmod": dt.date.today().isoformat()} for p in product_list]
        + [{"loc": r["url"], "lastmod": str(r["updated"])} for r in roundups]
        + [{"loc": url_for(site, p["path"]), "lastmod": str(p["date"])} for p in posts]
    )
    write(out, "/sitemap.xml", env.get_template("sitemap.xml").render(urls=all_urls))
    write(out, "/feed.xml", env.get_template("feed.xml").render(
        entries=(
            [{"title": r["title"], "url": r["url"], "summary": r.get("lead", ""), "date": str(r["updated"])} for r in roundups]
            + [{"title": p["title"], "url": p["url"], "summary": p.get("summary", ""), "date": dt.date.today().isoformat()} for p in product_list]
        )
    ))
    write(out, "/robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {url_for(site, '/sitemap.xml')}\n")
    # Jekyll の自動ビルドを止める（Actions でデプロイするため）
    (out / ".nojekyll").write_text("", encoding="utf-8")

    if STATIC.exists():
        shutil.copytree(STATIC, out / "static", dirs_exist_ok=True)

    print(f"生成完了: {out}")
    print(f"  商品ページ   {len(product_list)}")
    print(f"  まとめ記事   {len(roundups)}")
    print(f"  記事         {len(posts)}")
    print(f"  カテゴリ     {len(categories)}")
    if not has_associate_tag(site):
        print("  ※ associate_tag が未設定のため、Amazonリンクはタグなしで出力されています。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="public")
    args = parser.parse_args()
    build(ROOT / args.out)


if __name__ == "__main__":
    main()
