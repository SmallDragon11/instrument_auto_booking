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


with sheet_page() as (ctx, a):
    b = ctx.new_page()
    b.goto(TEST_URL, wait_until="domcontentloaded")
    b.wait_for_selector("#t-name-box", timeout=60_000)
    b.wait_for_timeout(3000)

    # 0. 確認測試區為空
    for r in range(60, 67):
        assert read_first(a, "Q", r) == "", f"Q{r} 不是空的，中止"

    # 1. 不同格幾乎同時寫入
    paste(a, "Q", 60, 1, "A1", "rgb(164, 194, 244)")
    paste(b, "Q", 61, 1, "B1", "rgb(244, 204, 204)")
    a.wait_for_timeout(4000)
    print("1. 不同格：A 分頁看到", read_first(a, "Q", 60), read_first(a, "Q", 61),
          "｜B 分頁看到", read_first(b, "Q", 60), read_first(b, "Q", 61))

    # 2. 傳播延遲：A 貼上後，B 反覆讀取直到看到
    for trial in range(3):
        row = 62 + trial
        t_paste = paste(a, "Q", row, 1, f"P{trial}", "rgb(164, 194, 244)")
        seen = None
        while time.perf_counter() - t_paste < 10:
            if read_first(b, "Q", row) == f"P{trial}":
                seen = time.perf_counter() - t_paste
                break
        print(f"2. 傳播延遲第 {trial + 1} 次：{'%.2fs' % seen if seen else '10 秒內未看到'}")

    # 3. 同格競爭：A、B 先後貼上同一格（B 貼上時尚未看到 A）
    paste(a, "Q", 65, 1, "AAA", "rgb(164, 194, 244)")
    paste(b, "Q", 65, 1, "BBB", "rgb(244, 204, 204)")
    a.wait_for_timeout(5000)
    print("3. 同格競爭：A 分頁看到", read_first(a, "Q", 65), "｜B 分頁看到", read_first(b, "Q", 65))
