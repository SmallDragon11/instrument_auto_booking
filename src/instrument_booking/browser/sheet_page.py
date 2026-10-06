"""試算表網頁的最小操作介面，以及以 Playwright 實作的版本（細節見 docs/spike-report.md）。"""
from __future__ import annotations

import functools
from typing import Protocol

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from instrument_booking.core.job import WriterCrashed, WriterError

NAME_BOX = "#t-name-box"
ACTIVE_SHEET_TAB = ".docs-sheet-active-tab .docs-sheet-tab-name"  # 已於測試副本實測（2026-10-06）

READ_HTML_JS = """async () => {
  const items = await navigator.clipboard.read();
  for (const it of items) {
    if (it.types.includes('text/html')) return await (await it.getType('text/html')).text();
  }
  return null;
}"""

WRITE_JS = """async ([html, text]) => {
  const item = {'text/plain': new Blob([text], {type: 'text/plain'})};
  if (html) item['text/html'] = new Blob([html], {type: 'text/html'});
  await navigator.clipboard.write([new ClipboardItem(item)]);
}"""


class SheetPage(Protocol):
    """BrowserSheetWriter 需要的網頁操作。失敗時拋 WriterError（可繼續）或 WriterCrashed（需重啟）。"""

    def jump(self, ref: str) -> None:
        """在名稱方塊輸入 ref（例 "'202610'!B10:B13"）並按 Enter。"""
        ...

    def name_box(self) -> str:
        """名稱方塊目前顯示的內容（例 "B10:B13"）。"""
        ...

    def active_sheet(self) -> str:
        """目前作用中的工作表名稱（工作表分頁上的文字）。"""
        ...

    def press(self, keys: str) -> None:
        """送出按鍵（例 "Control+C"）。"""
        ...

    def read_clipboard_html(self) -> str | None: ...

    def write_clipboard(self, html: str | None, text: str) -> None:
        """寫入剪貼簿；html 為 None 時只寫純文字（用來清空）。"""
        ...

    def wait(self, ms: int) -> None: ...


def _translate(fn):
    """Playwright 逾時 → WriterError；其他 Playwright 錯誤（頁面關閉、瀏覽器當掉）→ WriterCrashed。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PlaywrightTimeoutError as e:
            raise WriterError(f"瀏覽器操作逾時：{e}") from e
        except PlaywrightError as e:
            raise WriterCrashed(f"瀏覽器異常：{e}") from e

    return wrapper


class PlaywrightSheetPage:
    def __init__(self, page) -> None:
        self._page = page

    @_translate
    def jump(self, ref: str) -> None:
        box = self._page.locator(NAME_BOX)
        box.click()
        box.fill(ref)
        box.press("Enter")

    @_translate
    def name_box(self) -> str:
        return self._page.locator(NAME_BOX).input_value()

    @_translate
    def active_sheet(self) -> str:
        return self._page.locator(ACTIVE_SHEET_TAB).first.inner_text().strip()

    @_translate
    def press(self, keys: str) -> None:
        self._page.keyboard.press(keys)

    @_translate
    def read_clipboard_html(self) -> str | None:
        return self._page.evaluate(READ_HTML_JS)

    @_translate
    def write_clipboard(self, html: str | None, text: str) -> None:
        self._page.evaluate(WRITE_JS, [html or "", text])

    @_translate
    def wait(self, ms: int) -> None:
        self._page.wait_for_timeout(ms)
