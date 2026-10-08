"""自我檢查：確認 Playwright 驅動程式能以自動化啟動系統 Edge（打包後與安裝到新電腦時使用）。

不使用專屬設定檔、不開預約表、不需要登入；以無頭模式開一個空白頁後立即關閉。
"""
from __future__ import annotations

from typing import Callable

from playwright.sync_api import sync_playwright


def run_self_check(*, playwright_factory: Callable = sync_playwright) -> tuple[bool, str]:
    """回傳 (是否正常, 給使用者看的說明)；不會拋出例外。"""
    try:
        with playwright_factory() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            try:
                version = browser.version
                browser.new_page().goto("about:blank")
            finally:
                browser.close()
    except Exception as e:
        return False, f"無法以自動化啟動 Edge：{type(e).__name__}：{e}"
    return True, f"Edge 自動化正常（版本 {version}）"
