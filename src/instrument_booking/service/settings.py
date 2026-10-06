"""使用者設定與驗證（規格 §5、§8.3）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from instrument_booking.browser.downloader import file_id_from_url

THEMES = ("system", "light", "dark")


class NotConfigured(Exception):
    """設定尚未完成或無效，無法進行需要設定的動作（訊息為給使用者看的繁體中文）。"""


@dataclass(frozen=True)
class Settings:
    name: str = ""                 # 寫入預約第一格的名字
    spreadsheet_url: str = ""
    run_weekday: int = 4           # 週五（週一＝0）
    run_time: time = time(13, 0)
    autostart: bool = True
    minimize_to_tray: bool = True
    theme: str = "system"


def validate_settings(s: Settings) -> list[str]:
    """回傳所有問題（繁體中文）；空清單表示設定可用於自動預約。"""
    errors = []
    if not s.name.strip():
        errors.append("請填寫要寫入表格的名字")
    elif s.name != s.name.strip():
        errors.append("名字前後不可有空白")
    elif any(c in s.name for c in "\t\r\n"):
        errors.append("名字不可包含 Tab 或換行")
    try:
        file_id_from_url(s.spreadsheet_url)
    except ValueError:
        errors.append("預約表網址必須是 Google 試算表網址")
    if not 0 <= s.run_weekday <= 6:
        errors.append("自動預約的星期不正確")
    if s.run_time.second or s.run_time.microsecond:
        errors.append("自動預約時間只能設定到分鐘")
    if s.theme not in THEMES:
        errors.append("外觀設定不正確")
    return errors


def require_configured(s: Settings) -> Settings:
    """設定有問題時拋 NotConfigured（「請先完成設定：…」）；否則原樣回傳。"""
    problems = validate_settings(s)
    if problems:
        raise NotConfigured("請先完成設定：" + "；".join(problems))
    return s
