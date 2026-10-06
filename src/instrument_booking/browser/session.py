"""Edge 專屬設定檔：一般 Edge 登入一次，之後由 Playwright 沿用登入狀態（見 docs/spike-report.md 第 1 項）。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from instrument_booking.browser.sheet_page import NAME_BOX, PlaywrightSheetPage

EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
GOOGLE_LOGIN_HOST = "accounts.google.com"


class NotLoggedIn(Exception):
    """開啟試算表時被導向 Google 登入頁：需要使用者重新登入。"""


def default_profile_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "InstrumentBooking" / "edge-profile"


def open_login_window(url: str, profile_dir: Path, *, edge_path: Path = EDGE_PATH,
                      popen: Callable = subprocess.Popen):
    """以一般（非自動化）Edge 開啟專屬設定檔，讓使用者手動登入 Google；登入後關閉視窗即可。"""
    profile_dir.mkdir(parents=True, exist_ok=True)
    return popen([str(edge_path), f"--user-data-dir={profile_dir}", "--no-first-run",
                  "--no-default-browser-check", url])


class EdgeSession:
    """以 Playwright 啟動 Edge 專屬設定檔並開啟試算表。"""

    def __init__(self, url: str, profile_dir: Path, *, playwright_factory: Callable = sync_playwright,
                 load_timeout_ms: int = 60_000) -> None:
        self.url = url
        self.profile_dir = profile_dir
        self._playwright_factory = playwright_factory
        self._load_timeout_ms = load_timeout_ms
        self._playwright = None
        self._context = None
        self._page = None

    def start(self):
        """啟動並開啟試算表，回傳 Playwright Page；未登入時拋 NotLoggedIn。"""
        self._playwright = self._playwright_factory().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            channel="msedge",
            headless=False,
            viewport=None,
            args=["--start-maximized"],
            ignore_default_args=["--enable-automation"],
        )
        self._context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://docs.google.com")
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        page.goto(self.url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(NAME_BOX, timeout=self._load_timeout_ms)
        except PlaywrightTimeoutError:
            if GOOGLE_LOGIN_HOST in page.url:
                self.close()
                raise NotLoggedIn("需要重新登入 Google") from None
            raise
        self._page = page
        return page

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
