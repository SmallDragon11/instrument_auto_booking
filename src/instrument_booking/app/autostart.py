"""開機自動啟動（規格 §8.3、§11）：寫入目前使用者的 Run 登錄機碼，以 --background 啟動（只出現在系統匣）。

只有打包後的 exe 才註冊；以 python 執行（開發中）時不改動登錄，避免把開發環境設成開機啟動。
"""
from __future__ import annotations

import logging
import sys
from typing import Protocol

from instrument_booking.service.housekeeping import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "InstrumentBooking"
BACKGROUND_ARG = "--background"


class Registry(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class RunKey:
    """HKEY_CURRENT_USER\\...\\Run（不需要系統管理員權限）。"""

    def get(self, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, name)
                return value
        except FileNotFoundError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def launch_command(*, frozen: bool | None = None, executable: str | None = None) -> str | None:
    """打包後的 exe：「"…\\InstrumentBooking.exe" --background」；開發中為 None。"""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return None
    return f'"{executable or sys.executable}" {BACKGROUND_ARG}'


def apply_autostart(enabled: bool, *, command: str | None, registry: Registry) -> None:
    """與設定同步（已一致時不寫入）；登錄失敗拋 OSError。"""
    if command is None:
        log.info("開發模式：不設定開機自動啟動")
        return
    current = registry.get(VALUE_NAME)
    if enabled and current != command:
        registry.set(VALUE_NAME, command)
    elif not enabled and current is not None:
        registry.delete(VALUE_NAME)
