"""兩個分頁＝兩個編輯工作階段：測不同格同時寫入、傳播延遲、同格競爭。"""
import re
import time

from common import OUT, TEST_URL, WRITE_JS, copy_range, jump, name_box, sheet_page

SHEET = "'202610'!"


def paste(page, col, row, rows, name, rgb):
    """以「複製目標 → 改底色、填名字 → 貼回」寫入；只用於空白範圍。"""
    page.bring_to_front()
    # 單格複製會得到 <span>（沒有底色）→ 一律多讀一列，貼上前再刪掉多出的列
    html, _ = copy_range(page, f"{SHEET}{col}{row}:{col}{row + rows}", rows + 1)
    html = re.sub(r"<tr\b(?:(?!<tr\b).)*?</tr>(?=</tbody>)", "", html, flags=re.S)
    assert len(re.findall(r"<tr\b", html)) == rows
    n = 0

    def repl(m):
        nonlocal n
        style = re.sub(r"background-color:\s*[^;]+;?", "", m.group(1)).strip()
        style += f" background-color: {rgb}; font-size: 12pt; font-weight: bold; text-align: center;"
        content = name if n == 0 else ""
        n += 1
        return f'<td style="{style}">{content}</td>'

    html = re.sub(r"<td style=\"([^\"]*)\">(.*?)</td>", repl, html, flags=re.S)
    page.evaluate(WRITE_JS, [html, name])
    jump(page, f"{SHEET}{col}{row}")
    assert name_box(page) == f"{col}{row}"
    page.keyboard.press("Control+V")
    return time.perf_counter()


def read_first(page, col, row):
    page.bring_to_front()
    html, _ = copy_range(page, f"{SHEET}{col}{row}:{col}{row + 1}", 2)
    m = re.search(r"<td[^>]*>(.*?)</td>", html, re.S)
    return (m.group(1) if m else "").strip()


