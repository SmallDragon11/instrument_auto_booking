"""唯讀驗證（最終審查 #1、#3）：23:00 單格多選下一列時名稱方塊的樣子；無填色儲存格的剪貼簿 HTML。"""
import re

from common import READ_HTML_JS, WRITE_JS, jump, sheet_page


def copy(page, ref):
    page.evaluate(WRITE_JS, ["", ""])
    jump(page, ref)
    page.wait_for_timeout(400)
    box = page.locator("#t-name-box").input_value()
    page.keyboard.press("Control+C")
    html = None
    for _ in range(40):
        html = page.evaluate(READ_HTML_JS)
        if html:
            break
        page.wait_for_timeout(30)
    tds = re.findall(r"<td\b[^>]*>", html or "")
    bgs = [re.search(r"background(?:-color)?:\s*([^;\"]+)", td) for td in tds]
    print(f"{ref}: 名稱方塊={box!r} rows={len(re.findall(r'<tr', html or ''))} "
          f"background={[b.group(0) if b else None for b in bgs]} span={'<span' in (html or '')[:20]}")


with sheet_page() as (ctx, page):
    for ref in ["'202610'!U20:U21", "'202610'!B20:B21", "'202610'!U19:U20",
                "'202610'!B19:B20", "'202610'!I16:I18", "'202610'!U36:U37"]:
        copy(page, ref)
