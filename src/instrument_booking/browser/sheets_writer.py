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
    drop_last_row,
    parse_cells,
)
from instrument_booking.core.job import EarlyWriteError, TimeSource, WriterCrashed, WriterError
from instrument_booking.core.models import CellFont, CellState

LOAD_WAIT_MS = 300        # 切換工作表後等待載入（Spike：約 0.3 秒）
RETRY_WAIT_MS = 150       # 讀取不正確時，重試前等待
CLIPBOARD_POLLS = 20      # Ctrl+C 後輪詢剪貼簿的次數
CLIPBOARD_POLL_MS = 30


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
            self._page.jump(f"'{sheet}'!A1")
            self._page.wait(LOAD_WAIT_MS)
            self._loaded_sheet = sheet

    def read_range(self, sheet: str, a1: str) -> list[CellState]:
        self._draft = None
        col1, row1, col2, row2 = range_boundaries(a1)
        if col1 != col2:
            raise WriterError(f"只支援單欄範圍：{a1}")
        rows = row2 - row1 + 1
        col = get_column_letter(col1)
        # 單格複製只會得到沒有底色的 <span> → 多選下一列，之後再刪掉
        copy_a1 = a1 if rows > 1 else f"{col}{row1}:{col}{row1 + 1}"
        html = self._copy(sheet, copy_a1, max(rows, 2))
        try:
            if rows == 1:
                html = drop_last_row(html)
            cells = parse_cells(html)
        except ClipboardFormatError as e:
            raise WriterError(f"無法解析 '{sheet}'!{a1} 的內容：{e}") from e
        self._draft = (sheet, a1, html)
        return cells

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
        self._page.jump(f"'{sheet}'!{first}")
        box, active = self._page.name_box(), self._page.active_sheet()
        if box != first or active != sheet:
            raise WriterError(f"選取位置錯誤（{active}!{box}），放棄貼上")
        check = self._page.read_clipboard_html()
        if count_rows(check) != len(fonts) or parse_cells(check)[0].value != name.strip():
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
        """跳轉並複製；確認名稱方塊與表格列數都正確才回傳，否則重試到逾時拋 WriterError。"""
        deadline = self._monotonic() + self._read_timeout
        while True:
            self._page.write_clipboard(None, "")  # 先清空，避免讀到舊內容
            self._page.jump(f"'{sheet}'!{a1}")
            if self._page.active_sheet() == sheet and self._page.name_box() == a1:
                if sheet != self._loaded_sheet:
                    self._page.wait(LOAD_WAIT_MS)  # 剛切換工作表：等表格載入後再複製
                    self._loaded_sheet = sheet
                self._page.press("Control+C")
                html = self._poll_clipboard()
                if html is not None and count_rows(html) == rows:
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
