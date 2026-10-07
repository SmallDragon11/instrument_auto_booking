"""單一執行個體：第二個實例只通知第一個顯示視窗後就結束（規格 §9；兩個實例會搶同一個 Edge 設定檔與排程）。"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

CONNECT_TIMEOUT_MS = 500
NOTIFY_ATTEMPTS = 10
NOTIFY_RETRY_DELAY = 0.2  # 秒：對方可能剛取得鎖、還沒開始監聽


class SingleInstance(QObject):
    activated = Signal()  # 另一個實例啟動了：請顯示視窗

    def __init__(self, key: str, parent: QObject | None = None, *, lock_path: Path | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None
        self._lock_path = lock_path or Path(tempfile.gettempdir()) / f"{key}.lock"
        self._lock: QLockFile | None = None

    def acquire(self) -> bool:
        """成為唯一的實例回傳 True；已有實例在執行時通知它並回傳 False。

        單一執行個體由檔案鎖保證（Windows 的具名管道允許同名多個伺服器），管道只用來通知已在執行的實例。
        """
        lock = QLockFile(str(self._lock_path))
        lock.setStaleLockTime(0)  # 不以時間判斷過期；擁有者的行程已結束時 Qt 會自動視為過期
        if not lock.tryLock(0):
            # 另一個實例持有鎖：通知它顯示視窗（它可能剛啟動、尚未開始監聽，重試幾次）
            for _ in range(NOTIFY_ATTEMPTS):
                if self._notify_existing():
                    break
                time.sleep(NOTIFY_RETRY_DELAY)
            return False
        self._lock = lock
        QLocalServer.removeServer(self._key)  # 前一次異常結束留下的名稱
        self._server = QLocalServer(self)
        if not self._server.listen(self._key):
            error = self._server.errorString()
            self._server = None
            lock.unlock()
            self._lock = None
            raise RuntimeError(f"無法建立單一執行個體的通道：{error}")
        self._server.newConnection.connect(self._on_connection)
        return True

    def _notify_existing(self) -> bool:
        """嘗試通知現有實例；連接成功則傳送訊號並回傳 True，否則回傳 False。"""
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if socket.waitForConnected(CONNECT_TIMEOUT_MS):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
            socket.disconnectFromServer()
            return True
        return False

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

    def _on_connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.close()  # 連上就代表要顯示視窗，不需要讀內容；立即關閉，不留著管道
            conn.deleteLater()
            self.activated.emit()
