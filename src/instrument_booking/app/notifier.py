"""服務的 Notifier（會在背景執行緒呼叫）→ Qt signal → GUI 執行緒顯示系統匣通知。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class QtNotifier(QObject):
    notified = Signal(str, str)  # (標題, 內容)

    def notify(self, title: str, message: str) -> None:
        self.notified.emit(title, message)
