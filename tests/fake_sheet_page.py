"""模擬 Google 試算表網頁的 SheetPage：名稱方塊跳轉、Ctrl+C 產生與 Google 相同格式的 HTML、Ctrl+V 寫回。"""
from __future__ import annotations

import html as html_lib
import re

from openpyxl.utils import get_column_letter, range_boundaries

from instrument_booking.core.clipboard_html import parse_cells
from instrument_booking.core.models import CellState

_REF = re.compile(r"^'(?P<sheet>[^']+)'!(?P<a1>[A-Z]+\d+(?::[A-Z]+\d+)?)$")

WRAPPER_OPEN = ('<google-sheets-html-origin style="color: rgb(0, 0, 0); font-size: medium;">'
                '<table xmlns="http://www.w3.org/1999/xhtml" cellspacing="0" cellpadding="0" dir="ltr" border="1" '
                'data-sheets-root="1" data-sheets-baot="1" style="table-layout: fixed; font-size: 10pt; '
                'font-family: Arial;"><colgroup><col width="46"></colgroup><tbody>')
WRAPPER_CLOSE = "</tbody></table></google-sheets-html-origin>"
SPAN = ('<span data-sheets-root="1" style="font-size: 12pt; font-family: Arial; font-weight: bold; '
        'color: rgb(255, 0, 0);"></span>')


def google_html(states: list[CellState]) -> str:
    """產生與 Google 試算表相同結構的多格複製 HTML（白色以 rgb(255, 255, 255) 表示）。"""
    rows = []
    for s in states:
        rgb = s.color or "FFFFFF"
        r, g, b = (int(rgb[i:i + 2], 16) for i in (0, 2, 4))
        style = (f"border-width: 1px; border-style: solid; overflow: hidden; padding: 0px 3px; "
                 f"vertical-align: bottom; background-color: rgb({r}, {g}, {b});")
        if s.value not in (None, ""):
            style += " font-size: 12pt; font-weight: bold; text-align: center;"
        text = html_lib.escape(str(s.value)) if s.value not in (None, "") else ""
        rows.append(f'<tr style="height: 21px;"><td style="{style}">{text}</td></tr>')
    return WRAPPER_OPEN + "".join(rows) + WRAPPER_CLOSE


def cell_names(a1: str) -> list[str]:
    c1, r1, c2, r2 = range_boundaries(a1)
    return [f"{get_column_letter(c)}{r}" for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


class FakeSheetPage:
    def __init__(self) -> None:
        self.cells: dict[tuple[str, str], CellState] = {}
        self.selection: tuple[str, str] | None = None
        self.clipboard_html: str | None = None
        self.clipboard_text = ""
        self.pasted: list[tuple[str, str, str]] = []  # (sheet, 第一格, html)
        self.log: list[tuple] = []
        self.elapsed = 0.0        # 假時間（秒），只由 wait() 推進
        self.stale_copies = 0     # 接下來幾次 Ctrl+C 只複製到單格 <span>（模擬跳轉尚未生效）
        self.name_box_lag = 0     # 接下來幾次 name_box() 回傳舊位置
        self.active_lag = 0       # 接下來幾次 active_sheet() 回傳「切換中」（模擬工作表尚未切換完成）
        self.fail: dict[str, Exception] = {}  # 操作名稱 → 下一次呼叫時拋出的例外

    def now(self) -> float:
        return self.elapsed

    def _maybe_fail(self, op: str) -> None:
        exc = self.fail.pop(op, None)
        if exc is not None:
            raise exc

    def jump(self, ref: str) -> None:
        self._maybe_fail("jump")
        m = _REF.match(ref)
        assert m, f"名稱方塊格式錯誤：{ref}"
        self.selection = (m["sheet"], m["a1"])
        self.log.append(("jump", ref))

    def name_box(self) -> str:
        if self.name_box_lag:
            self.name_box_lag -= 1
            return "A1"
        return self.selection[1] if self.selection else ""

    def active_sheet(self) -> str:
        if self.active_lag:
            self.active_lag -= 1
            return "（切換中）"
        return self.selection[0] if self.selection else ""

    def press(self, keys: str) -> None:
        self._maybe_fail(keys)
        self.log.append(("press", keys))
        sheet, a1 = self.selection
        if keys == "Control+C":
            names = cell_names(a1)
            if self.stale_copies:
                self.stale_copies -= 1
                self.clipboard_html = SPAN
            elif len(names) == 1:
                self.clipboard_html = SPAN  # 真實行為：單格沒有底色資訊
            else:
                self.clipboard_html = google_html([self.cells.get((sheet, n), CellState(None, None)) for n in names])
        elif keys == "Control+V":
            states = parse_cells(self.clipboard_html)
            col1, row1, _, _ = range_boundaries(a1)
            for i, s in enumerate(states):
                self.cells[(sheet, f"{get_column_letter(col1)}{row1 + i}")] = s
            self.pasted.append((sheet, a1, self.clipboard_html))

    def read_clipboard_html(self) -> str | None:
        self._maybe_fail("read_clipboard_html")
        return self.clipboard_html

    def write_clipboard(self, html: str | None, text: str) -> None:
        self._maybe_fail("write_clipboard")
        self.clipboard_html = html or None
        self.clipboard_text = text

    def wait(self, ms: int) -> None:
        self.elapsed += ms / 1000
