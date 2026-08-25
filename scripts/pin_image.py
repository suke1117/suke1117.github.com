"""Pinterest 用の画像（SVG）を、比較記事1本につき3枚作る。

Pinterest は縦長が伸びやすいため 2:3（1000x1500）で出力する。
検索エンジンに近い性質で、ピンは消えずに蓄積するため、
1記事から複数の切り口を出しておくと入口が増える。

  <slug>.svg           比較表（OGP画像にも使う）
  <slug>-criteria.svg  選ぶときに見る3つ
  <slug>-mistakes.svg  よくある失敗

rsvg-convert があれば build_site.py が PNG にも変換する
（Pinterest は SVG のアップロードを受け付けないため）。
"""

from __future__ import annotations

import svg

WIDTH, HEIGHT = 1000, 1500
MARGIN = 60
CONTENT = WIDTH - MARGIN * 2
FOOTER_H = 130

INK = "#1b1b1f"
SOFT = "#4a4a55"
MUTE = "#6b6b76"
ACCENT = "#b45309"
PAPER = "#fdfcfa"
BAND = "#f0ece6"
GOOD = "#1f6b3f"

MAX_COLUMNS = 3
MAX_ROWS = 4

VARIANTS = ("table", "criteria", "mistakes")


def _header(parts: list[str], comparison: dict, label: str) -> float:
    """上部の見出し。戻り値は本文の開始Y。"""
    parts.append(svg.rect(0, 0, WIDTH, 14, ACCENT))
    y = 110
    parts.append(svg.text(MARGIN, y, 27, ACCENT, label, "700"))
    y += 74
    chunk, y = svg.block(MARGIN, y, CONTENT, 48, INK,
                         comparison.get("title", ""), 1.32, 3, "700")
    parts.append(chunk)
    y += 8
    if comparison.get("keyword"):
        parts.append(svg.text(MARGIN, y, 26, MUTE, comparison["keyword"]))
        y += 50
    return y


def _footer(parts: list[str], site: dict) -> None:
    """広告表示は必ず入れる（ステマ規制）。"""
    parts.append(svg.rect(0, HEIGHT - FOOTER_H, WIDTH, FOOTER_H, BAND))
    parts.append(svg.text(MARGIN, HEIGHT - 74, 30, INK, site.get("title", ""), "700"))
    parts.append(svg.text(MARGIN, HEIGHT - 36, 23, MUTE,
                          (site.get("disclosure") or {}).get("short", "")))
    parts.append(svg.text(WIDTH - MARGIN, HEIGHT - 74, 26, ACCENT, "#PR", "700", "end"))


def _table(parts: list[str], comparison: dict, y: float) -> float:
    columns = (comparison.get("table_columns") or [])[:MAX_COLUMNS]
    entries = (comparison.get("entries") or [])[:MAX_ROWS]
    if not (columns and entries):
        return y

    name_w = CONTENT * 0.40
    col_w = (CONTENT - name_w) / len(columns)
    header_h, row_h = 62, 100
    best = comparison.get("best_cells") or {}

    parts.append(svg.rect(MARGIN, y, CONTENT, header_h, BAND))
    parts.append(svg.text(MARGIN + 16, y + 40, 24, SOFT, "商品", "700"))
    for i, col in enumerate(columns):
        parts.append(svg.text(MARGIN + name_w + col_w * i + 12, y + 40, 24, SOFT,
                              svg.fit(col, col_w - 24, 24), "700"))
    y += header_h

    for entry in entries:
        product = entry["product"]
        parts.append(svg.line(MARGIN, y, WIDTH - MARGIN, y, "#e0dbd3"))
        chunk, _ = svg.block(MARGIN + 16, y + 40, name_w - 28, 24, INK,
                             product.get("title", ""), 1.33, 2)
        parts.append(chunk)
        for i, col in enumerate(columns):
            value = str((product.get("specs") or {}).get(col, "-"))
            x = MARGIN + name_w + col_w * i + 12
            is_best = product["slug"] in (best.get(col, {}).get("slugs") or [])
            parts.append(svg.text(x, y + 40, 24, GOOD if is_best else "#3b3b44",
                                  svg.fit(value, col_w - 24, 24), "700" if is_best else ""))
            if is_best:
                parts.append(svg.text(x, y + 70, 19, GOOD, best[col]["label"], "700"))
        y += row_h
    parts.append(svg.line(MARGIN, y, WIDTH - MARGIN, y, "#e0dbd3"))
    return y + 50


def _situations(parts: list[str], comparison: dict, y: float) -> float:
    situations = comparison.get("situation_entries") or []
    if not situations or y > HEIGHT - FOOTER_H - 200:
        return y
    parts.append(svg.text(MARGIN, y, 32, INK, "状況別のおすすめ", "700"))
    y += 52
    for s in situations[:3]:
        if y > HEIGHT - FOOTER_H - 70:
            break
        parts.append(svg.text(MARGIN, y, 26, SOFT,
                              svg.fit("・" + s.get("situation", ""), CONTENT, 26)))
        y += 36
        parts.append(svg.text(MARGIN + 30, y, 26, ACCENT,
                              svg.fit("→ " + s["product"].get("title", ""), CONTENT - 30, 26), "600"))
        y += 46
    return y


def _criteria(parts: list[str], comparison: dict, y: float) -> float:
    for i, criterion in enumerate(comparison.get("criteria") or [], 1):
        if y > HEIGHT - FOOTER_H - 150:
            break
        parts.append(svg.rect(MARGIN, y - 30, 44, 44, BAND, 22))
        parts.append(svg.text(MARGIN + 22, y + 1, 24, ACCENT, str(i), "700", "middle"))
        chunk, y = svg.block(MARGIN + 62, y, CONTENT - 62, 32, INK,
                             criterion.get("name", ""), 1.35, 2, "700")
        parts.append(chunk)
        y += 8
        chunk, y = svg.block(MARGIN + 62, y, CONTENT - 62, 25, SOFT,
                             criterion.get("why", ""), 1.5, 3)
        parts.append(chunk)
        y += 10
        if criterion.get("check") and y < HEIGHT - FOOTER_H - 130:
            box_top = y - 24
            chunk, after = svg.block(MARGIN + 78, y + 6, CONTENT - 110, 23, MUTE,
                                     "確認のしかた：" + criterion["check"], 1.5, 3)
            parts.append(svg.rect(MARGIN + 62, box_top, CONTENT - 62, after - box_top - 6, BAND, 10))
            parts.append(chunk)
            y = after
        y += 34
    return y


def _mistakes(parts: list[str], comparison: dict, y: float) -> float:
    complaints = (comparison.get("complaints") or [])[:4]
    # 件数が少ないときは1件あたりを大きく見せる
    size = 34 if len(complaints) <= 3 else 31
    for complaint in complaints:
        if y > HEIGHT - FOOTER_H - 150:
            break
        chunk, y = svg.block(MARGIN, y, CONTENT, size, INK,
                             "「" + complaint.get("complaint", "") + "」", 1.4, 2, "700")
        parts.append(chunk)
        y += 14
        box_top = y - 28
        chunk, after = svg.block(MARGIN + 20, y + 6, CONTENT - 40, size - 5, SOFT,
                                 "→ " + complaint.get("fix", ""), 1.55, 4)
        parts.append(svg.rect(MARGIN, box_top, CONTENT, after - box_top - 4, BAND, 12))
        parts.append(chunk)
        y = after + 46
    return y


def render(comparison: dict, site: dict, variant: str = "table") -> str:
    parts = svg.open_svg(WIDTH, HEIGHT, PAPER)

    if variant == "criteria":
        y = _header(parts, comparison, f"選ぶときに見る{len(comparison.get('criteria') or [])}つ")
        _criteria(parts, comparison, y + 10)
    elif variant == "mistakes":
        y = _header(parts, comparison, "よくある失敗と、その対処")
        _mistakes(parts, comparison, y + 10)
    else:
        y = _header(parts, comparison, f"{comparison.get('count', 0)}商品を比較")
        y = _table(parts, comparison, y)
        _situations(parts, comparison, y)

    _footer(parts, site)
    parts.append("</svg>")
    return "\n".join(parts)
