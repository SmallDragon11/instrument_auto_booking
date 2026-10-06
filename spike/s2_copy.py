"""跳到已知有預約的範圍與空白範圍，Ctrl+C 讀出 HTML，量測耗時。"""
from common import OUT, copy_html, jump, sheet_page

CASES = {
    "booked": "'202610'!E8:E12",   # 10/6 A-牆 11:00 起，E8 = 'Zoe'，底色 A4C2F4
    "empty": "'202610'!V54:V56",   # 11/1（日）C-小房間 09–12，預期空白
    "other_sheet": "'10月oven2026'!B4:B6",  # 跨工作表跳轉，B4 = 'Rolling'
}

with sheet_page() as (ctx, page):
    for name, ref in CASES.items():
        html, secs = copy_html(page, ref)
        print(f"{name}: {secs:.3f}s, html={'有' if html else '無'}, 長度={len(html or '')}")
        (OUT / f"s2_{name}.html").write_text(html or "", encoding="utf-8")
    for i in range(3):
        print(f"重複跳轉第 {i + 1} 次：{jump(page, CASES['booked']):.3f}s")
