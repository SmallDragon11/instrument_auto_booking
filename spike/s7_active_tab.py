"""唯讀驗證：切換工作表後，能否從網頁讀到目前作用中的工作表名稱，以及切換需要多久。"""
import time

from common import jump, sheet_page

CANDIDATES = [
    ".docs-sheet-active-tab .docs-sheet-tab-name",
    ".docs-sheet-active-tab",
]

with sheet_page() as (ctx, page):
    for sel in CANDIDATES:
        loc = page.locator(sel)
        print(f"{sel}: count={loc.count()} text={loc.first.inner_text()!r}" if loc.count() else f"{sel}: count=0")
    for target in ["10月oven2026", "202610", "9月oven2026"]:
        t0 = time.perf_counter()
        jump(page, f"'{target}'!B5:B6")
        seen = []
        while time.perf_counter() - t0 < 3:
            name = page.locator(CANDIDATES[0]).first.inner_text()
            seen.append((round(time.perf_counter() - t0, 3), name))
            if name == target:
                break
            page.wait_for_timeout(20)
        print(target, "→", seen[0], "...", seen[-1], f"({len(seen)} 次)")
