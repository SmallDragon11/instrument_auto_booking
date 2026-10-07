"""在背景執行緒執行會阻塞的工作，完成後把結果送回 GUI 執行緒（規格：GUI 執行緒不可阻塞）。"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from instrument_booking.service.housekeeping import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME)


class BackgroundTasks(QObject):
    """run(fn, on_done, on_error)：fn 在背景執行；on_done(結果) 或 on_error(例外) 一定在 GUI 執行緒呼叫。"""

    _deliver = Signal(object, object)  # (回呼, 參數)；跨執行緒 emit 會排入 GUI 執行緒的事件佇列

    def __init__(self, parent: QObject | None = None, max_workers: int = 2) -> None:
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gui-task")
        self._deliver.connect(self._call)

    def run(self, fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[Exception], None] | None = None) -> Future:
        def job():
            try:
                result = fn()
            except Exception as e:
                log.warning("背景工作失敗：%s", e)
                if on_error is not None:
                    self._deliver.emit(on_error, e)
                return None
            if on_done is not None:
                self._deliver.emit(on_done, result)
            return result
        return self._pool.submit(job)

    def shutdown(self) -> None:
        """不等待進行中的工作（例如下載中）；結束程式時呼叫。"""
        self._pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _call(callback, arg) -> None:
        callback(arg)
