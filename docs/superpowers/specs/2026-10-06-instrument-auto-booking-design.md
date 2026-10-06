# 實驗儀器自動預約 App — 設計規格

- 日期：2026-10-06
- 狀態：待審閱
- 相關文件：[表格結構](../../表格結構.md)、[需求決策](../../需求決策.md)

## 1. 目標

一個有精美 GUI、可直接雙擊執行的 Windows 桌面 App。使用者事先在週曆上排好「下週」想預約的時段，App 在實驗室規定的開放時間（預設每週五 13:00）自動依優先序寫入實驗室的 Google 雲端預約表（管型爐 `YYYYMM`、烘箱 `M月ovenYYYY`），完成後回報結果。

### 範圍外（本版不做）

- 填寫溫度
- 自動建立尚未存在的月份工作表
- 被他人覆寫後自動搶回
- 多位使用者共用同一個 App

## 2. 使用者與情境

- 實際使用者不是開發者，只會設定一次「名字、預約時間、表單網址、Google 登入」。
- 每週在「下週預約」頁排好時段（通常數筆），之後不用管。
- 週五 13:00 時她的筆電開著、App 在執行中（開機自動啟動、常駐系統匣）。

## 3. 已確認的決策

| 議題 | 決策 |
|---|---|
| 優先序語意 | 各筆彼此獨立，全部都想要；優先序只決定搶的先後 |
| 衝突 | 一筆中任一格已被佔用 → 整筆跳過，繼續下一筆；全部處理完再回報 |
| 工作表不存在 | 該筆回報失敗 |
| 溫度 | 不填 |
| 預約表格式 | Google 雲端硬碟上的 `.xlsx`（Office 編輯模式），Sheets API 不可用 |
| 讀寫方式 | 瀏覽器自動化：Playwright 操作 Edge 專屬設定檔（使用者以個人 Google 帳號登入一次） |
| 執行環境 | 使用者的筆電，App 常駐，自行倒數計時 |
| 技術 | Python 3、PySide6 + QFluentWidgets、openpyxl、Playwright（`channel="msedge"`） |

## 4. 架構

```
app/                     GUI 層（PySide6 + QFluentWidgets）
  main_window.py           導覽：下週預約 / 執行紀錄 / 設定；系統匣；單一執行個體
  week_page.py             週曆編輯器＋優先序清單
  history_page.py          執行紀錄
  settings_page.py         設定
  scheduler.py             以 QTimer 倒數，到點觸發 BookingJob（在背景執行緒）
core/                    純邏輯；不依賴 Qt、Playwright、網路
  models.py                資料類別（§5）
  sheet_locator.py         日期＋儀器＋時段 → 工作表名稱與 A1 範圍
  legend.py                讀氣體圖例（名稱 → 色碼）、烘箱欄位色
  occupancy.py             儲存格是否為空
  planner.py               依優先序比對快照 → 寫入清單＋略過/失敗清單
  clock.py                 網路校時（NTP，失敗時用 HTTPS Date 標頭）、誤差計算
  schedule.py              下次執行時間、目標週、是否錯過
  job.py                   一次預約執行的流程編排（依賴下方介面，不依賴 Playwright）
browser/                 唯一使用 Playwright 的地方
  session.py               啟動 Edge 專屬設定檔、檢查登入狀態
  downloader.py            以登入狀態下載最新 xlsx
  sheets_writer.py         名稱方塊選取範圍、Ctrl+C 讀取文字＋底色、塗色（自訂色碼）、填名字
storage/
  json_store.py            設定、預約清單、執行紀錄（%APPDATA%\InstrumentBooking\）
```

### 介面邊界

`core/job.py` 只透過以下兩個介面接觸外界，測試時以假物件取代：

```python
class SnapshotSource(Protocol):
    def download(self) -> Path: ...                      # 回傳本機 xlsx 路徑

class SheetWriter(Protocol):
    def read_range(self, sheet: str, a1: str) -> list[CellState]: ...   # 文字＋底色
    def write_booking(self, sheet: str, a1: str, color: str, name: str) -> None: ...
```

## 5. 資料模型

```python
class Instrument(Enum):  TUBE_A, TUBE_B, TUBE_C, OVEN_A, OVEN_B
# TUBE_* 對應 YYYYMM 工作表日期合併範圍內第 0/1/2 欄；OVEN_* 對應 M月ovenYYYY 第 0/1 欄

@dataclass(frozen=True)
class BookingRequest:
    id: str
    instrument: Instrument
    date: date
    start_hour: int          # 9..23
    end_hour: int            # start_hour+1..24（24 表示到 2300~ 那格結束）
    gas: str | None          # 管型爐必填（圖例名稱，如 "Ar"）；烘箱為 None
# 優先序＝清單中的順序

@dataclass
class Settings:
    name: str                       # 寫入第一格的名字
    spreadsheet_url: str
    run_weekday: int = 4            # 週五（Monday=0）
    run_time: time = time(13, 0)
    autostart: bool = True
    minimize_to_tray: bool = True
    theme: str = "system"

class ItemStatus(Enum):
    SUCCESS            # ✅ 成功
    TAKEN_IN_SNAPSHOT  # ⏭ 快照中已被預約
    SELF_OVERLAP       # ⏭ 與自己更高優先的一筆重疊
    LIVE_CONFLICT      # ⚡ 寫入前即時檢查發現衝突
    SUSPECTED_CLASH    # ⚠️ 寫入後驗證不符（疑似撞車）
    FAILED             # ❌ 失敗（附原因：工作表不存在、氣體不存在、操作逾時…）
```

**目標週**：執行時間所在週（週一～週日）的下一週。預設週五 13:00 執行 → 預約之後的週一～週日。

## 6. 表格定位

完全依照 [表格結構](../../表格結構.md) §6，不寫死列號、欄號或工作表名稱：

1. 讀取儲存格計算後的值（`data_only=True`）。
2. 依名稱格式篩選同類工作表，以「日期」搜尋目標日所在的工作表與儲存格（工作表按週切分，跨月週屬於前一個月的表）。
3. 子欄＝日期儲存格合併範圍內的第幾欄。
4. 時段列＝日期列以下、A 欄符合時段文字（`0900~1000`…`2300~`）的列，直到下一個日期列；非時段列跳過。

一筆預約對應同一欄中連續的時段列。若中間夾有非時段列（例如誤插入的列），該列也包含在寫入範圍內，以維持塗色連續（`occupancy` 檢查同樣涵蓋）。

**空格**＝無值，且底色為無填色、`FFFFFFFF` 或主題色 0；其他一律視為已佔用。

**顏色**：管型爐由目標工作表第 2 列圖例以氣體名稱查色碼；烘箱讀 `B1`/`C1`，讀不到時用 `FFE599`（ovenA）／`A4C2F4`（ovenB）。

## 7. 執行流程

T＝預約時間（網路時間）。

```
T−10 分  預檢
         ├ 校時（NTP time.google.com；失敗 → HTTPS Date 標頭；再失敗 → 本機時間＋安全餘量 3 秒並記錄警告）
         ├ 啟動 Edge 專屬設定檔、開啟試算表；被導向登入頁 → 通知「請重新登入」
         ├ 下載 xlsx 快照 → planner 依優先序逐筆：
         │    工作表不存在 → FAILED；氣體不在圖例 → FAILED
         │    快照中已佔用 → TAKEN_IN_SNAPSHOT；與自己更高優先的一筆重疊 → SELF_OVERLAP
         └ 產出寫入清單 [(工作表, A1 範圍, 色碼, 名字)]
         失敗 → T−2 分重試一次 → 仍失敗則放棄並記錄
T−1 分   停在試算表頁面待命
T+1 秒   依優先序逐筆：
         ├ 名稱方塊輸入 '工作表'!範圍（同時切換工作表並選取）
         ├ Ctrl+C → 解析剪貼簿 HTML 的文字與底色 → 任一格非空 → LIVE_CONFLICT，跳過
         └ 全空 → 塗上該筆色碼（填滿顏色 → 自訂 → 輸入色碼）→ 選第一格 → 輸入名字
全部寫完  等 5 秒 → 逐筆 Ctrl+C 驗證：第一格＝名字且整段＝色碼 → SUCCESS，否則 SUCCESS 改為 SUSPECTED_CLASH
回報     執行紀錄＋Windows 系統通知；此週清單標記「已執行」
```

- 錯過時間（例如電腦睡眠）：醒來後立即執行，結果標記「延遲執行」。
- 寫入順序「先塗色、後填名字」：一個動作即讓整段在他人畫面上顯示為已佔用。

### 防覆寫原則

- **絕不覆寫非空儲存格**（最高原則）。
- 每筆寫入前以 Ctrl+C 即時讀取文字＋底色（不修改文件、不留編輯紀錄）。
- 剩餘風險：檢查到寫入之間約 0.5 秒以內的空檔無法消除（Google 試算表沒有鎖定機制）。發生時由寫入後驗證偵測為 `SUSPECTED_CLASH` 並通知使用者；程式不自動修改或撤銷。
- 編輯紀錄可被他人查閱，因此上述原則不可為了速度放寬。

## 8. GUI

Fluent 風格，左側導覽列三頁，支援淺色／深色／跟隨系統。

### 8.1 下週預約（週曆式）

- 頂部：目標週（例：10/12–10/18）、倒數計時（距離下次自動預約）。
- 儀器分頁：管型爐 A-牆 / B-窗 / C-小房間 / ovenA / ovenB；管型爐分頁另有氣體選擇（選項來自圖例，含色塊）。
- 週曆格：7 天 × 15 時段（09–24）。在同一天的欄中**拖曳**即新增一筆（顏色＝所選氣體或烘箱色）；點既有區塊可改氣體或刪除；同一儀器上不可與自己的區塊重疊。
- 佔用顯示：開啟頁面時於背景下載最新表格，將已被佔用的格子畫成斜線；「🔄 重新整理」按鈕重新下載。下載失敗時只顯示提示，不影響編輯。
- 右側優先序清單：每筆一列，可拖曳調整順序。
- 底部：「📋 複製上週清單（日期 +7 天）」。
- 氣體選項來源：最近一次下載的快照中，目標週所在的工作表；若尚不存在，用最新的管型爐工作表。選項快取於本機，離線也能編輯。執行時一律以目標工作表的圖例重新查色碼。

### 8.2 執行紀錄

每次執行一張卡片：預檢摘要（校時誤差與來源、登入、快照時間）＋逐筆結果（§5 的狀態與原因、寫入時間）。

### 8.3 設定

名字、自動預約時間（星期＋時:分，預設週五 13:00）、預約表網址、Google 帳號（狀態＋重新登入）、開機自動啟動並縮到系統匣（預設開）、關閉視窗時縮到系統匣（預設開）、外觀、連線測試（校時＋登入＋下載＋讀圖例，不寫入）。

安全餘量等進階參數**不開放**設定，避免誤設造成偷跑。

## 9. 錯誤處理

| 狀況 | 處理 |
|---|---|
| 偷跑防護 | 雙重：排程器只在網路時間 ≥ T+1 秒觸發；`sheets_writer` 每次寫入前再檢查，未過 T 一律拒絕 |
| 重複執行 | 單一執行個體（再次啟動只喚出既有視窗）；每個目標週只執行一次 |
| 預檢失敗 | T−10 分通知，T−2 分重試一次，仍失敗則放棄並記錄原因 |
| 瀏覽器當掉 | 重啟 Edge，從下一筆未處理的繼續；已寫入的不重寫，未處理的照常即時檢查 |
| 單筆操作逾時 | 該筆 FAILED，繼續下一筆 |
| 除錯資訊 | 本機文字紀錄檔保留 30 天；瀏覽器錯誤時存截圖（僅本機） |

## 10. 測試策略（TDD）

1. **core 單元測試（pytest）**：以 openpyxl 建立小型測試活頁簿，涵蓋 [表格結構](../../表格結構.md) §6 所有已知例外（插入列、標頭被刪、合併 4 欄、公式日期、三種白色）；planner 的各種狀態；`schedule` 與 `clock` 注入假時鐘（下次執行、錯過、偷跑防護）。
2. **歷史工作表回歸測試**：以本機 `tube furnace reservation table.xlsx`，對每個月份工作表的每個日期執行定位，驗證 15 個時段與子欄。檔案不存在時自動略過（xlsx 不進 git）。
3. **流程測試**：`job.py` 搭配假的 `SnapshotSource` / `SheetWriter`，模擬即時衝突、寫入後被覆寫、瀏覽器當掉後接續、偷跑防護。
4. **GUI**：拖曳選取 → `BookingRequest` 的換算抽成純函式測試；畫面僅做少量 pytest-qt 冒煙測試。
5. **瀏覽器整合測試**：標記為手動執行，只操作測試副本，絕不對實驗室正式表單執行。

## 11. 打包與發佈

- PyInstaller（onedir），產出可雙擊執行的 `.exe` 資料夾。
- 使用 Windows 11 內建 Edge（`channel="msedge"`），不打包 Chromium。
- Edge 專屬設定檔位於 `%LOCALAPPDATA%\InstrumentBooking\edge-profile`；設定與紀錄位於 `%APPDATA%\InstrumentBooking\`。
- 開機自動啟動：於使用者啟動資料夾建立捷徑（可由設定關閉）。

## 12. 實作前的驗證程式（Spike）

正式開發前，以一次性程式在使用者電腦上、對**測試副本**確認：

1. Edge 專屬設定檔登入 Google 後，Playwright 能沿用登入狀態，不被「此瀏覽器可能不安全」擋下。
2. Office 編輯模式下，名稱方塊可用 `'工作表'!範圍` 跳轉並選取。
3. Ctrl+C 後剪貼簿 HTML 含每格文字與背景色，讀取時間 < 1 秒。
4. 填滿顏色 → 自訂 → 輸入色碼可塗上精確顏色；輸入名字正常。
5. 以登入狀態下載的 xlsx 反映最新編輯（確認存檔延遲）。
6. 兩個瀏覽器同時編輯不同格／同一格時的合併行為。

備案：(1) 失敗 → 改為連接使用者平常已登入的 Edge；(3) 讀不到底色 → 寫入前重新下載 xlsx 並只解析目標工作表（較慢，空檔變大，需重新評估）。任何項目不可行時回到設計階段調整。
