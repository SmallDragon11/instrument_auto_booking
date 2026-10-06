"""設定頁的「連線測試」與「重新登入」（規格 §8.3）。"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from instrument_booking.browser.downloader import SnapshotDownloader, file_id_from_url
from instrument_booking.browser.session import open_login_window
from instrument_booking.core.clock import Clock, ClockSync, sync_clock
from instrument_booking.core.job import load_snapshot
from instrument_booking.core.models import TAIPEI
from instrument_booking.core.schedule import next_run, target_week
from instrument_booking.service.housekeeping import describe_error, prune_snapshots
from instrument_booking.service.occupancy import legend_for_week
from instrument_booking.service.settings import Settings, validate_settings


@dataclass(frozen=True)
class ConnectionReport:
    ok: bool
    message: str
    clock_source: str
    clock_diff: float                     # 真實時間 − 本機系統時鐘（秒）
    legend: dict[str, str] = field(default_factory=dict)


def run_connection_test(store, worker, snapshot_dir: Path, *, sync: Callable[[], ClockSync] = sync_clock,
                        local_now: Callable[[], float] = time.monotonic,
                        wall: Callable[[], float] = time.time) -> ConnectionReport:
    """校時、登入、下載表格、讀圖例；不寫入任何東西（會阻塞，請在背景執行緒呼叫）。"""
    synced = sync()
    clock = Clock(synced, local_now)
    diff = clock.now() - wall()
    settings = store.load_settings()
    problems = validate_settings(settings)
    if problems:
        return ConnectionReport(False, "；".join(problems), synced.source.value, diff)
    try:
        file_id = file_id_from_url(settings.spreadsheet_url)
        path = worker.submit(
            lambda session: SnapshotDownloader(lambda: session.request, file_id, snapshot_dir).download()
        ).result()
        wb, index = load_snapshot(path)
        now = datetime.fromtimestamp(clock.now(), TAIPEI)
        legend = legend_for_week(wb, index, target_week(next_run(now, settings.run_weekday, settings.run_time)))
        if legend:
            store.save_legend(legend)
        prune_snapshots(snapshot_dir)
    except Exception as e:
        return ConnectionReport(False, describe_error(e), synced.source.value, diff)
    return ConnectionReport(True, f"連線正常：已登入並下載預約表，圖例有 {len(legend)} 種氣體",
                            synced.source.value, diff, legend)


def start_login(worker, settings: Settings, profile_dir: Path, *, popen: Callable = subprocess.Popen):
    """先關閉自動化中的瀏覽器（同一設定檔只能有一個 Edge），再開啟一般 Edge 讓使用者登入。"""
    worker.close_session().result()
    return open_login_window(settings.spreadsheet_url, profile_dir, popen=popen)
