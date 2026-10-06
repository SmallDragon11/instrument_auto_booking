"""以瀏覽器實作 core.job.SheetWriter：Ctrl+C 讀取、以讀取到的 HTML 為底稿 Ctrl+V 寫入（規格 §7、§9）。"""
from __future__ import annotations

import time
from typing import Callable, Sequence

from openpyxl.utils import get_column_letter, range_boundaries

from instrument_booking.browser.sheet_page import SheetPage
from instrument_booking.core.clipboard_html import (
    ClipboardFormatError,
    build_booking_html,
    count_rows,
    drop_first_row,
    drop_last_row,
    is_sheets_table,
    parse_cells,
)
from instrument_booking.core.job import EarlyWriteError, TimeSource, WriterCrashed, WriterError
from instrument_booking.core.models import CellFont, CellState

LOAD_WAIT_MS = 300        # 切換工作表後等待載入（Spike：約 0.3 秒）
RETRY_WAIT_MS = 150       # 讀取不正確時，重試前等待
CLIPBOARD_POLLS = 20      # Ctrl+C 後輪詢剪貼簿的次數
CLIPBOARD_POLL_MS = 30


def sheet_ref(sheet: str, a1: str) -> str:
    """名稱方塊的參照：工作表名稱以 ' 包住，名稱中的 ' 寫成 ''（Google 試算表的跳脫規則）。"""
    return "'" + sheet.replace("'", "''") + "'!" + a1


def _same_sheet(active: str, sheet: str) -> bool:
    """分頁上顯示的名稱不含前後空白（實際有 '1月oven2023 ' 這種名稱），兩邊都去掉後比較。"""
    return active.strip() == sheet.strip()


def _same_content(check: str | None, html: str) -> bool:
    """剪貼簿是否仍是我們剛放入的內容：Google 表格，且每一格的文字與底色都相同。"""
    if not is_sheets_table(check):
        return False
    try:
        return parse_cells(check) == parse_cells(html)
    except ClipboardFormatError:
        return False


class _SelectionExpanded(Exception):
    """名稱方塊顯示的範圍列相同、但欄位被擴張（選取碰到合併儲存格，例 U20:U21 → T20:V21）。"""


def _widened(box: str, a1: str) -> bool:
    """box 是否為 a1 列相同、欄位向左右擴張後的範圍。"""
    try:
        bounds = range_boundaries(box)
    except ValueError:
        return False
    if None in bounds:
        return False
    bc1, br1, bc2, br2 = bounds
    c1, r1, c2, r2 = range_boundaries(a1)
    return (br1, br2) == (r1, r2) and bc1 <= c1 and c2 <= bc2 and (bc1, bc2) != (c1, c2)


class BrowserSheetWriter:
    """遵守 core.job.SheetWriter 的約定；所有網頁操作透過 SheetPage。"""

    def __init__(self, page: SheetPage, *, restart: Callable[[], SheetPage], clock: TimeSource,
                 not_before: float, monotonic: Callable[[], float] = time.monotonic,
                 read_timeout: float = 3.0) -> None:
        self._page = page
        self._restart = restart
        self._clock = clock
        self._not_before = not_before
        self._monotonic = monotonic
        self._read_timeout = read_timeout
        self._draft: tuple[str, str, str] | None = None  # (sheet, a1, 剛複製的 HTML)
        self._loaded_sheet: str | None = None  # 已載入完成的工作表（切換後需等待載入）

    def prewarm(self, sheets: Sequence[str]) -> None:
        for sheet in sheets:
            self._page.jump(sheet_ref(sheet, "A1"))
            self._page.wait(LOAD_WAIT_MS)
            self._loaded_sheet = sheet

    def read_range(self, sheet: str, a1: str) -> list[CellState]:
        self._draft = None
        col1, row1, col2, row2 = range_boundaries(a1)
        if col1 != col2:
            raise WriterError(f"只支援單欄範圍：{a1}")
        try:
            if row1 == row2:
                html = self._copy_single(sheet, get_column_letter(col1), row1)
            else:
                html = self._copy(sheet, a1, row2 - row1 + 1)
            cells = parse_cells(html)
        except _SelectionExpanded as e:
            raise WriterError(f"選取 '{sheet}'!{e} 時被合併儲存格擴張，無法讀取 {a1}") from e
        except ClipboardFormatError as e:
            raise WriterError(f"無法解析 '{sheet}'!{a1} 的內容：{e}") from e
        self._draft = (sheet, a1, html)
        return cells

    def _copy_single(self, sheet: str, col: str, row: int) -> str:
        """單格複製只會得到沒有底色的 <span> → 多選一列（2 列表格），再刪掉多讀的那一列。

        先選「本格＋下一列」；若下一列是合併儲存格（例如當天 23:00 那格的下一列是下一週的日期列，
        選取會被擴張成整天三欄），立刻改選「上一列＋本格」，讀取後刪除第一列。
        """
        try:
            return drop_last_row(self._copy(sheet, f"{col}{row}:{col}{row + 1}", 2))
        except _SelectionExpanded:
            if row == 1:
                raise WriterError(f"'{sheet}'!{col}{row} 的下一列是合併儲存格，且沒有上一列可多讀") from None
        return drop_first_row(self._copy(sheet, f"{col}{row - 1}:{col}{row}", 2))

    def paste_booking(self, sheet: str, a1: str, color: str, name: str, fonts: Sequence[CellFont]) -> None:
        draft, self._draft = self._draft, None  # 底稿只能用一次：任何結果（含拒絕）都先取走
        if self._clock.now() < self._not_before:
            raise EarlyWriteError("時間未到，拒絕貼上")
        if draft is None or draft[:2] != (sheet, a1):
            raise WriterError(f"'{sheet}'!{a1} 沒有對應的即時讀取底稿，拒絕貼上")
        try:
            html = build_booking_html(draft[2], color=color, name=name, fonts=fonts)
        except ClipboardFormatError as e:
            raise WriterError(f"無法組出貼上內容：{e}") from e
        first = a1.split(":")[0]
        self._page.write_clipboard(html, name + "\n" * (len(fonts) - 1))
        self._page.jump(sheet_ref(sheet, first))
        box, active = self._page.name_box(), self._page.active_sheet()
        if box != first or not _same_sheet(active, sheet):
            raise WriterError(f"選取位置錯誤（{active}!{box}），放棄貼上")
        if not _same_content(self._page.read_clipboard_html(), html):
            raise WriterError("剪貼簿內容在貼上前被改變，放棄貼上")
        try:
            self._page.press("Control+V")
        except WriterError as e:
            # 已送出 Ctrl+V：無法確定是否已貼上，必須交給 recover 與驗證判斷
            raise WriterCrashed(f"貼上時發生錯誤，無法確定是否已貼上：{e}") from e

    def recover(self) -> None:
        self._draft = None
        self._loaded_sheet = None
        self._page = self._restart()

    def _copy(self, sheet: str, a1: str, rows: int) -> str:
        """跳轉並複製；確認名稱方塊正確、且複製到列數正確的 Google 表格才回傳，否則重試到逾時拋 WriterError。"""
        deadline = self._monotonic() + self._read_timeout
        while True:
            self._page.write_clipboard(None, "")  # 先清空，避免讀到舊內容
            self._page.jump(sheet_ref(sheet, a1))
            box = self._page.name_box() if _same_sheet(self._page.active_sheet(), sheet) else None
            if box is not None and _widened(box, a1):
                raise _SelectionExpanded(a1)  # 再試也一樣：交給呼叫端立刻改選其他範圍
            if box == a1:
                if sheet != self._loaded_sheet:
                    self._page.wait(LOAD_WAIT_MS)  # 剛切換工作表：等表格載入後再複製
                    self._loaded_sheet = sheet
                self._page.press("Control+C")
                html = self._poll_clipboard()
                if is_sheets_table(html) and count_rows(html) == rows:
                    return html
            if self._monotonic() >= deadline:
                raise WriterError(f"無法正確讀取 '{sheet}'!{a1}")
            self._page.wait(RETRY_WAIT_MS)

    def _poll_clipboard(self) -> str | None:
        for _ in range(CLIPBOARD_POLLS):
            html = self._page.read_clipboard_html()
            if html:
                return html
            self._page.wait(CLIPBOARD_POLL_MS)
        return None
