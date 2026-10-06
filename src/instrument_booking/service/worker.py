"""唯一操作瀏覽器的執行緒（Playwright 同步物件只能在建立它的執行緒使用；同一設定檔同時只能有一個 Edge）。"""
from __future__ import annotations

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

    def submit(self, job: Callable[[Session], T], *, keep_open: bool = False) -> Future[T]:
        """排入工作；keep_open=False 時工作結束（含失敗）後關閉瀏覽器。"""
        return self._executor.submit(self._run, job, keep_open)

    def close_session(self) -> Future[None]:
        """關閉瀏覽器（例如開啟登入視窗前）；排在已排入的工作之後執行。"""
        return self._executor.submit(self._close)

    def shutdown(self) -> None:
        self.close_session().result()
        self._executor.shutdown(wait=True)

    def _run(self, job: Callable[[Session], T], keep_open: bool) -> T:
        try:
            if self._session is None:
                session = self._factory()
                session.start()
                self._session = session
            return job(self._session)
        finally:
            if not keep_open:
                self._close()

    def _close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            session.close()
