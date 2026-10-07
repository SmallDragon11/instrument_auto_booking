"""唯一操作瀏覽器的執行緒（Playwright 同步物件只能在建立它的執行緒使用；同一設定檔同時只能有一個 Edge）。"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Protocol, TypeVar

T = TypeVar("T")


class Session(Protocol):
    """EdgeSession 的最小介面。"""

    def start(self): ...
    def close(self) -> None: ...


class BrowserWorker:
    """所有瀏覽器工作排入同一條執行緒依序執行；session 在第一個工作時才啟動。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")
        self._factory = session_factory
        self._session: Session | None = None
        self._hold = False  # 保留中：預檢後待命寫入的 Edge 不可被其他工作關閉（只在瀏覽器執行緒讀寫）
        self._shutdown_lock = threading.Lock()
        self._shut_down = False

    def submit(self, job: Callable[[Session], T], *, keep_open: bool = False,
               hold: bool | None = None) -> Future[T]:
        """排入工作；keep_open=False 時工作結束（含失敗）後關閉瀏覽器，但「保留中」時不關閉。

        hold=True 設定保留中、hold=False 解除（在工作開始前生效）、None 不改變。
        """
        return self._executor.submit(self._run, job, keep_open, hold)

    def close_session(self) -> Future[None]:
        """關閉瀏覽器並解除保留（使用者明確要求，例如開啟登入視窗前）；排在已排入的工作之後執行。"""
        return self._executor.submit(self._close)

    def shutdown(self) -> None:
        """關閉瀏覽器並停止執行緒；可重複呼叫（第二次起不做任何事）。"""
        with self._shutdown_lock:
            if self._shut_down:
                return
            self._shut_down = True
        self.close_session().result()
        self._executor.shutdown(wait=True)

    def _run(self, job: Callable[[Session], T], keep_open: bool, hold: bool | None) -> T:
        if hold is not None:
            self._hold = hold
        try:
            if self._session is None:
                session = self._factory()
                session.start()
                self._session = session
            return job(self._session)
        finally:
            if not keep_open and not self._hold:
                self._close()

    def _close(self) -> None:
        self._hold = False
        session, self._session = self._session, None
        if session is not None:
            session.close()
