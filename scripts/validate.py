#!/usr/bin/env python3
"""公開前のチェック。記事の型と、書いてはいけない表現を機械的に見る。

  python scripts/validate.py

エラーが1件でもあると終了コード1。GitHub Actions のビルド前に走る。

ここで弾いているのは主に2つ。

1. 比較記事の型が崩れていないか
   （選ぶ基準が3つあるか、向いていない人を書いているか、不満と対処があるか）
2. AIが書いたままの文章が公開されていないか
   （実際に使った一言 own_note が雛形のままなら、比較記事には載せさせない）

2つ目が重要で、レビューを読ませて生成した文章をそのまま出すと、
Amazonアソシエイトの審査でも読者の信用でも不利になる。
"""

from __future__ import annotations

import re
import sys

from common import load_yaml

# 保証・断定表現。景品表示法と、アソシエイト規約の両方に関わる
BANNED = [
    ("絶対", "保証表現"),
    ("必ず痩", "効果の断定"),
    ("誰でも", "保証表現"),
    ("100%", "保証表現"),
    ("確実に", "保証表現"),
    ("治る", "医薬品的な効能の表現"),
    ("完治", "医薬品的な効能の表現"),
    ("効果があります", "効果の断定"),
    ("最安値", "価格の断定（変動するため）"),
    ("日本一", "最上級表現の根拠が必要"),
]

# 雛形のまま残っている文章を検出する
PLACEHOLDER = re.compile(r"[（(](実際に|本文|良かった点|気になった点|向いている人|メーカー名|1行で)")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def check_banned(report: Report, where: str, text: str) -> None:
    for word, reason in BANNED:
        if word in str(text):
            report.error(f"{where}: 「{word}」は使わないでください（{reason}）")


def check_products(report: Report) -> dict[str, dict]:
    products = load_yaml("products.yml", []) or []
    by_slug: dict[str, dict] = {}

    for p in products:
        slug = p.get("slug", "")
        where = f"products.yml[{slug or '?'}]"
        if not slug:
            report.error(f"{where}: slug がありません")
            continue
        if slug in by_slug:
            report.error(f"{where}: slug が重複しています")
        by_slug[slug] = p

        if not p.get("asin"):
            report.error(f"{where}: asin がありません")
        elif not re.fullmatch(r"[A-Z0-9]{10}", p["asin"]):
            report.warn(f"{where}: asin の形式が怪しいです -> {p['asin']}")

        if not p.get("cons"):
            report.error(f"{where}: cons（気になった点）が空です。全部おすすめにしないでください")
        if not p.get("summary"):
            report.error(f"{where}: summary がありません")

        for field in ("summary", "review", "best_for"):
            check_banned(report, f"{where}.{field}", p.get(field, ""))
        for field in ("pros", "cons"):
            for line in p.get(field) or []:
                check_banned(report, f"{where}.{field}", line)

        for field in ("summary", "best_for", "review", "title", "brand"):
            if PLACEHOLDER.search(str(p.get(field) or "")):
                report.warn(f"{where}.{field}: 雛形の文章が残っています")

    return by_slug


def check_comparisons(report: Report, products: dict[str, dict]) -> None:
    comparisons = load_yaml("comparisons.yml", []) or []
    slugs = {c.get("slug") for c in comparisons}

    for c in comparisons:
        slug = c.get("slug", "?")
        where = f"comparisons.yml[{slug}]"

        for field in ("title", "keyword", "scene", "category"):
            if not c.get(field):
                report.error(f"{where}: {field} がありません")

        criteria = c.get("criteria") or []
        if len(criteria) < 3:
            report.error(f"{where}: criteria（選ぶ基準）は3つ必要です（今は{len(criteria)}つ）")
        for i, criterion in enumerate(criteria, 1):
            if not criterion.get("check"):
                report.warn(f"{where}.criteria[{i}]: check（確認のしかた）が空です")

        if not c.get("not_for"):
            report.error(f"{where}: not_for（向いていない人）がありません。先に外してください")
        if not c.get("complaints"):
            report.warn(f"{where}: complaints（レビューの不満と対処）が空です")
        if not c.get("first_steps"):
            report.warn(f"{where}: first_steps（買ったあと最初にやること）が空です")

        entries = c.get("items") or []
        if len(entries) < 2:
            report.error(f"{where}: 比較記事なので、items は2件以上必要です")

        for item in entries:
            target = products.get(item.get("slug", ""))
            if target is None:
                report.error(f"{where}.items: 存在しない slug -> {item.get('slug')}")
                continue
            for field in ("for_whom", "not_for_whom"):
                if not item.get(field):
                    report.error(f"{where}.items[{item['slug']}]: {field} がありません")
            # 比較記事に載せる商品は、実際に使った一言が必須
            note = str(target.get("own_note") or "").strip()
            if not note or PLACEHOLDER.search(note):
                report.error(
                    f"products.yml[{item['slug']}]: own_note が空か雛形のままです。"
                    f"比較記事『{slug}』に載せる商品には、実際に使って気づいた一言を書いてください"
                )

        for s in c.get("situations") or []:
            if s.get("slug") not in products:
                report.error(f"{where}.situations: 存在しない slug -> {s.get('slug')}")

        for rel in c.get("related") or []:
            if rel.get("slug") not in slugs:
                report.error(f"{where}.related: 存在しない比較記事 -> {rel.get('slug')}")
            if len(str(rel.get("lead") or "")) > 80:
                report.warn(f"{where}.related[{rel.get('slug')}]: 誘導文が80字を超えています")

        for field in ("scene", "closing"):
            check_banned(report, f"{where}.{field}", c.get(field, ""))
        for item in entries:
            check_banned(report, f"{where}.items", item.get("comment", ""))

    if not comparisons:
        report.warn("comparisons.yml が空です。比較記事がこのサイトの主力です")


def check_ad_label(report: Report) -> None:
    config = load_yaml("x_templates.yml", {}) or {}
    if not str(config.get("ad_label") or "").strip():
        report.error("x_templates.yml: ad_label が空です。広告であることの明示は外せません")
    for key in ("templates", "comparison_templates"):
        for i, template in enumerate(config.get(key) or [], 1):
            if "{url}" not in template:
                report.error(f"x_templates.yml.{key}[{i}]: {{url}} がありません")
            if "{ad_label}" not in template:
                report.error(f"x_templates.yml.{key}[{i}]: {{ad_label}} がありません")


def main() -> None:
    report = Report()
    products = check_products(report)
    check_comparisons(report, products)
    check_ad_label(report)

    for w in report.warnings:
        print(f"警告: {w}")
    for e in report.errors:
        print(f"エラー: {e}")

    if report.errors:
        print(f"\n{len(report.errors)}件のエラーがあります。公開前に直してください。")
        sys.exit(1)
    print(f"\nチェック完了（警告 {len(report.warnings)}件、エラーなし）")


if __name__ == "__main__":
    main()
