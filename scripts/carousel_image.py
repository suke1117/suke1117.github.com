"""Instagram 用の縦型カルーセル（1080x1350）を作る。

Instagram はフィード投稿から外部リンクに送客できないため、
このカルーセルは「送客」ではなく「保存される情報」を狙う。
比較表・選ぶ基準・よくある失敗は、あとで見返される形式。

  <slug>-1.svg  表紙
  <slug>-2..4   選ぶ基準（1枚ずつ）
  <slug>-5      比較表
  <slug>-6      よくある失敗
  <slug>-7      状況別のまとめ
  <slug>-8      導線（プロフィールのリンクへ）

広告表示（#PR）は表紙に入れる。末尾のハッシュタグに紛れさせない。
"""

from __future__ import annotations

import svg

WIDTH, HEIGHT = 1080, 1350
MARGIN = 72
CONTENT = WIDTH - MARGIN * 2

INK = "#1b1b1f"
SOFT = "#4a4a55"
MUTE = "#6f6f7a"
ACCENT = "#b45309"
PAPER = "#fdfcfa"
DEEP = "#2c2723"
BAND = "#f0ece6"
GOOD = "#1f6b3f"


def _page_mark(parts: list[str], index: int, total: int, on_dark: bool = False) -> None:
    color = "#ffffff88" if on_dark else MUTE
    parts.append(svg.text(WIDTH - MARGIN, MARGIN + 6, 24, color, f"{index} / {total}", "600", "end"))


def _swipe(parts: list[str]) -> None:
    parts.append(svg.text(WIDTH - MARGIN, HEIGHT - 56, 26, ACCENT, "スワイプ →", "700", "end"))


def _title_bar(parts: list[str], label: str) -> float:
    parts.append(svg.rect(0, 0, WIDTH, 10, ACCENT))
    parts.append(svg.text(MARGIN, MARGIN + 6, 26, ACCENT, label, "700"))
    return MARGIN + 80


def _cover(comparison: dict, site: dict, total: int) -> str:
    parts = svg.open_svg(WIDTH, HEIGHT, DEEP)
    parts.append(svg.rect(0, 0, WIDTH, 10, ACCENT))
    _page_mark(parts, 1, total, on_dark=True)

    y = 420
    parts.append(svg.text(MARGIN, y, 28, "#e8b87f", comparison.get("keyword", ""), "700"))
    y += 90
    chunk, y = svg.block(MARGIN, y, CONTENT, 66, "#ffffff",
                         comparison.get("title", "").split("｜")[0], 1.35, 4, "700")
    parts.append(chunk)
    y += 30

    sub = comparison.get("title", "").split("｜")
    if len(sub) > 1:
        chunk, y = svg.block(MARGIN, y, CONTENT, 34, "#cfc7bd", sub[1], 1.5, 3)
        parts.append(chunk)
        y += 24

    parts.append(svg.text(MARGIN, y + 30, 30, "#e8b87f",
                          f"{comparison.get('count', 0)}商品を比較", "700"))

    parts.append(svg.rect(MARGIN, HEIGHT - 210, 120, 46, "#ffffff22", 23))
    parts.append(svg.text(MARGIN + 60, HEIGHT - 178, 26, "#e8b87f", "#PR", "700", "middle"))
    parts.append(svg.text(MARGIN, HEIGHT - 120, 28, "#ffffff", site.get("title", ""), "700"))
    parts.append(svg.text(MARGIN, HEIGHT - 78, 23, "#a8a09a",
                          (site.get("disclosure") or {}).get("short", "")))
    _swipe(parts)
    parts.append("</svg>")
    return "\n".join(parts)


def _criterion_slide(criterion: dict, index: int, count: int, page: int, total: int) -> str:
    parts = svg.open_svg(WIDTH, HEIGHT, PAPER)
    y = _title_bar(parts, f"選ぶときに見る{count}つ")
    _page_mark(parts, page, total)

    parts.append(svg.rect(MARGIN, y, 72, 72, BAND, 36))
    parts.append(svg.text(MARGIN + 36, y + 50, 38, ACCENT, str(index), "700", "middle"))
    y += 130

    chunk, y = svg.block(MARGIN, y, CONTENT, 52, INK, criterion.get("name", ""), 1.35, 3, "700")
    parts.append(chunk)
    y += 40
    chunk, y = svg.block(MARGIN, y, CONTENT, 32, SOFT, criterion.get("why", ""), 1.6, 5)
    parts.append(chunk)
    y += 40

    if criterion.get("check"):
        top = y
        parts.append(svg.text(MARGIN + 28, y + 48, 24, ACCENT, "確認のしかた", "700"))
        chunk, after = svg.block(MARGIN + 28, y + 96, CONTENT - 56, 29, SOFT,
                                 criterion["check"], 1.55, 5)
        parts.append(svg.rect(MARGIN, top, CONTENT, after - top - 10, BAND, 16))
        parts.append(chunk)
    _swipe(parts)
    parts.append("</svg>")
    return "\n".join(parts)


def _table_slide(comparison: dict, page: int, total: int) -> str:
    parts = svg.open_svg(WIDTH, HEIGHT, PAPER)
    y = _title_bar(parts, "比較表")
    _page_mark(parts, page, total)
    y += 30

    columns = (comparison.get("table_columns") or [])[:3]
    entries = (comparison.get("entries") or [])[:4]
    best = comparison.get("best_cells") or {}

    if columns and entries:
        name_w = CONTENT * 0.40
        col_w = (CONTENT - name_w) / len(columns)
        parts.append(svg.rect(MARGIN, y, CONTENT, 66, BAND))
        parts.append(svg.text(MARGIN + 18, y + 44, 25, SOFT, "商品", "700"))
        for i, col in enumerate(columns):
            parts.append(svg.text(MARGIN + name_w + col_w * i + 12, y + 44, 25, SOFT,
                                  svg.fit(col, col_w - 24, 25), "700"))
        y += 66
        # 行数が少ないときは1行を高くして、下の余白を減らす
        available = HEIGHT - y - 210
        row_h = max(108, min(160, available / max(len(entries), 1)))
        for entry in entries:
            product = entry["product"]
            parts.append(svg.line(MARGIN, y, WIDTH - MARGIN, y, "#e0dbd3"))
            chunk, _ = svg.block(MARGIN + 18, y + 44, name_w - 30, 26, INK,
                                 product.get("title", ""), 1.32, 2)
            parts.append(chunk)
            for i, col in enumerate(columns):
                value = str((product.get("specs") or {}).get(col, "-"))
                x = MARGIN + name_w + col_w * i + 12
                is_best = product["slug"] in (best.get(col, {}).get("slugs") or [])
                parts.append(svg.text(x, y + 44, 26, GOOD if is_best else "#3b3b44",
                                      svg.fit(value, col_w - 24, 26), "700" if is_best else ""))
                if is_best:
                    parts.append(svg.text(x, y + 78, 21, GOOD, best[col]["label"], "700"))
            y += row_h
        parts.append(svg.line(MARGIN, y, WIDTH - MARGIN, y, "#e0dbd3"))

    if best:
        parts.append(svg.text(MARGIN, HEIGHT - 96, 22, MUTE,
                              "「最少」「最大」はこの記事の基準での値です。総合的な優劣ではありません。"))
    _swipe(parts)
    parts.append("</svg>")
    return "\n".join(parts)


def _mistakes_slide(comparison: dict, page: int, total: int) -> str:
    parts = svg.open_svg(WIDTH, HEIGHT, PAPER)
    y = _title_bar(parts, "よくある失敗")
    _page_mark(parts, page, total)
    y += 40

    for complaint in (comparison.get("complaints") or [])[:3]:
        if y > HEIGHT - 260:
            break
        chunk, y = svg.block(MARGIN, y, CONTENT, 34, INK,
                             "「" + complaint.get("complaint", "") + "」", 1.4, 2, "700")
        parts.append(chunk)
        y += 16
        top = y - 30
        chunk, after = svg.block(MARGIN + 20, y + 8, CONTENT - 40, 27, SOFT,
                                 "→ " + complaint.get("fix", ""), 1.55, 4)
        parts.append(svg.rect(MARGIN, top, CONTENT, after - top - 8, BAND, 14))
        parts.append(chunk)
        y = after + 44
    _swipe(parts)
    parts.append("</svg>")
    return "\n".join(parts)


def _answer_slide(comparison: dict, page: int, total: int) -> str:
    parts = svg.open_svg(WIDTH, HEIGHT, PAPER)
    y = _title_bar(parts, "状況別のまとめ")
    _page_mark(parts, page, total)
    y += 50

    for s in (comparison.get("situation_entries") or [])[:3]:
        if y > HEIGHT - 240:
            break
        chunk, y = svg.block(MARGIN, y, CONTENT, 30, MUTE, s.get("situation", ""), 1.45, 2)
        parts.append(chunk)
        y += 10
        chunk, y = svg.block(MARGIN, y, CONTENT, 40, ACCENT,
                             s["product"].get("title", ""), 1.35, 2, "700")
        parts.append(chunk)
        y += 14
        chunk, y = svg.block(MARGIN, y, CONTENT, 26, SOFT, s.get("reason", ""), 1.55, 3)
        parts.append(chunk)
        y += 46
    _swipe(parts)
    parts.append("</svg>")
    return "\n".join(parts)


def _outro_slide(comparison: dict, site: dict, page: int, total: int) -> str:
    parts = svg.open_svg(WIDTH, HEIGHT, DEEP)
    parts.append(svg.rect(0, 0, WIDTH, 10, ACCENT))
    _page_mark(parts, page, total, on_dark=True)

    y = 330
    chunk, y = svg.block(MARGIN, y, CONTENT, 54, "#ffffff",
                         "くわしい比較は、プロフィールのリンクから", 1.4, 3, "700")
    parts.append(chunk)
    y += 50
    chunk, y = svg.block(MARGIN, y, CONTENT, 30, "#cfc7bd",
                         f"レビューで多かった不満と、その対処までまとめています"
                         f"（{comparison.get('count', 0)}商品 / 約{comparison.get('read_minutes', 3)}分）",
                         1.6, 4)
    parts.append(chunk)

    parts.append(svg.rect(MARGIN, y + 50, 470, 84, "#ffffff14", 42))
    parts.append(svg.text(MARGIN + 36, y + 104, 30, "#e8b87f", "保存して、買う前に見返す", "700"))

    parts.append(svg.text(MARGIN, HEIGHT - 150, 30, "#ffffff", site.get("title", ""), "700"))
    parts.append(svg.text(MARGIN, HEIGHT - 108, 24, "#a8a09a",
                          (site.get("disclosure") or {}).get("short", "")))
    parts.append(svg.text(MARGIN, HEIGHT - 66, 26, "#e8b87f", "#PR", "700"))
    parts.append("</svg>")
    return "\n".join(parts)


def render_slides(comparison: dict, site: dict) -> list[str]:
    """カルーセルの全スライドを、順番に返す。"""
    criteria = (comparison.get("criteria") or [])[:3]
    total = 1 + len(criteria) + 3 + 1     # 表紙 + 基準 + 表/失敗/まとめ + 導線

    slides = [_cover(comparison, site, total)]
    page = 2
    for i, criterion in enumerate(criteria, 1):
        slides.append(_criterion_slide(criterion, i, len(criteria), page, total))
        page += 1
    slides.append(_table_slide(comparison, page, total)); page += 1
    slides.append(_mistakes_slide(comparison, page, total)); page += 1
    slides.append(_answer_slide(comparison, page, total)); page += 1
    slides.append(_outro_slide(comparison, site, page, total))
    return slides


# 投稿文（キャプション）。Instagram は本文にリンクを貼っても機能しないため、
# プロフィールのリンクへ誘導する形にする。
BASE_TAGS = ["猫", "猫のいる暮らし", "猫用品", "ねこ", "買い物メモ"]


def caption(comparison: dict, site: dict) -> str:
    """Instagram 用のキャプション。広告表示は冒頭に置く。"""
    lines = [
        "#PR（アフィリエイトリンクを含むサイトへ誘導しています）",
        "",
        comparison.get("title", "").split("｜")[0],
        "",
    ]
    scene = (comparison.get("scene") or "").strip().splitlines()
    if scene:
        lines += [scene[0], ""]

    criteria = comparison.get("criteria") or []
    if criteria:
        lines.append(f"選ぶときに見る{len(criteria[:3])}つ")
        for i, c in enumerate(criteria[:3], 1):
            lines.append(f"{i}. {c.get('name', '')}")
        lines.append("")

    situations = comparison.get("situation_entries") or []
    if situations:
        lines.append("状況別")
        for s in situations[:3]:
            lines.append(f"・{s.get('situation', '')} → {s['product'].get('title', '')}")
        lines.append("")

    lines += [
        "くわしい比較（レビューで多かった不満と、その対処まで）は",
        "プロフィールのリンクからどうぞ。",
        "",
        "保存しておくと、買う前に見返せます。",
        "",
    ]

    tags = BASE_TAGS + [comparison.get("category", ""), comparison.get("keyword", "").split()[0]]
    seen, unique = set(), []
    for tag in tags:
        tag = tag.strip().replace(" ", "")
        if tag and tag not in seen:
            seen.add(tag)
            unique.append("#" + tag)
    lines.append(" ".join(unique))
    return "\n".join(lines)
