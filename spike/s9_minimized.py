"""驗證：Edge 最小化在背景時，剪貼簿與 Ctrl+C/V 是否仍正常、是否搶走前景視窗（throwaway）。

只操作測試副本；寫入 U63 後以 Ctrl+Z 復原。
"""
import ctypes
import time
from pathlib import Path
import os

from playwright.sync_api import sync_playwright

from instrument_booking.browser.sheet_page import NAME_BOX, PlaywrightSheetPage
from instrument_booking.browser.sheets_writer import BrowserSheetWriter
from instrument_booking.core.models import CellFont

URL = "https://docs.google.com/spreadsheets/d/1621E29JjZYnJGj1lnLgJ-GiU_bCVJCdh/edit"
PROFILE = Path(os.environ["LOCALAPPDATA"]) / "InstrumentBooking" / "edge-profile"
user32 = ctypes.windll.user32


def foreground() -> str:
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


class Clock:
    def now(self):
        return time.time()


print("啟動前前景視窗：", foreground())
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        str(PROFILE), channel="msedge", headless=False, viewport=None,
        args=["--start-minimized", "--disable-background-timer-throttling",
              "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"],
        ignore_default_args=["--enable-automation"])
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://docs.google.com")
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    window = cdp.send("Browser.getWindowForTarget")
    cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}})
    print("最小化後前景視窗：", foreground())
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_selector(NAME_BOX, timeout=60_000)
    state = cdp.send("Browser.getWindowBounds", {"windowId": window["windowId"]})["bounds"]["windowState"]
    print("載入後視窗狀態：", state, "｜前景視窗：", foreground())

    sp = PlaywrightSheetPage(page)
    writer = BrowserSheetWriter(sp, restart=lambda: sp, clock=Clock(), not_before=0.0)
    t0 = time.perf_counter()
    cells = writer.read_range("202610", "E8:E12")
    print(f"最小化時讀取 E8:E12（{time.perf_counter() - t0:.2f}s）：第一格有字＝{cells[0].value is not None}，底色＝{cells[0].color}")
    before = writer.read_range("202610", "U63:U63")
    assert before[0].value is None and before[0].color is None, "U63 不是空的，中止"
    t0 = time.perf_counter()
    writer.paste_booking("202610", "U63:U63", "A4C2F4", "MIN-TEST", [CellFont(12.0, True, "center")])
    print(f"最小化時貼上（{time.perf_counter() - t0:.2f}s）")
    page.wait_for_timeout(1500)
    after = writer.read_range("202610", "U63:U63")
    print("讀回：", after)
    sp.press("Control+Z")
    page.wait_for_timeout(1500)
    restored = writer.read_range("202610", "U63:U63")
    print("復原後：", restored, "｜前景視窗：", foreground())
    state = cdp.send("Browser.getWindowBounds", {"windowId": window["windowId"]})["bounds"]["windowState"]
    print("結束時視窗狀態：", state)
    ctx.close()
