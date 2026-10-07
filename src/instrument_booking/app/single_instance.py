"""單一執行個體：第二個實例只通知第一個顯示視窗後就結束（規格 §9；兩個實例會搶同一個 Edge 設定檔與排程）。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

CONNECT_TIMEOUT_MS = 500


class SingleInstance(QObject):
    activated = Signal()  # 另一個實例啟動了：請顯示視窗

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """成為唯一的實例回傳 True；已有實例在執行時通知它並回傳 False。

        同時啟動多個實例時，探查失敗的實例會再試一次，以便同時發起的實例之間能正確握手。
        """
        # 第一次嘗試連接到現有實例
        if self._notify_existing():
            return False

        QLocalServer.removeServer(self._key)  # 前一次異常結束留下的名稱
        self._server = QLocalServer(self)
        if not self._server.listen(self._key):
            # 競速時，另一個實例可能剛好搶到名稱；再試一次連接
            self._server.close()
            self._server = None
            if self._notify_existing():
                return False
            raise RuntimeError(f"無法建立單一執行個體的通道")
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

    def _on_connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.close()  # 連上就代表要顯示視窗，不需要讀內容；立即關閉，不留著管道
            conn.deleteLater()
            self.activated.emit()
