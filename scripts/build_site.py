#!/usr/bin/env python3
"""data/*.yml と content/posts/*.md から静的サイトを public/ に生成する。

  python scripts/build_site.py [--out public]

出力:
  /                        トップ
  /compare/<slug>/         比較記事（記事の主力）
  /compare/                比較記事の一覧
  /items/<slug>/           商品ページ
  /posts/<slug>/           Markdown記事
  /categories/<name>/      カテゴリ別
  /pin/<slug>.svg          Pinterest用の比較表画像
  /about/ /disclosure/ /privacy/
  feed.xml  sitemap.xml  robots.txt
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import markdown as md
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

import pin_image
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
CACHE_PATH = ROOT / "data" / "amazon_cache.json"

FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
# PA-API の規約上、取得した価格を表示できるのは24時間以内
PRICE_TTL = dt.timedelta(hours=24)


# ── 読み込み ──────────────────────────────────────────────

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


def prepare_products(site: dict) -> dict[str, dict]:
    """products.yml を読み、リンクなどの派生情報を足して slug -> product にする。"""
    cache = load_cache()
    result: dict[str, dict] = {}
    for p in load_yaml("products.yml", []) or []:
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
        p.setdefault("specs", {})
        p.setdefault("stance", "researched")
        p.setdefault("evidence", {})
        p["review_html"] = md.markdown(p.get("review", "") or "", extensions=["extra"])
        p["own_note_html"] = md.markdown(p.get("own_note", "") or "", extensions=["extra"])
        if slug in result:
            raise SystemExit(f"products.yml: slug が重複しています -> {slug}")
        result[slug] = p
    return result


def prepare_comparisons(site: dict, products: dict[str, dict]) -> list[dict]:
    """comparisons.yml を、テンプレートがそのまま描ける形に整える。"""
    prepared = []
    for c in load_yaml("comparisons.yml", []) or []:
        c["path"] = f"/compare/{c['slug']}/"
        c["url"] = url_for(site, c["path"])
        c["pin_path"] = f"/pin/{c['slug']}.svg"
        c["pin_url"] = url_for(site, c["pin_path"])

        def resolve(slug: str, where: str) -> dict:
            product = products.get(slug)
            if product is None:
                raise SystemExit(
                    f"comparisons.yml '{c['slug']}' の {where}: "
                    f"products.yml に存在しない slug -> {slug}"
                )
            return product

        c["entries"] = [
            {**item, "product": resolve(item["slug"], "items")}
            for item in c.get("items", [])
        ]
        c["situation_entries"] = [
            {**s, "product": resolve(s["slug"], "situations")}
            for s in c.get("situations", [])
        ]
        c["count"] = len(c["entries"])
        c["owned_count"] = sum(1 for e in c["entries"] if e["product"].get("stance") == "owned")
        # 記事全体の検証スタンス。読者が最初に知るべき情報なので冒頭に出す
        c["stance"] = (
            "owned" if c["owned_count"] == c["count"] and c["count"]
            else "mixed" if c["owned_count"]
            else "researched"
        )
        c["basis_html"] = md.markdown(c.get("basis", "") or "", extensions=["extra"])
        c["scene_html"] = md.markdown(c.get("scene", "") or "", extensions=["extra"])
        c["closing_html"] = md.markdown(c.get("closing", "") or "", extensions=["extra"])
        c.setdefault("table_columns", [])
        c.setdefault("updated", dt.date.today().isoformat())
        prepared.append(c)

    # 回遊（related）を解決する。相手が存在しない場合はここで気づける
    by_slug = {c["slug"]: c for c in prepared}
    for c in prepared:
        links = []
        for rel in c.get("related", []) or []:
            target = by_slug.get(rel["slug"])
            if target is None:
                raise SystemExit(
                    f"comparisons.yml '{c['slug']}' の related: "
                    f"存在しない slug -> {rel['slug']}"
                )
            links.append({**rel, "target": target})
        c["related_links"] = links

    prepared.sort(key=lambda c: str(c.get("updated")), reverse=True)
    return prepared


# ── 出力 ──────────────────────────────────────────────────

def write(out: Path, path: str, content: str) -> None:
    """'/compare/foo/' -> out/compare/foo/index.html に書き出す。"""
    target = out / path.strip("/") / "index.html" if path.endswith("/") else out / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def rasterize(out: Path) -> None:
    """rsvg-convert があれば Pinterest 用の SVG を PNG にも書き出す。

    Pinterest は SVG を受け付けないため、PNG があるとそのまま投稿できる。
    ローカルに rsvg-convert が無い場合は SVG だけ残して黙って進む。
    """
    if not shutil.which("rsvg-convert"):
        return
    for svg in (out / "pin").glob("*.svg"):
        subprocess.run(
            ["rsvg-convert", "-w", str(pin_image.WIDTH), "-h", str(pin_image.HEIGHT),
             "-o", str(svg.with_suffix(".png")), str(svg)],
            check=True,
        )


def build(out: Path) -> None:
    site = load_site()
    products = prepare_products(site)
    comparisons = prepare_comparisons(site, products)
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
        now=dt.datetime.now(dt.timezone.utc),
        has_tag=has_associate_tag(site),
        slugify=slugify,
    )

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    product_list = sorted(products.values(), key=lambda p: p.get("title", ""))

    write(out, "/", env.get_template("index.html").render(
        products=product_list, comparisons=comparisons, posts=posts[:5],
        category_map=categories,
    ))

    write(out, "/compare/", env.get_template("comparison_list.html").render(comparisons=comparisons))
    for c in comparisons:
        write(out, c["path"], env.get_template("comparison.html").render(comparison=c))
        write(out, c["pin_path"], pin_image.render(c, site))

    for p in product_list:
        write(out, p["path"], env.get_template("item.html").render(
            product=p,
            related=[q for q in categories[p["category"]] if q["slug"] != p["slug"]][:3],
            in_comparisons=[c for c in comparisons
                            if any(e["product"]["slug"] == p["slug"] for e in c["entries"])],
        ))

    write(out, "/posts/", env.get_template("post_list.html").render(posts=posts))
    for post in posts:
        write(out, post["path"], env.get_template("post.html").render(post=post))

    for name, items in categories.items():
        write(out, f"/categories/{slugify(name)}/", env.get_template("category.html").render(
            category=name,
            products=sorted(items, key=lambda p: p.get("title", "")),
            comparisons=[c for c in comparisons if c.get("category") == name],
        ))

    for page in ("about", "disclosure", "privacy"):
        write(out, f"/{page}/", env.get_template(f"page_{page}.html").render())

    today = dt.date.today().isoformat()
    urls = (
        [{"loc": url_for(site, "/"), "lastmod": today}]
        + [{"loc": c["url"], "lastmod": str(c["updated"])} for c in comparisons]
        + [{"loc": p["url"], "lastmod": today} for p in product_list]
        + [{"loc": url_for(site, p["path"]), "lastmod": str(p["date"])} for p in posts]
    )
    write(out, "/sitemap.xml", env.get_template("sitemap.xml").render(urls=urls))
    write(out, "/feed.xml", env.get_template("feed.xml").render(entries=(
        [{"title": c["title"], "url": c["url"], "summary": c.get("scene", ""), "date": str(c["updated"])}
         for c in comparisons]
        + [{"title": p["title"], "url": p["url"], "summary": p.get("summary", ""), "date": today}
           for p in product_list]
    )))
    write(out, "/robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {url_for(site, '/sitemap.xml')}\n")
    # Jekyll の自動ビルドを止める（Actions でデプロイするため）
    (out / ".nojekyll").write_text("", encoding="utf-8")

    if STATIC.exists():
        shutil.copytree(STATIC, out / "static", dirs_exist_ok=True)
    rasterize(out)

    print(f"生成完了: {out}")
    print(f"  比較記事     {len(comparisons)}")
    print(f"  商品ページ   {len(product_list)}")
    print(f"  記事         {len(posts)}")
    print(f"  カテゴリ     {len(categories)}")
    if not has_associate_tag(site):
        print("  ※ associate_tag が未設定のため、Amazonリンクはタグなしで出力されています。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="public")
    parser.add_argument(
        "--local", action="store_true",
        help="手元で確認する用。base_path を外して public/ 直下から配信できる形にする",
    )
    args = parser.parse_args()
    if args.local:
        os.environ["SITE_BASE_PATH"] = ""
    build(ROOT / args.out)


if __name__ == "__main__":
    main()
