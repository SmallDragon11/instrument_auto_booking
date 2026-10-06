"""診斷：跨工作表跳轉後 Ctrl+C 讀到空內容，是否因為工作表尚未載入。"""
import time

from common import OUT, READ_HTML_JS, WRITE_JS, jump, sheet_page

REF = "'10月oven2026'!B4:B6"


def copy_after(page, ref, wait_ms):
    page.evaluate(WRITE_JS, ["", ""])
    jump(page, ref)
    page.wait_for_timeout(wait_ms)
    t0 = time.perf_counter()
    page.keyboard.press("Control+C")
    html = None
    while time.perf_counter() - t0 < 2:
        html = page.evaluate(READ_HTML_JS)
        if html:
            break
        page.wait_for_timeout(30)
    return html


with sheet_page() as (ctx, page):
    for wait_ms in (0, 300, 1000):
        jump(page, "'202610'!A1")  # 先回到管型爐表
        page.wait_for_timeout(1000)
        html = copy_after(page, REF, wait_ms)
        ok = "Rolling" in (html or "")
        print(f"跳轉後等 {wait_ms}ms：{'讀到 Rolling' if ok else '沒讀到'}（長度 {len(html or '')}）")
    print("名稱方塊目前內容：", page.locator("#t-name-box").input_value())
    page.screenshot(path=str(OUT / "s2b_after.png"))
    # 已在同一工作表時再讀一次
    html = copy_after(page, REF, 0)
    print("已在 10月oven2026 時再讀：", "讀到 Rolling" if "Rolling" in (html or "") else "沒讀到")
    (OUT / "s2b_oven.html").write_text(html or "", encoding="utf-8")
