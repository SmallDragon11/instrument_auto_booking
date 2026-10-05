# CLAUDE.md

## 規範

1. **語言**：一律以繁體中文回答。
2. **開發流程**：遵守 TDD（測試驅動開發）——先寫會失敗的測試，再寫最少的程式碼讓測試通過，最後重構。
3. **驗證**：驗證時，必須確認程式定位表格的方式（例如日期欄、A-牆/B-窗/C-小房間欄、ovenA/ovenB 欄、時段列、顏色圖例位置）能套用到**所有工作表**，包含歷史工作表（各月份的 `YYYYMM` 與 `MM月ovenYYYY`），而不只是當月的工作表。

## 測試資料

- 測試用 Google 試算表：https://docs.google.com/spreadsheets/d/1621E29JjZYnJGj1lnLgJ-GiU_bCVJCdh/edit?gid=701232019#gid=701232019
- 本地副本：`tube furnace reservation table.xlsx`
