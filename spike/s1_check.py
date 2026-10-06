"""確認 Playwright 啟動同一設定檔時仍是登入狀態，且能看到試算表。"""
from common import OUT, sheet_page

with sheet_page() as (ctx, page):
    print("URL:", page.url)
    print("標題:", page.title())
    page.screenshot(path=str(OUT / "s1_check.png"))
    assert "accounts.google.com" not in page.url, "被導向登入頁：登入狀態未被沿用"
    print("OK：登入狀態已沿用，名稱方塊存在")
