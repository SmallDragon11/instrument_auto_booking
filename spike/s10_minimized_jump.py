"""驗證（唯讀）：最小化時改用「不點擊、直接 fill」或「強制點擊」跳轉名稱方塊，是否穩定且夠快（throwaway）。"""
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from instrument_booking.browser.sheet_page import NAME_BOX, PlaywrightSheetPage
from instrument_booking.browser.sheets_writer import BrowserSheetWriter

URL = "https://docs.google.com/spreadsheets/d/1621E29JjZYnJGj1lnLgJ-GiU_bCVJCdh/edit"
PROFILE = Path(os.environ["LOCALAPPDATA"]) / "InstrumentBooking" / "edge-profile"
RANGES = [("202610", "E8:E12"), ("10月oven2026", "B4:B6"), ("202610", "U60:U61"), ("202610", "U63:U63")]


class Clock:
    def now(self):
        return time.time()


def jump_fill(self, ref):
    box = self._page.locator(NAME_BOX)
    box.fill(ref)
    box.press("Enter")


def jump_force(self, ref):
    box = self._page.locator(NAME_BOX)
    box.click(force=True)
    box.fill(ref)
    box.press("Enter")


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        str(PROFILE), channel="msedge", headless=False, viewport=None,
        args=["--start-minimized", "--disable-background-timer-throttling",
              "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"],
        ignore_default_args=["--enable-automation"])
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://docs.google.com")
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    wid = cdp.send("Browser.getWindowForTarget")["windowId"]
    cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_selector(NAME_BOX, timeout=60_000)
    for label, fn in (("fill", jump_fill), ("force", jump_force)):
        PlaywrightSheetPage.jump = fn
        sp = PlaywrightSheetPage(page)
        writer = BrowserSheetWriter(sp, restart=lambda: sp, clock=Clock(), not_before=0.0)
        for rnd in range(3):
            for sheet, a1 in RANGES:
                t0 = time.perf_counter()
                try:
                    cells = writer.read_range(sheet, a1)
                    print(f"{label} 第{rnd + 1}輪 {sheet}!{a1}: {time.perf_counter() - t0:.2f}s 格數={len(cells)} 第一格={cells[0]}")
                except Exception as e:
                    print(f"{label} 第{rnd + 1}輪 {sheet}!{a1}: 失敗 {type(e).__name__}: {str(e)[:80]}")
    state = cdp.send("Browser.getWindowBounds", {"windowId": wid})["bounds"]["windowState"]
    print("結束時視窗狀態：", state)
    ctx.close()
