"""Pinterest 用の比較表画像（SVG）を作る。

Pinterest は縦長の画像が伸びやすいため、2:3（1000x1500）で出力する。
比較表をそのまま画像にしておくと、検索とは別の流入経路になる。

rsvg-convert が入っていれば build_site.py が PNG にも変換する
（Pinterest は SVG のアップロードを受け付けないため）。
"""

from __future__ import annotations

from xml.sax.saxutils import escape

WIDTH, HEIGHT = 1000, 1500
MARGIN = 60
CONTENT = WIDTH - MARGIN * 2
FONT = "Noto Sans JP, Hiragino Kaku Gothic ProN, sans-serif"
FOOTER_H = 120
# 比較表に載せる列数。多いと1列あたりが狭くなって読めなくなる
MAX_COLUMNS = 3
MAX_ROWS = 4


def _fit(text: str, px: int, font_size: int) -> str:
    """指定した幅に収まる文字数で切る。全角は font_size とほぼ同じ幅として扱う。"""
    limit = max(int(px / (font_size * 0.98)), 1)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _wrap(text: str, px: int, font_size: int, max_lines: int) -> list[str]:
    """日本語は単語境界が無いので、幅から求めた文字数で折り返す。"""
    per_line = max(int(px / (font_size * 0.98)), 1)
    text = " ".join(str(text).split())
    lines = [text[i:i + per_line] for i in range(0, len(text), per_line)] or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _text(x: float, y: float, size: int, fill: str, content: str, weight: str = "") -> str:
    bold = f' font-weight="{weight}"' if weight else ""
    return (f'<text x="{x:.0f}" y="{y:.0f}" font-size="{size}" fill="{fill}"{bold}>'
            f"{escape(content)}</text>")


def render(comparison: dict, site: dict) -> str:
    columns = (comparison.get("table_columns") or [])[:MAX_COLUMNS]
    entries = (comparison.get("entries") or [])[:MAX_ROWS]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="{FONT}">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#fdfcfa"/>',
        f'<rect width="{WIDTH}" height="14" fill="#b45309"/>',
    ]
    y = 128

    for line in _wrap(comparison.get("title", ""), CONTENT, 50, 3):
        parts.append(_text(MARGIN, y, 50, "#1b1b1f", line, "700"))
        y += 64
    y += 6

    if comparison.get("keyword"):
        parts.append(_text(MARGIN, y, 27, "#8a6a3a", comparison["keyword"]))
        y += 54

    # ── 比較表 ─────────────────────────────
    if columns and entries:
        name_w = CONTENT * 0.40
        col_w = (CONTENT - name_w) / len(columns)
        header_h, row_h = 62, 100

        parts.append(f'<rect x="{MARGIN}" y="{y}" width="{CONTENT}" height="{header_h}" fill="#f0ece6"/>')
        parts.append(_text(MARGIN + 16, y + 40, 24, "#5b5b66", "商品", "700"))
        for i, col in enumerate(columns):
            parts.append(_text(
                MARGIN + name_w + col_w * i + 12, y + 40, 24, "#5b5b66",
                _fit(col, col_w - 24, 24), "700",
            ))
        y += header_h

        for entry in entries:
            product = entry["product"]
            parts.append(
                f'<line x1="{MARGIN}" y1="{y:.0f}" x2="{WIDTH - MARGIN}" y2="{y:.0f}" '
                f'stroke="#e0dbd3" stroke-width="2"/>'
            )
            for j, line in enumerate(_wrap(product.get("title", ""), name_w - 28, 24, 2)):
                parts.append(_text(MARGIN + 16, y + 40 + j * 32, 24, "#1b1b1f", line))
            for i, col in enumerate(columns):
                value = (product.get("specs") or {}).get(col, "-")
                parts.append(_text(
                    MARGIN + name_w + col_w * i + 12, y + 40, 24, "#3b3b44",
                    _fit(value, col_w - 24, 24),
                ))
            y += row_h
        parts.append(
            f'<line x1="{MARGIN}" y1="{y:.0f}" x2="{WIDTH - MARGIN}" y2="{y:.0f}" '
            f'stroke="#e0dbd3" stroke-width="2"/>'
        )
        y += 56

    # ── 選ぶときに見る3つ ───────────────────
    criteria = comparison.get("criteria") or []
    if criteria and y < HEIGHT - FOOTER_H - 200:
        parts.append(_text(MARGIN, y, 32, "#1b1b1f", f"選ぶときに見る{len(criteria[:3])}つ", "700"))
        y += 54
        for i, criterion in enumerate(criteria[:3], 1):
            if y > HEIGHT - FOOTER_H - 90:
                break
            for j, line in enumerate(_wrap(f"{i}. {criterion.get('name', '')}", CONTENT, 28, 2)):
                parts.append(_text(MARGIN, y, 28, "#1b1b1f", line, "600" if j == 0 else ""))
                y += 38
            for line in _wrap(criterion.get("why", ""), CONTENT - 30, 24, 2):
                parts.append(_text(MARGIN + 30, y, 24, "#6b6b76", line))
                y += 32
            y += 14

    # ── 状況別のおすすめ（余白が残っていれば入れる）───
    situations = comparison.get("situation_entries") or []
    if situations and y < HEIGHT - FOOTER_H - 170:
        y += 14
        parts.append(_text(MARGIN, y, 32, "#1b1b1f", "状況別のおすすめ", "700"))
        y += 52
        for s_entry in situations[:3]:
            if y > HEIGHT - FOOTER_H - 60:
                break
            parts.append(_text(MARGIN, y, 26, "#3b3b44",
                               _fit(f"・{s_entry.get('situation', '')}", CONTENT, 26)))
            y += 36
            parts.append(_text(MARGIN + 30, y, 26, "#b45309",
                               _fit(f"→ {s_entry['product'].get('title', '')}", CONTENT - 30, 26), "600"))
            y += 46

    parts.append(f'<rect x="0" y="{HEIGHT - FOOTER_H}" width="{WIDTH}" height="{FOOTER_H}" fill="#f0ece6"/>')
    parts.append(_text(MARGIN, HEIGHT - 66, 30, "#1b1b1f", site.get("title", ""), "700"))
    parts.append(_text(MARGIN, HEIGHT - 30, 22, "#6b6b76",
                       (site.get("disclosure") or {}).get("short", "")))
    parts.append("</svg>")
    return "\n".join(parts)
