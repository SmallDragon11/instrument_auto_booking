"""對照：B 確定看到 A 的內容後才貼上同一格，是否會覆蓋？"""
from common import TEST_URL, sheet_page
from s5_concurrent_lib import paste, read_first

with sheet_page() as (ctx, a):
    b = ctx.new_page()
    b.goto(TEST_URL, wait_until="domcontentloaded")
    b.wait_for_selector("#t-name-box", timeout=60_000)
    b.wait_for_timeout(3000)
    assert read_first(a, "Q", 66) == "", "Q66 不是空的"
    paste(a, "Q", 66, 1, "AAA", "rgb(164, 194, 244)")
    a.wait_for_timeout(3000)
    print("B 貼上前看到：", read_first(b, "Q", 66))
    paste(b, "Q", 66, 1, "BBB", "rgb(244, 204, 204)")
    a.wait_for_timeout(5000)
    print("結果：A 分頁看到", read_first(a, "Q", 66), "｜B 分頁看到", read_first(b, "Q", 66))
    print("Q65（上一輪同格競爭）：", read_first(a, "Q", 65))
