"""以登入狀態下載 xlsx，測兩種網址；檢查 S3 貼上的內容、底色與字型是否已存回檔案。"""
import time

import openpyxl

from common import FILE_ID, OUT, sheet_page

URLS = {
    "sheets_export": f"https://docs.google.com/spreadsheets/d/{FILE_ID}/export?format=xlsx",
}
CELLS = ["R57", "R58", "R59", "Q58", "S58"]  # Q58、S58：未貼上的鄰格作對照


def describe(c):
    fill = c.fill.fgColor.rgb if c.fill and c.fill.fill_type else None
    return f"{c.coordinate}={c.value!r} fill={fill} font=({c.font.sz},{'粗' if c.font.b else '細'},{c.alignment.horizontal})"


with sheet_page() as (ctx, page):
    for name, url in URLS.items():
        t0 = time.perf_counter()
        resp = ctx.request.get(url, timeout=60_000)
        body = resp.body()
        print(f"{name}: HTTP {resp.status}, {len(body) / 1e6:.2f} MB, {time.perf_counter() - t0:.2f}s, "
              f"type={resp.headers.get('content-type')}")
        if resp.ok and body[:2] == b"PK":
            path = OUT / f"s4_{name}.xlsx"
            path.write_bytes(body)
            ws = openpyxl.load_workbook(path, data_only=True)["202610"]
            for ref in CELLS:
                print("   ", describe(ws[ref]))
