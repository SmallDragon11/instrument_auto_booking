"""Spike 共用工具（throwaway，不進正式程式碼）。"""
import os
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

FILE_ID = "1621E29JjZYnJGj1lnLgJ-GiU_bCVJCdh"
TEST_URL = f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = Path(os.environ["LOCALAPPDATA"]) / "InstrumentBooking" / "spike-profile"
OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)

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


@contextmanager
def sheet_page():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE),
            channel="msedge",
            headless=False,
            viewport=None,
            args=["--start-maximized"],
            ignore_default_args=["--enable-automation"],
        )
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://docs.google.com")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(TEST_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#t-name-box", timeout=60_000)
        try:
            yield ctx, page
        finally:
            ctx.close()


def jump(page, ref: str) -> float:
    """用名稱方塊跳到範圍（可含工作表名），回傳耗時秒數。"""
    t0 = time.perf_counter()
    box = page.locator("#t-name-box")
    box.click()
    box.fill(ref)
    box.press("Enter")
    return time.perf_counter() - t0


def name_box(page) -> str:
    return page.locator("#t-name-box").input_value()


def copy_range(page, ref: str, rows: int, timeout: float = 3.0) -> tuple[str, float]:
    """安全版：跳轉後確認名稱方塊與 HTML 列數都正確才回傳；否則重試，逾時拋出例外。"""
    import re
    t0 = time.perf_counter()
    expected_box = ref.split("!")[-1]
    while time.perf_counter() - t0 < timeout:
        page.evaluate(WRITE_JS, ["", ""])
        jump(page, ref)
        if name_box(page) != expected_box:
            page.wait_for_timeout(100)
            continue
        page.keyboard.press("Control+C")
        for _ in range(20):
            html = page.evaluate(READ_HTML_JS)
            if html:
                break
            page.wait_for_timeout(30)
        if html and "<table" in html and len(re.findall(r"<tr\b", html)) == rows:
            return html, time.perf_counter() - t0
        page.wait_for_timeout(150)
    raise RuntimeError(f"無法正確讀取 {ref}（名稱方塊：{name_box(page)}）")


def copy_html(page, ref: str, timeout: float = 2.0) -> tuple[str | None, float]:
    """選取範圍 → Ctrl+C → 讀剪貼簿 HTML。回傳 (html, 總耗時)。"""
    t0 = time.perf_counter()
    page.evaluate(WRITE_JS, ["", ""])  # 先清空，避免讀到舊內容
    jump(page, ref)
    page.keyboard.press("Control+C")
    html = None
    while time.perf_counter() - t0 < timeout:
        html = page.evaluate(READ_HTML_JS)
        if html:
            break
        page.wait_for_timeout(30)
    return html, time.perf_counter() - t0
