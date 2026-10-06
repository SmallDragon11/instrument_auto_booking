# Plan 01（core）完成後帶給 Plan 02／03 的待辦

來源：Plan 01 子代理執行的審查紀錄（2026-10-06）。

## Plan 02（瀏覽器層）

- 寫入器建構子要傳入開放時間，`paste_booking` 在開放時間前一律拒絕（規格 §9 的第二道防護；core 的 `job.execute` 已有一道）。
- 剪貼簿 `rgb(r, g, b)` 要轉成 6 碼大寫十六進位，白色轉成 `None`，讓 `_is_ours`／`describe_busy` 能直接比較。
- 必須遵守 `SheetWriter` Protocol docstring 的約定（`core/job.py`），並為「違約回傳非 CellState」的情況寫轉接層測試。
- 可考慮收緊：只有在貼上時當掉，重試才把「已是自己的名字＋顏色」判為已寫入；讀取時當掉則仍判為 LIVE_CONFLICT。

## Plan 03（GUI／排程／紀錄）

- 「現在時間」一律從 `Clock` 取得（`datetime.fromtimestamp(clock.now(), TAIPEI)`），不要用系統時鐘。
- 執行紀錄要記下 `ClockSync.source`；可加上 NTP 不確定度欄位（目前 `ClockSync` 沒有保留 delay）。
- `sync_clock` 目前不回報 NTP／HTTPS 為何失敗，紀錄層需要補上原因（規格 §7「記錄警告」）。
- `due_run` 在週五 13:00 前會回傳上一個週期；呼叫端要依 `Due.target_monday` 過濾預約清單，避免舊週期的補跑在 T 時占住瀏覽器。

## Plan 02（瀏覽器層）完成後新增（2026-10-06）

### Plan 03 必須做到

- **單一執行緒擁有瀏覽器**：Playwright sync API 的物件只能在建立它的執行緒使用。EdgeSession 的 start/restart/close、SnapshotDownloader.download（用 context.request）、所有寫入器呼叫與 execute 都放在同一條專用工作執行緒；GUI 的「重新整理佔用」與「連線測試」不可在 GUI 執行緒呼叫，一律排入同一佇列。設定檔同時只能有一個 Edge，T−10 到驗證結束期間封鎖重新整理。
- **登入視窗**：開啟 open_login_window 前必須先關閉同一設定檔的 EdgeSession（否則網址會被交給自動化中的瀏覽器）。不要在有 asyncio 事件迴圈的執行緒呼叫 sync_playwright()。
- **接線方式**：T−10 `session.start()`（NotLoggedIn → 通知）→ `SnapshotDownloader(lambda: session.request, file_id_from_url(url), 快照資料夾)` → `preflight(...)`（now 取自校時後的 Clock）；約 T−1 建立 `BrowserSheetWriter(session.sheet_page(), restart=session.restart_sheet_page, clock=clock, not_before=nb)` 並呼叫 `execute(..., nb, sleep=time.sleep)`；寫入器與 execute 用**同一個** Clock 與**同一個** nb；finally 關閉 session；DownloadError、NotLoggedIn、Playwright 英文錯誤轉為中文訊息。
- **視窗與焦點**：目前以 `--start-maximized` 有頭啟動，T−10 可能搶走焦點，她正在打字時按鍵會落到實驗室表單。需決定視窗行為（最小化／移到畫面外／不取得焦點），並實測 Edge 不在前景時剪貼簿與 Ctrl+C/V 仍正常（Playwright 預設有焦點模擬，但未實測）。需告知使用者 13:00 時 App 會覆寫系統剪貼簿。
- **正式使用前的實機端對端測試**（測試副本）：execute 一次跑 3 筆以上、跨管型爐與烘箱工作表、連續不等待；09:00 與 23:00 單格；白色與主題白格；在兩個「同範圍內容不同」的工作表間反覆切換，量測作用中工作表檢查＋0.3 秒等待後是否仍會讀錯；執行中強制關閉 Edge 測試復原。
- **設定驗證**：名字需去空白、不可為空、不可含 tab 或換行。

### 延後的強化（非阻擋）

- 渲染程序整個卡死時 `page.evaluate`／`keyboard.press` 仍可能無限等待（JS 的 Promise.race 只在頁面仍有回應時有效）→ 考慮以獨立執行緒的看門狗限制整次執行時間。
- `background` 簡寫若是多色漸層（如 `linear-gradient(rgb(255,255,255), …)`）會取第一個顏色而讀成白色（Google 目前不會輸出這種格式）→ 可改為多色時回傳 "unknown"。
- `core/job.py` 的 `SheetWriter.read_range` docstring 仍寫「多選下一列」，實作在 23:00 改為多讀上一列 → 更新文字。
- 非 TargetClosed 的 Playwright／JS 錯誤目前一律視為 WriterCrashed（安全但會重啟瀏覽器、較慢）。
- EDGE_PATH 寫死在 Program Files (x86)、無備援；`start()` 重複呼叫會洩漏前一個 Playwright；底稿沒有時效限制；快照檔不會自動清理（每個約 3.4 MB）；登入偵測要等滿 60 秒。

## Plan 03（服務層）完成後新增（2026-10-07）

### Plan 03 文件之後的介面變更（Plan 04 請以程式碼為準）

- `RunRecord.preflight_error` 已改名為 `error`；新增 `snapshot_at`；`started_at` 為開始寫入的時間；`late` 依實際寫入時間判斷。
- `JsonStore.save_settings(s, now=None)` 會在星期或時間改變時記錄 `schedule_since()`（GUI 呼叫時不要傳 `now`）；`JsonStore.corrupt_runs()` 回傳被隔離的損毀紀錄檔名（GUI 應顯示警告）。存檔已跨執行緒安全，失敗一律為 `StoreError`。
- `AutomationService.status() -> ServiceStatus`（不阻塞；phase、run_at、target_monday、retry_at、last_error、service_error、editing_locked）、`cancel_current()`、`upcoming(now, settings, store)`；`OccupancyService` 不再接受 `file_id`（改讀設定，設定無效時拋 `NotConfigured`）；`start_login` 設定無效時拋 `NotConfigured`、網址空白時開 Google 登入頁。
- 取消語意：因設定變更而在寫入前取消 → 不記錄、不算已執行，依新設定重新排程（通知「自動預約已延後」）；手動取消 → 記錄並算已執行。

### Plan 04 必須做到

- `status().editing_locked` 為 True（T−10 到寫入結束）時：鎖定該週清單編輯與「重新登入」按鈕並說明原因；只有 `run_at` 不為 None 時才提供「取消本次預約」（避免延後後 1 秒內的取消被丟棄）。
- `DONE` 只維持約 1 秒、`last_error` 會在下一週期清除 → 執行紀錄頁與「上次結果」一律讀 `load_runs()`。
- `upcoming()`、`load_runs()` 會讀檔（可能重試），GUI 不可在 GUI 執行緒頻繁呼叫；放在背景或快取。
- 所有阻塞呼叫（`OccupancyService.refresh`、`run_connection_test`、`start_login`、`Services.shutdown`）都不可在 GUI 執行緒；Notifier 以 Qt signal 轉到 GUI 執行緒顯示系統匣通知。
- 結束程式：先停止自動化執行緒（stop.set()），再在背景 `Services.shutdown()`；T−10 到寫入結束期間結束程式要警告。
- 單一執行個體（第二個實例會搶同一個 Edge 設定檔與排程）。
- 提醒使用者：開放時間前後 App 會使用系統剪貼簿，請勿同時複製貼上。
- 自動化服務持續出錯或設定無效時，顯示常駐提示（通知只發一次）。

### 延後的小問題（非阻擋）

- 預檢後修改清單（保留 Edge）且同一秒手動取消 → 待命的 Edge 不會被關閉（`_cancel_cycle` 的 had_plan 判斷）；`step()` 在該時點拋例外時亦同。
- 被隔離的損毀紀錄不再算已執行 → 該週可能補跑一次（不會覆寫，使用者會收到第二次通知）。
- storage 依賴 service.settings 的分層、渲染卡死的看門狗、設定檔被占用的專屬提示。
