"""SVGを組み立てるための共通部品。

Pinterest用（1000x1500）とInstagram用（1080x1350）で使い回す。
日本語は単語境界がないので、折り返しは文字数で概算している。
"""

from __future__ import annotations

from xml.sax.saxutils import escape

FONT = "Noto Sans JP, Hiragino Sans, Hiragino Kaku Gothic ProN, sans-serif"

# 全角1文字がフォントサイズの何倍の幅になるかの概算
CHAR_RATIO = 0.98


def chars_per_line(px: float, size: float) -> int:
    return max(int(px / (size * CHAR_RATIO)), 1)


def fit(text: str, px: float, size: float) -> str:
    """指定幅に収まる長さに切り、あふれたら「…」を付ける。"""
    limit = chars_per_line(px, size)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def wrap(text: str, px: float, size: float, max_lines: int = 99) -> list[str]:
    """文字数で折り返す。max_lines を超えた分は最終行に「…」を付けて捨てる。"""
    per_line = chars_per_line(px, size)
    text = " ".join(str(text).split())
    lines = [text[i:i + per_line] for i in range(0, len(text), per_line)] or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def text(x: float, y: float, size: float, fill: str, content: str,
         weight: str = "", anchor: str = "") -> str:
    attrs = f' font-weight="{weight}"' if weight else ""
    attrs += f' text-anchor="{anchor}"' if anchor else ""
    return (f'<text x="{x:.0f}" y="{y:.0f}" font-size="{size:.0f}" fill="{fill}"{attrs}>'
            f"{escape(str(content))}</text>")


def block(x: float, y: float, px: float, size: float, fill: str, content: str,
          line_height: float = 1.45, max_lines: int = 99, weight: str = "") -> tuple[str, float]:
    """折り返したテキストを描く。描画結果と、次のY座標を返す。"""
    parts = []
    step = size * line_height
    for line in wrap(content, px, size, max_lines):
        parts.append(text(x, y, size, fill, line, weight))
        y += step
    return "\n".join(parts), y


def rect(x: float, y: float, w: float, h: float, fill: str, radius: float = 0) -> str:
    r = f' rx="{radius:.0f}"' if radius else ""
    return f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" fill="{fill}"{r}/>'


def line(x1: float, y1: float, x2: float, y2: float, stroke: str, width: float = 2) -> str:
    return (f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="{stroke}" stroke-width="{width}"/>')


def open_svg(width: int, height: int, background: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{FONT}">',
        rect(0, 0, width, height, background),
    ]
