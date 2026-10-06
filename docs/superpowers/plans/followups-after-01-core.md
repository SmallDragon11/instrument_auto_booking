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
