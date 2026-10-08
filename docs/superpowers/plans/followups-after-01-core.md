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

## Plan 04（GUI）完成後新增（2026-10-07）

### 實機檢查結果（測試副本）

- 第一次檢查發現：點儀器分頁後拖曳無法新增、看不到該儀器的佔用（`SegmentedWidget` 的 `clicked(bool)` 蓋掉 lambda 預設參數）；原生 tooltip 顯示成黑框；視窗最窄 991 px。皆已修正（最窄 723 px），並補上真的點擊分頁的測試。
- 第二次檢查：6 項全部正常；Edge 無殘留。
- 端對端實機（設定改為週三 22:40）：22:30 預檢完成（NTP，本機慢 0.05 秒），第一筆 22:40:00.532 寫入，7 筆於 22:40:02.6 前處理完（6 成功、1 與自己重疊），無錯誤。

### Plan 04 文件之後的介面變更（Plan 05 請以程式碼為準）

- 單一執行個體改以 `QLockFile`（`%TEMP%\<key>.lock`）保證唯一，具名管道只用來通知既有實例（Windows 允許同名多個管道伺服器）。
- `AutomationService.run_forever`：離開迴圈時若有待處理的取消，會再跑一次 `step()` 記錄取消；`MainWindow(automation_thread=...)` 結束時在背景先 join 自動化執行緒（最多 45 秒）再 `Services.shutdown()`。
- 佔用的自動下載改由 `WeekPage.apply_status` 在「有真實狀態且未鎖定」時觸發；`set_week` 只標記待下載。
- 預檢失敗等待重試（RETRY_WAIT）時允許「重新登入」（連線測試仍鎖定）。
- 設定有未儲存的變更時不進行連線測試或重新登入。
- 執行紀錄頁：「第一筆寫入」時間取代預熱開始時間；原因中的預約 id 以「第 N 筆」顯示。

### Plan 05 必須做到

- PyInstaller（onedir、無主控台）：收集 qfluentwidgets 資源與 PySide6 外掛；qfluentwidgets 匯入時會 `print` 廣告，確認無主控台時不出錯；鎖定 PySide6-Fluent-Widgets 版本（主視窗依賴 `FluentWindow.widgetLayout` 內部結構）。
- 以打包後的 exe 驗證開機自動啟動（Run 登錄 `InstrumentBooking`＝`"…exe" --background`）、背景啟動只出現在系統匣、關閉開關後移除；exe 圖示（由 `app_icon()` 產生 .ico）。
- 正式使用前的端對端實機測試沿用「Plan 02 完成後」所列項目（跨管型爐與烘箱、09:00 與 23:00 單格、執行中強制關閉 Edge），並以 exe 從 GUI 走一次。

### 延後的小問題（非阻擋）

- 「重新登入」在 RETRY_WAIT→PREPARING 後最多約 1 秒仍可點；此時點下會關掉預檢保留的 Edge，該週寫入失敗。可在 `start_login` 加服務端狀態檢查。
- 結束前一刻若執行時間剛好改變，結束時的最後一次 `step()` 可能短暫開始預檢（受 45 秒 join 與關閉服務限制）。
- 「取消本次預約」確認後未重新判斷是否已過開放時間；單一執行個體取得鎖失敗時未區分權限錯誤；具名管道未依使用者區分；補跑時不提供取消；清單為空時仍顯示倒數。
- 執行紀錄頁深色主題下成功／略過的文字顏色對比偏低；`HistoryPage` 失敗路徑、`MAX_CARDS` 未測。

## Plan 05（打包）完成後新增（2026-10-08）

### 實機驗收結果（開發機、測試副本）

- 隔離冒煙（暫存 APPDATA／LOCALAPPDATA、預先關閉開機啟動）：背景啟動、紀錄檔、第二個實例交給第一個（代碼 0）、未寫入 Run 登錄、無殘留 Edge。
- 以檔案總管解壓 zip、雙擊「安裝.cmd」：沒有警告視窗（本機產生的 zip 沒有網路標記；她從網路下載時可能出現 SmartScreen）；桌面與開始功能表捷徑正常；沿用既有設定；Run 值為 `"%LOCALAPPDATA%\Programs\ExperimentPlanner\ExperimentPlanner.exe" --background`。工作管理員的開機啟動清單顯示「ExperimentPlanner.exe」（不是「實驗規劃助手」）。
- 從桌面捷徑開啟後端對端實測（12:10，16 筆）：12:00 預檢（NTP，本機慢 0.02 秒）；第一筆 12:10:00.529 寫入；12:10:01.18 強制結束自動化 Edge 後約 5 秒恢復，其餘各筆照常完成（13 成功、3 與自己重疊），含 09:00 與 23:00 單格、管型爐與烘箱；完成後自動化 Edge 全部關閉。

### 交付到她的筆電

1. 先在她的電腦確認「Windows 安全性 → 應用程式與瀏覽器控制 → 智慧型應用程式控制」：若為「開啟」，未簽章的 exe 會被直接封鎖且無法略過，需要另外處理。
2. 把 `dist\ExperimentPlanner-0.1.0.zip` 給她 → 檔案總管「解壓縮全部」→ 雙擊「安裝.cmd」（若出現「Windows 已保護您的電腦」：其他資訊 → 仍要執行）。
3. 設定頁：名字、**實驗室正式預約表網址**、每週五 13:00 → 「儲存預約設定」；「重新登入」用她的 Google 帳號，確認看得到預約表後關閉該 Edge 視窗；「連線測試」→ ✅。
4. 確認開機啟動已啟用；把系統匣圖示從「^」拖到工作列。
5. 電源：插電時不睡眠、闔上螢幕不做任何動作（或請她不要闔上）；Windows Update 使用時段涵蓋週五 13:00；勿打擾不要擋掉通知。

### 延後的小問題（非阻擋）

- 工作管理員開機清單顯示 exe 檔名：版本資訊可再加一份英文（040904B0）字串表。
- 重新登入保護在預檢校時（最多約 5 秒、尚未保留 Edge）期間仍有空檔：可在校時前就在瀏覽器執行緒保留（例如 `worker.reserve()`）；最壞情況是該週預檢失敗而放棄（有通知）。
- 建置：沒有 `.gitattributes`（`.cmd`／`.ps1` 的 CRLF 依賴 `core.autocrlf`）、zip 內沒有記錄 commit 與套件版本、`.ico` 只有 256 一種尺寸、自我檢查逾時與結果檔無法寫入時只有 traceback、測試寫死版本 0.1.0。
- `install.ps1`：相對路徑的 `-InstallDir` 可能讓「程式執行中」的判斷失效（預設與安裝.cmd 都是絕對路徑）。
