"""Google 試算表剪貼簿 HTML 的解析與改寫（格式見 docs/spike-report.md「剪貼簿 HTML 格式」）。"""
from __future__ import annotations

import html as html_lib
import re
from typing import Sequence

from instrument_booking.core.models import CellFont, CellState

_TR = re.compile(r"<tr\b[^>]*>.*?</tr>", re.S)
_TD = re.compile(r"<td\b([^>]*)>(.*?)</td>", re.S)
_TD_OPEN = re.compile(r"<td\b", re.I)
_STYLE = re.compile(r'(?<!\S)style="([^"]*)"')  # 必須是完整的 style 屬性（不可匹配 data-x-style=）
_TAG = re.compile(r"<[^>]+>")
_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")
_RGB_IN = re.compile(r"rgba?\([^)]*\)")
_NO_FILL = {"none", "transparent", "initial", "unset"}
_REWRITTEN = ("background-color", "background", "font-size", "font-weight", "text-align")


class ClipboardFormatError(ValueError):
    """剪貼簿內容不是預期的 Google 試算表表格（例如單格 <span> 或空白）。"""


def parse_rgb(css_value: str | None) -> str | None:
    """'rgb(164, 194, 244)' → 'A4C2F4'；白色、透明或未設定 → None；認不得的格式 → 'unknown'。"""
    if css_value is None or not css_value.strip() or css_value.strip() == "transparent":
        return None
    m = _RGB.fullmatch(css_value.strip())
    if not m:
        return "unknown"
    if m.group(4) is not None and float(m.group(4)) == 0:
        return None
    rgb = "".join(f"{int(m.group(i)):02X}" for i in (1, 2, 3))
    return None if rgb == "FFFFFF" else rgb


def _style_value(style: str, prop: str) -> str | None:
    for decl in style.split(";"):
        key, _, value = decl.partition(":")
        if key.strip().lower() == prop:
            return value.strip()
    return None


def _background(style: str) -> str | None:
    """底色：優先 background-color；沒有時改讀 background 簡寫中的 rgb(...)／rgba(...)。

    兩者都沒有時回傳 None（無填色）——實測 Google 對無填色格的 <td> 不輸出任何底色樣式。
    簡寫中沒有 rgb() 且不是 none／transparent 等無填色值（例如 #a4c2f4、漸層）→ 'unknown'。
    """
    color = _style_value(style, "background-color")
    if color is not None:
        return parse_rgb(color)
    shorthand = _style_value(style, "background")
    if shorthand is None:
        return None
    m = _RGB_IN.search(shorthand)
    if m:
        return parse_rgb(m.group(0))
    if not shorthand or all(token.lower() in _NO_FILL for token in shorthand.split()):
        return None
    return "unknown"


def _rows(html: str) -> list[tuple[str, str]]:
    """每列唯一一個 <td> 的 (屬性字串, 內容)。不是單欄表格就拋 ClipboardFormatError。"""
    if "<table" not in html:
        raise ClipboardFormatError("剪貼簿內容不是表格")
    rows = []
    for tr in _TR.findall(html):
        m = _TD.search(tr)
        if m is None:
            raise ClipboardFormatError("表格列中沒有儲存格")
        if len(_TD_OPEN.findall(tr)) != 1:
            raise ClipboardFormatError("表格列中有多個儲存格（只支援單欄範圍）")
        rows.append((m.group(1), m.group(2)))
    if not rows:
        raise ClipboardFormatError("表格沒有任何列")
    return rows


def count_rows(html: str | None) -> int:
    """表格列數；不是表格（含 None）回傳 0。"""
    if not html:
        return 0
    try:
        return len(_rows(html))
    except ClipboardFormatError:
        return 0


def parse_cells(html: str) -> list[CellState]:
    """解析每一列的文字與底色。"""
    cells = []
    for attrs, inner in _rows(html):
        m = _STYLE.search(attrs)
        style = html_lib.unescape(m.group(1)) if m else ""
        text = html_lib.unescape(_TAG.sub("", inner)).strip()
        cells.append(CellState(value=text or None, color=_background(style)))
    return cells


def drop_last_row(html: str) -> str:
    """刪除表格最後一列（單格範圍多讀一列時使用）。"""
    trs = list(_TR.finditer(html))
    if len(trs) < 2:
        raise ClipboardFormatError("表格少於 2 列，無法刪除最後一列")
    last = trs[-1]
    return html[:last.start()] + html[last.end():]


def _font_css(font: CellFont) -> str:
    parts = []
    if font.size is not None:
        parts.append(f"font-size: {font.size:g}pt;")
    parts.append(f"font-weight: {'bold' if font.bold else 'normal'};")
    if font.h_align:
        parts.append(f"text-align: {font.h_align};")
    return " ".join(parts)


def _rewrite_style(style: str, color: str, font: CellFont) -> str:
    kept = [d.strip() for d in style.split(";")
            if d.strip() and d.partition(":")[0].strip().lower() not in _REWRITTEN]
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    base = "; ".join(kept)
    return f"{base}; background-color: rgb({r}, {g}, {b}); {_font_css(font)}".lstrip("; ")


def build_booking_html(copied_html: str, *, color: str, name: str, fonts: Sequence[CellFont]) -> str:
    """以剛複製的目標範圍 HTML 為底稿：每格改為 color、逐格寫回字型、第一格填 name、其餘清空。

    框線、間距等其他樣式原樣保留。列數與 fonts 數量不符時拋 ClipboardFormatError。
    """
    rows = _rows(copied_html)
    if len(rows) != len(fonts):
        raise ClipboardFormatError(f"表格有 {len(rows)} 列，但字型有 {len(fonts)} 筆")
    index = 0

    def repl(m: re.Match) -> str:
        nonlocal index
        attrs = m.group(1)
        sm = _STYLE.search(attrs)
        style = html_lib.unescape(sm.group(1)) if sm else ""
        new_style = html_lib.escape(_rewrite_style(style, color, fonts[index]), quote=True)
        attrs = _STYLE.sub(lambda _: f'style="{new_style}"', attrs, count=1) if sm else f'{attrs} style="{new_style}"'
        content = html_lib.escape(name) if index == 0 else ""
        index += 1
        return f"<td{attrs}>{content}</td>"

    out = []
    pos = 0
    for tr in _TR.finditer(copied_html):
        out.append(copied_html[pos:tr.start()])
        out.append(_TD.sub(repl, tr.group(0), count=1))
        pos = tr.end()
    out.append(copied_html[pos:])
    return "".join(out)
