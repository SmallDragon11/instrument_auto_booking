"""把 core 與瀏覽器層接起來：預檢（校時＋下載快照＋規劃）與到點寫入（規格 §7）。

所有瀏覽器工作都經由 BrowserWorker 在同一條執行緒執行；寫入器與 execute 用同一個 Clock 與同一個開放時間。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from instrument_booking.browser.downloader import SnapshotDownloader, file_id_from_url
from instrument_booking.browser.sheets_writer import BrowserSheetWriter
from instrument_booking.core.clock import Clock, ClockSync, sync_clock
from instrument_booking.core.job import execute, preflight
from instrument_booking.core.models import TAIPEI, BookingRequest, ItemResult
from instrument_booking.core.planner import Plan
from instrument_booking.core.schedule import fire_at
from instrument_booking.service.housekeeping import prune_snapshots
from instrument_booking.service.settings import Settings


class RunnerError(Exception):
    """為安全起見拒絕預檢或寫入（訊息為給使用者看的繁體中文）。"""


URL_CHANGED = "預約表網址在預檢之後被改變，為安全起見不寫入"


def _session_file_id(session) -> str | None:
    """瀏覽器開啟的試算表；不是 Google 試算表網址時為 None。"""
    try:
        return file_id_from_url(session.url)
    except ValueError:
        return None


@dataclass(frozen=True)
class Prepared:
    """預檢結果：規劃好的寫入清單與校時結果；規劃只適用於 file_id 這份試算表。"""
    plan: Plan
    sync: ClockSync
    clock: Clock
    prepared_at: datetime
    file_id: str

    @property
    def clock_diff(self) -> float:
        """真實時間 − 本機系統時鐘（秒），供執行紀錄顯示。"""
        return self.clock.now() - time.time()


class BookingRunner:
    def __init__(self, worker, *, snapshot_dir: Path, sync: Callable[[], ClockSync] = sync_clock,
                 writer_factory: Callable = BrowserSheetWriter, sleep: Callable[[float], None] = time.sleep,
                 local_now: Callable[[], float] = time.monotonic) -> None:
        self._worker = worker
        self._snapshot_dir = snapshot_dir
        self._sync = sync
        self._writer_factory = writer_factory
        self._sleep = sleep
        self._local_now = local_now

    def prepare(self, settings: Settings, requests: Sequence[BookingRequest]) -> Prepared:
        """校時、開啟瀏覽器（保留到寫入或放棄）、下載快照並規劃。失敗時拋出例外。"""
        sync = self._sync()
        clock = Clock(sync, self._local_now)
        now = datetime.fromtimestamp(clock.now(), TAIPEI)
        file_id = file_id_from_url(settings.spreadsheet_url)

        def job(session):
            if _session_file_id(session) != file_id:
                # 瀏覽器開的是另一份試算表（網址剛被改變）：快照與寫入的頁面不一致，不可規劃
                raise RunnerError("瀏覽器開啟的試算表與設定的預約表網址不同，請稍後再試")
            source = SnapshotDownloader(lambda: session.request, file_id, self._snapshot_dir)
            return preflight(source, requests, now)

        plan = self._worker.submit(job, keep_open=True, hold=True).result()
        prune_snapshots(self._snapshot_dir)
        return Prepared(plan, sync, clock, now, file_id)

    def run(self, prepared: Prepared, settings: Settings, run_at: datetime) -> tuple[ItemResult, ...]:
        """預熱、等到開放時間＋安全餘量、依優先序寫入並驗證；結束後關閉瀏覽器。"""
        not_before = fire_at(run_at, prepared.sync)

        def job(session):
            if _session_file_id(session) != prepared.file_id:
                raise RunnerError(URL_CHANGED)  # 規劃是依另一份試算表的快照做的，絕不寫入
            writer = self._writer_factory(session.sheet_page(), restart=session.restart_sheet_page,
                                          clock=prepared.clock, not_before=not_before)
            return execute(prepared.plan, writer, settings.name, prepared.clock, not_before, sleep=self._sleep)

        return tuple(self._worker.submit(job, keep_open=False, hold=False).result())

    def abandon(self) -> None:
        """預檢後不寫入（例如放棄、規劃被丟棄）時關閉瀏覽器並解除保留。"""
        self._worker.close_session().result()
