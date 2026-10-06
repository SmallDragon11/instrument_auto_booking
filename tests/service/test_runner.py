from datetime import date, datetime

import pytest

from instrument_booking.core.clock import ClockSource, ClockSync
from instrument_booking.core.models import TAIPEI, BookingRequest, CellState, Instrument, ItemStatus
from instrument_booking.browser.downloader import DownloadError
from instrument_booking.browser.sheets_writer import BrowserSheetWriter
from instrument_booking.service.runner import BookingRunner
from instrument_booking.service.settings import Settings
from fake_sheet_page import FakeSheetPage
from service.fakes import FakeSession, InlineWorker, XlsxRequest
from sheet_builder import add_tube_sheet, new_workbook

MON = date(2026, 10, 12)
RUN_AT = datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)
T0 = RUN_AT.timestamp()
SETTINGS = Settings(name="Zoe", spreadsheet_url="https://docs.google.com/spreadsheets/d/FILEID/edit")
REQ = BookingRequest("a", Instrument.TUBE_A, MON, 13, 15, "Ar", "A4C2F4")


class FakeTime:
    """單調時鐘＋sleep：sleep 會推進時間。"""

    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(tmp_path, page=None, sync_offset=None):
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON)
    session = FakeSession([], request=XlsxRequest(wb))
    page = page or FakeSheetPage()
    session.sheet_page = lambda: page
    session.restart_sheet_page = lambda: page
    worker = InlineWorker(session)
    clock = FakeTime(1000.0)
    offset = (T0 - 600) - 1000.0 if sync_offset is None else sync_offset  # 預檢時＝T−10 分
    runner = BookingRunner(worker, snapshot_dir=tmp_path / "snap",
                           sync=lambda: ClockSync(ClockSource.NTP, offset), sleep=clock.sleep,
                           local_now=clock.now,
                           writer_factory=lambda p, **kw: BrowserSheetWriter(p, monotonic=page.now, **kw))
    return runner, worker, page, clock


def test_prepare_syncs_downloads_and_plans_keeping_browser_open(tmp_path):
    runner, worker, _, _ = make(tmp_path)
    prepared = runner.prepare(SETTINGS, [REQ])
    assert [w.target.a1 for w in prepared.plan.writes] == ["B10:B11"]
    assert prepared.sync.source is ClockSource.NTP
    assert prepared.prepared_at == datetime(2026, 10, 9, 12, 50, tzinfo=TAIPEI)
    assert worker.jobs == [True]  # 瀏覽器保持開啟到寫入


def test_run_waits_for_opening_time_then_writes_and_closes_browser(tmp_path):
    runner, worker, page, clock = make(tmp_path)
    prepared = runner.prepare(SETTINGS, [REQ])
    (result,) = runner.run(prepared, SETTINGS, RUN_AT)
    assert result.status is ItemStatus.SUCCESS
    assert page.cells[("202610", "B10")] == CellState("Zoe", "A4C2F4")
    assert result.written_at.timestamp() >= T0 + 0.05  # NTP 餘量 0.05 秒之後才寫入
    assert worker.jobs == [True, False]


def test_prepare_failure_propagates(tmp_path):
    runner, worker, _, _ = make(tmp_path)
    worker.session.request = None  # 例如瀏覽器沒有登入狀態
    with pytest.raises(DownloadError):
        runner.prepare(SETTINGS, [REQ])


def test_abandon_closes_browser(tmp_path):
    runner, worker, _, _ = make(tmp_path)
    runner.prepare(SETTINGS, [REQ])
    runner.abandon()
    assert worker.closed == 1
