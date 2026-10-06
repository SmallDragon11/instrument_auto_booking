"""複製目標範圍 → 只改底色、第一格填名字 → 貼回；再讀回比對框線與底色。"""
import re
import sys
import time

from common import OUT, WRITE_JS, copy_range, jump, name_box, sheet_page

SHEET = "202610"
COL, FIRST_ROW, LAST_ROW = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])  # 例：V 54 56
TARGET = f"'{SHEET}'!{COL}{FIRST_ROW}:{COL}{LAST_ROW}"
FIRST = f"'{SHEET}'!{COL}{FIRST_ROW}"
RGB = "rgb(164, 194, 244)"  # A4C2F4
NAME = sys.argv[4] if len(sys.argv) > 4 else "SPIKE-TEST"
NAME_STYLE = "font-size: 12pt; font-weight: bold; text-align: center;"
ALL_CELLS_STYLED = len(sys.argv) > 5 and sys.argv[5] == "all"
TD = re.compile(r"<td style=\"([^\"]*)\">(.*?)</td>", re.S)


def booking_html(copied: str, rgb: str, name: str, name_style: str) -> str:
    n = 0

    def repl(m):
        nonlocal n
        style = re.sub(r"background-color:\s*[^;]+;?", "", m.group(1)).strip()
        style = f"{style} background-color: {rgb};"
        content = ""
        if n == 0 or ALL_CELLS_STYLED:
            style += " " + name_style
        if n == 0:
            content = name
        n += 1
        return f'<td style="{style}">{content}</td>'

    return TD.sub(repl, copied)


with sheet_page() as (ctx, page):
    rows = LAST_ROW - FIRST_ROW + 1
    before, secs = copy_range(page, TARGET, rows)
    print(f"貼上前讀取 {secs:.3f}s；是否空白：{NAME not in before and 'rgb(255, 255, 255)' in before}")
    import re as _re
    assert all(bg == "rgb(255, 255, 255)" for bg in _re.findall(r"background-color: ([^;]+);", before))         and not _re.search(r">[^<\s]+</td>", before), "目標範圍不是空的，放棄貼上"
    (OUT / f"s3_before_{COL}{FIRST_ROW}.html").write_text(before, encoding="utf-8")
    html = booking_html(before, RGB, NAME, NAME_STYLE)
    page.evaluate(WRITE_JS, [html, NAME + "\n" * (rows - 1)])
    t0 = time.perf_counter()
    jump(page, FIRST)
    assert name_box(page) == f"{COL}{FIRST_ROW}", f"選取位置錯誤：{name_box(page)}，放棄貼上"
    page.keyboard.press("Control+V")
    print(f"跳轉＋貼上耗時：{time.perf_counter() - t0:.3f}s")
    page.wait_for_timeout(1500)
    after, secs = copy_range(page, TARGET, rows)
    (OUT / f"s3_after_{COL}{FIRST_ROW}.html").write_text(after or "", encoding="utf-8")
    print(f"讀回耗時 {secs:.3f}s")
    b_borders = re.findall(r"border-width: [^;]+; border-style: [^;]+; border-color: [^;]+;", before)
    a_borders = re.findall(r"border-width: [^;]+; border-style: [^;]+; border-color: [^;]+;", after or "")
    print("框線一致：", b_borders == a_borders)
    print("底色：", re.findall(r"background-color: ([^;]+);", after or ""))
    print("名字：", NAME in (after or ""), "｜第一格樣式含粗體：", "font-weight: bold" in (after or ""))
    page.screenshot(path=str(OUT / f"s3_after_{COL}{FIRST_ROW}.png"))
