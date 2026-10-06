"""下週預約頁的佔用顯示與氣體選項（規格 §8.1）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from instrument_booking.browser.downloader import SnapshotDownloader
from instrument_booking.core.job import load_snapshot
from instrument_booking.core.legend import read_tube_legend
from instrument_booking.core.models import Instrument
from instrument_booking.core.occupancy import cell_state, is_empty
from instrument_booking.core.sheet_locator import ALL_HOURS, LocateError, SheetIndex, sheet_kind
from instrument_booking.service.housekeeping import prune_snapshots

Slot = tuple[Instrument, date, int]  # (儀器, 日期, 起始小時)


@dataclass(frozen=True)
class WeekView:
    monday: date
    occupied: frozenset[Slot]                    # 快照中已被佔用的時段
    unavailable: frozenset[tuple[str, date]]     # (kind, 日期)：找不到工作表或日期重複，無法預約
    legend: dict[str, str]                       # 氣體名稱 → 色碼（空＝找不到任何管型爐表）


def week_days(monday: date) -> list[date]:
    return [monday + timedelta(days=i) for i in range(7)]


def legend_for_week(wb, index: SheetIndex, monday: date) -> dict[str, str]:
    """目標週所在管型爐表的圖例；目標週的表尚未建立時，用日期最新的管型爐表。"""
    for d in week_days(monday):
        try:
            return read_tube_legend(wb[index.day("tube", d).sheet])
        except LocateError:
            continue
    tube_blocks = [b for b in index.all_blocks() if sheet_kind(b.sheet) == "tube"]
    if not tube_blocks:
        return {}
    latest = max(tube_blocks, key=lambda b: b.date)
    return read_tube_legend(wb[latest.sheet])


def week_view(wb, index: SheetIndex, monday: date) -> WeekView:
    occupied: set[Slot] = set()
    unavailable: set[tuple[str, date]] = set()
    for kind in ("tube", "oven"):
        instruments = [i for i in Instrument if i.kind == kind]
        for d in week_days(monday):
            try:
                block = index.day(kind, d)
            except LocateError:
                unavailable.add((kind, d))
                continue
            ws = wb[block.sheet]
            for inst in instruments:
                col = block.first_col + inst.sub_index
                for hour in ALL_HOURS:
                    if not is_empty(cell_state(ws.cell(block.row_of(hour), col))):
                        occupied.add((inst, d, hour))
    return WeekView(monday, frozenset(occupied), frozenset(unavailable), legend_for_week(wb, index, monday))


class OccupancyService:
    """以瀏覽器的登入狀態下載最新快照並計算某一週的佔用（會阻塞，請在背景執行緒呼叫）。"""

    def __init__(self, worker, *, file_id: Callable[[], str], snapshot_dir: Path, store) -> None:
        self._worker = worker
        self._file_id = file_id
        self._snapshot_dir = snapshot_dir
        self._store = store

    def refresh(self, monday: date) -> WeekView:
        file_id = self._file_id()
        path = self._worker.submit(
            lambda session: SnapshotDownloader(lambda: session.request, file_id, self._snapshot_dir).download()
        ).result()
        wb, index = load_snapshot(path)
        view = week_view(wb, index, monday)
        if view.legend:
            self._store.save_legend(view.legend)
        prune_snapshots(self._snapshot_dir)
        return view
