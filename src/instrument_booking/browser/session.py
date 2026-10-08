"""Edge 專屬設定檔：一般 Edge 登入一次，之後由 Playwright 沿用登入狀態（見 docs/spike-report.md 第 1 項）。

自動化執行時 Edge 最小化在背景，不搶走使用者的焦點（見 docs/spike-report.md「追加」）。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Mapping

from playwright.sync_api import sync_playwright

from instrument_booking.browser.sheet_page import NAME_BOX, PlaywrightSheetPage

EDGE_RELATIVE = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
EDGE_ROOT_VARS = ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")  # 依序尋找（系統安裝、64 位元、只裝給目前使用者）
GOOGLE_LOGIN_HOST = "accounts.google.com"
BACKGROUND_ARGS = [
    "--start-minimized",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]
LOAD_POLL_MS = 250


class NotLoggedIn(Exception):
    """開啟試算表時被導向 Google 登入頁：需要使用者重新登入。"""


class SessionError(Exception):
    """試算表無法開啟（訊息為給使用者看的繁體中文）。"""


def default_profile_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "InstrumentBooking" / "edge-profile"


def find_edge(env: Mapping[str, str] = os.environ, is_file: Callable[[Path], bool] = Path.is_file) -> Path:
    """系統 Edge 的位置；找不到時拋 SessionError。"""
    for var in EDGE_ROOT_VARS:
        root = env.get(var)
        if root:
            candidate = Path(root) / EDGE_RELATIVE
            if is_file(candidate):
                return candidate
    raise SessionError("找不到 Microsoft Edge：請先安裝 Edge，再重新登入")


def open_login_window(url: str, profile_dir: Path, *, edge_path: Path | None = None,
                      popen: Callable = subprocess.Popen):
    """以一般（非自動化）Edge 開啟專屬設定檔，讓使用者手動登入 Google；登入後關閉視窗即可。

    呼叫前必須先關閉同一設定檔的 EdgeSession，否則網址會被交給自動化中的瀏覽器。
    """
    edge = edge_path or find_edge()
    profile_dir.mkdir(parents=True, exist_ok=True)
    return popen([str(edge), f"--user-data-dir={profile_dir}", "--no-first-run",
                  "--no-default-browser-check", url])


class EdgeSession:
    """以 Playwright 啟動 Edge 專屬設定檔（最小化在背景）並開啟試算表。

    Playwright 的同步物件只能在建立它的執行緒使用：start、restart、close 與所有網頁操作必須在同一條執行緒。
    """

    def __init__(self, url: str, profile_dir: Path, *, playwright_factory: Callable = sync_playwright,
                 load_timeout_ms: int = 60_000, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.url = url
        self.profile_dir = profile_dir
        self._playwright_factory = playwright_factory
        self._load_timeout_ms = load_timeout_ms
        self._monotonic = monotonic
        self._playwright = None
        self._context = None
        self._page = None

    def start(self):
        """啟動並開啟試算表，回傳 Playwright Page；未登入時拋 NotLoggedIn，載入逾時拋 SessionError。

        任何失敗都會先關閉已啟動的資源。
        """
        self._playwright = self._playwright_factory().start()
        try:
            page = self._open()
        except BaseException:
            self.close()
            raise
        self._page = page
        return page

    def _open(self):
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            channel="msedge",
            headless=False,
            viewport=None,
            args=BACKGROUND_ARGS,
            ignore_default_args=["--enable-automation"],
        )
        self._context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://docs.google.com")
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._minimize(page)
        page.goto(self.url, wait_until="domcontentloaded")
        self._wait_loaded(page)
        return page

    def _minimize(self, page) -> None:
        try:
            cdp = self._context.new_cdp_session(page)
            window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "minimized"}})
        except Exception:
            pass  # 最小化失敗不影響預約，只是視窗會顯示在前景

    def _wait_loaded(self, page) -> None:
        """等到名稱方塊出現；一被導向登入頁就立刻拋 NotLoggedIn，不必等到逾時。"""
        deadline = self._monotonic() + self._load_timeout_ms / 1000
        while True:
            if GOOGLE_LOGIN_HOST in page.url:
                raise NotLoggedIn("需要重新登入 Google")
            if page.locator(NAME_BOX).count() > 0:
                return
            if self._monotonic() >= deadline:
                raise SessionError(f"試算表在 {self._load_timeout_ms // 1000} 秒內沒有載入完成，請檢查網路與預約表網址")
            page.wait_for_timeout(LOAD_POLL_MS)

    def restart(self):
        self.close()
        return self.start()

    def close(self) -> None:
        for closer in (lambda: self._context and self._context.close(),
                       lambda: self._playwright and self._playwright.stop()):
            try:
                closer()
            except Exception:
                pass  # 關閉失敗不影響之後重新啟動
        self._playwright = self._context = self._page = None

    @property
    def request(self):
        """與瀏覽器共用登入 cookie 的 HTTP 請求介面（下載快照用）。"""
        return self._context.request

    def sheet_page(self) -> PlaywrightSheetPage:
        return PlaywrightSheetPage(self._page)

    def restart_sheet_page(self) -> PlaywrightSheetPage:
        self.restart()
        return self.sheet_page()
