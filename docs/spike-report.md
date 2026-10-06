# Spike 報告：瀏覽器自動化可行性（2026-10-06）

環境：開發者電腦、Edge（系統內建）、Playwright 1.63、Python 3.14；對象為測試副本（Office 編輯模式 `.xlsx`）。腳本在 `spike/`（throwaway）。

| # | 項目 | 結果 | 數據／備註 |
|---|---|---|---|
| 1 | 登入沿用 | ✅ | 以**非自動化** Edge 開專屬設定檔登入一次（登入後視窗自動關閉屬正常）；Playwright `launch_persistent_context(channel="msedge", ignore_default_args=["--enable-automation"])` 直接沿用，未被 Google 阻擋 |
| 2 | 名稱方塊跳轉（含跨工作表） | ✅（有條件） | `#t-name-box` 輸入 `'工作表'!範圍` 可跳轉並選取，0.1–0.25 秒。**跨工作表後約 0.3 秒內** Ctrl+C 會複製到錯誤內容 → 必須驗證 |
| 3 | Ctrl+C 讀文字＋底色 | ✅（有條件） | 多格範圍得到 `<google-sheets-html-origin><table>`，每格 `<td style>` 含 `background-color: rgb(r, g, b)` 與框線；空白格為 `rgb(255, 255, 255)`。0.15–0.9 秒。**單格複製只得到 `<span>`，沒有底色** → 一律至少讀 2 列 |
| 4 | 自組 HTML 貼上 | ✅（有條件） | 「複製目標範圍 → 只改每格底色、第一格填名字 → 貼回」，跳轉＋貼上 0.3–0.5 秒。框線完全保留。**每一格都必須明確加上字型樣式**，否則非第一格的字型會被重設為預設值（見下） |
| 5 | 調色盤備案 | — | 不需要（4 可行） |
| 6 | 下載 xlsx | ✅ | `https://docs.google.com/spreadsheets/d/{ID}/export?format=xlsx` 以 `context.request.get` 下載，3.4 MB、3.2–4.5 秒，**立即反映最新編輯**。`drive.google.com/uc?export=download` 回傳**舊版本**，不可用 |
| 7 | 同時編輯 | ✅ | 不同格同時寫入：兩者皆保留。傳播延遲（含對方讀取 ~0.3 秒）0.7–0.9 秒 → 實際約 0.4–0.6 秒。同格：相隔數秒時後寫者覆蓋；**相隔 1 秒內時結果不可預測**（實測先寫者保留） |
| 8 | 校時 | ✅ | NTP（time.google.com）可用，來回 15–35 ms；本機時鐘慢 0.61 秒。HTTPS Date 中點估計在 +0.18～+1.13 秒間跳動。**Date 標頭為捨去**（40 次皆未超前真實時間，最大 −0.057 秒）→ HTTPS 餘量維持 0.1 秒 |

> 第 8 項是在開發者的網路環境測得；她的筆電若在校園網路，NTP 可能被擋，需在安裝後以 App 的「連線測試」確認校時來源。

## 剪貼簿 HTML 格式（Plan 02 `clipboard_html` 的規格）

多格（≥ 2 列）複製結果，人名改為 X，樣式只保留關鍵部分：

```html
<google-sheets-html-origin style="…">
  <table xmlns="http://www.w3.org/1999/xhtml" cellspacing="0" cellpadding="0" dir="ltr" border="1"
         data-sheets-root="1" data-sheets-baot="1" style="table-layout: fixed; font-size: 10pt; font-family: Arial; …">
    <colgroup><col width="46"></colgroup>
    <tbody>
      <tr style="height: 21px;">
        <td style="border-width: 1px 1px 1px 2px; border-style: solid; border-color: …; overflow: hidden;
                   padding: 0px 3px; vertical-align: bottom; background-color: rgb(164, 194, 244);
                   font-size: 12pt; font-weight: bold; text-align: center;">X</td>
      </tr>
      <tr style="height: 21px;">
        <td style="border-width: …; …; background-color: rgb(164, 194, 244);"></td>
      </tr>
    </tbody>
  </table>
</google-sheets-html-origin>
```

- 每一列一個 `<tr>`，每列一個 `<td>`（單欄範圍）。
- 底色一律為 `rgb(r, g, b)` 格式；白色 `rgb(255, 255, 255)` 視為空白。
- **空白格的 `<td>` 不含字型樣式**（即使該格實際是 12pt 粗體置中）。
- 文字溢出到鄰格時，該側框線會被輸出成 `transparent`（顯示現象，非格式改變）。
- 單格複製：`<span data-sheets-root="1" style="…">值</span>`，**沒有底色**。

### 實驗室表格的字型慣例

近期 5 個工作表的預約格中：人名格 358/358 為 **12pt、粗體、置中**；空白格 5118 格同為 12pt 粗體置中（少數為 10pt 或未設定）。貼上時應以快照（xlsx）中**該格原本的字型**為準，逐格寫入樣式，讓格式與人工操作完全一致。

## 安全相關的發現（必須寫入正式實作）

1. **貼上前必須驗證選取與內容**。Spike 中曾因跳轉尚未生效就複製，取得錯誤的單格 `<span>` 並貼到 S56，清除了該格底色與字型。正式程式必須：
   - 複製後確認 HTML 是 `<table>` 且列數＝預期，否則重試（最多 ~3 秒），仍失敗則該筆 FAILED；
   - 按 Ctrl+V 前確認名稱方塊顯示的就是目標第一格；
   - 只修改「從目標範圍複製到」的 HTML，絕不貼上其他來源的內容。
2. **單一時段（1 格）的預約**：讀取時多讀下一列（2 列表格）以取得底色，貼上前刪除多出的 `<tr>`，貼上 1 列表格（實測可行）。
3. **字型**：每一格的 `<td>` 都要帶字型樣式（取自快照中該格的字型），否則會留下與人工不同的格式痕跡。
4. **競爭窗口**：傳播延遲約 0.4–0.6 秒，加上檢查到貼上的間隔（~0.4 秒），同格 1 秒內的競爭結果不可預測 → 維持寫入後驗證（`SUSPECTED_CLASH`）。

## 對設計的影響

- 規格 §7「全空 → 將名字＋整段底色的 HTML 放入剪貼簿」改為：**以即時檢查時複製到的 HTML 為底**，只改底色、第一格填名字、每格加上快照中的字型樣式，再貼上。
- `SheetWriter.read_range` 需保證回傳列數正確（含單格多讀一列的處理）；`paste_booking` 需要每格字型資訊 → `PlannedWrite` 增加每格字型（由 planner 從快照讀取）。
- 下載網址固定使用 `export?format=xlsx`。
- 其餘設計（校時、HTTPS 餘量 0.1 秒、登入方式）維持不變。

## 版本記錄（使用者於測試副本確認）

- 時間**精確到分鐘**。
- 顯示**編輯者的帳號名稱**。
- **可看出哪些儲存格被修改**（點選版本會標示變更的格子）。

→ 任何覆寫他人的情況都能被追查到帳號與分鐘，「絕不覆寫非空儲存格」與寫入前即時檢查不可放寬。

## 清理

測試寫入（V54–V56、U54–U56、R57–R59、Q60–Q66，以及被誤清除格式的 S56）已由使用者以版本記錄還原至測試前版本。
