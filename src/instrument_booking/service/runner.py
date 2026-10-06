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


@dataclass(frozen=True)
class Prepared:
    """預檢結果：規劃好的寫入清單與校時結果。"""
    plan: Plan
    sync: ClockSync
    clock: Clock
    prepared_at: datetime

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
        """校時、開啟瀏覽器（保持開啟到寫入）、下載快照並規劃。失敗時拋出例外。"""
        sync = self._sync()
        clock = Clock(sync, self._local_now)
        now = datetime.fromtimestamp(clock.now(), TAIPEI)
        file_id = file_id_from_url(settings.spreadsheet_url)

        def job(session):
            source = SnapshotDownloader(lambda: session.request, file_id, self._snapshot_dir)
            return preflight(source, requests, now)

        plan = self._worker.submit(job, keep_open=True).result()
        prune_snapshots(self._snapshot_dir)
        return Prepared(plan, sync, clock, now)

    def run(self, prepared: Prepared, settings: Settings, run_at: datetime) -> tuple[ItemResult, ...]:
        """預熱、等到開放時間＋安全餘量、依優先序寫入並驗證；結束後關閉瀏覽器。"""
        not_before = fire_at(run_at, prepared.sync)

        def job(session):
            writer = self._writer_factory(session.sheet_page(), restart=session.restart_sheet_page,
                                          clock=prepared.clock, not_before=not_before)
            return execute(prepared.plan, writer, settings.name, prepared.clock, not_before, sleep=self._sleep)

        return tuple(self._worker.submit(job, keep_open=False).result())

    def abandon(self) -> None:
        """預檢後不寫入（例如放棄）時關閉瀏覽器。"""
        self._worker.close_session().result()
