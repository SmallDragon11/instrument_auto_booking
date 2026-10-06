from datetime import date, datetime

import pytest

from instrument_booking.core.clock import ClockSource, ClockSync
from instrument_booking.core.models import TAIPEI, BookingRequest, CellState, Instrument, ItemStatus
from instrument_booking.browser.downloader import DownloadError
from instrument_booking.browser.sheets_writer import BrowserSheetWriter
from instrument_booking.service.occupancy import OccupancyService
from instrument_booking.service.runner import BookingRunner, RunCancelled, RunnerError
from instrument_booking.service.worker import BrowserWorker
from instrument_booking.service.settings import Settings
from instrument_booking.storage.json_store import JsonStore
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


def make(tmp_path):
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON)
    session = FakeSession([], request=XlsxRequest(wb))
    page = FakeSheetPage()
    session.sheet_page = lambda: page
    session.restart_sheet_page = lambda: page
    worker = InlineWorker(session)
    clock = FakeTime(1000.0)
    offset = (T0 - 600) - 1000.0  # 預檢時＝T−10 分
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
    assert prepared.file_id == "FILEID"  # 規劃綁定試算表
    assert worker.jobs == [True]  # 瀏覽器保持開啟到寫入
    assert worker.holds == [True]  # 保留到寫入，期間其他工作不會關閉它


def test_run_waits_for_opening_time_then_writes_and_closes_browser(tmp_path):
    runner, worker, page, clock = make(tmp_path)
    prepared = runner.prepare(SETTINGS, [REQ])
    (result,) = runner.run(prepared, SETTINGS, RUN_AT)
    assert result.status is ItemStatus.SUCCESS
    assert page.cells[("202610", "B10")] == CellState("Zoe", "A4C2F4")
    assert result.written_at.timestamp() >= T0 + 0.05  # NTP 餘量 0.05 秒之後才寫入
    assert worker.jobs == [True, False]
    assert worker.holds == [True, False]  # 寫入時解除保留，結束後關閉


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


@pytest.mark.parametrize("url", ["https://docs.google.com/spreadsheets/d/OTHER/edit", "about:blank"])
def test_run_refuses_when_browser_shows_another_spreadsheet(tmp_path, url):
    runner, worker, page, _ = make(tmp_path)
    prepared = runner.prepare(SETTINGS, [REQ])
    worker.session.url = url  # 預檢之後網址被改變（瀏覽器已用新網址重新開啟）
    with pytest.raises(RunnerError, match="預約表網址在預檢之後被改變，為安全起見不寫入"):
        runner.run(prepared, SETTINGS, RUN_AT)
    assert page.pasted == [] and page.log == []  # 沒有建立寫入器、沒有任何操作


def test_prepare_refuses_when_browser_shows_another_spreadsheet(tmp_path):
    runner, worker, _, _ = make(tmp_path)
    worker.session.url = "https://docs.google.com/spreadsheets/d/OTHER/edit"
    with pytest.raises(RunnerError):
        runner.prepare(SETTINGS, [REQ])
    assert worker.session.request.urls == []  # 不下載、不規劃


def test_other_browser_work_after_prepare_keeps_standby_edge_open(tmp_path):
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON)
    log = []
    worker = BrowserWorker(lambda: FakeSession(log, request=XlsxRequest(wb)))
    try:
        runner = BookingRunner(worker, snapshot_dir=tmp_path / "snap",
                               sync=lambda: ClockSync(ClockSource.NTP, (T0 - 600) - 1000.0),
                               local_now=lambda: 1000.0)
        store = JsonStore(tmp_path / "data")
        store.save_settings(SETTINGS)
        occupancy = OccupancyService(worker, snapshot_dir=tmp_path / "snap", store=store)
        runner.prepare(SETTINGS, [REQ])
        occupancy.refresh(MON)  # T−10～T−1 之間使用者開啟「下週預約」頁
        assert [e for e, _ in log] == ["start"]  # 待命的 Edge 沒有被關閉
        runner.abandon()
        assert [e for e, _ in log] == ["start", "close"]
    finally:
        worker.shutdown()


# --- 寫入前可取消（偷跑防護）---

class Guard:
    """記錄每次被呼叫時的（本機）時間；cancel_after 之後回傳取消原因。"""

    def __init__(self, clock, cancel_after=None, reason="自動預約時間已變更，取消本次寫入", page=None):
        self.clock, self.cancel_after, self.reason, self.page = clock, cancel_after, reason, page
        self.calls = []
        self.pasted_before = []  # 每次被呼叫時已貼上的筆數

    def __call__(self):
        self.calls.append(self.clock.t)
        if self.page is not None:
            self.pasted_before.append(len(self.page.pasted))
        if self.cancel_after is not None and self.clock.t >= self.cancel_after:
            return self.reason
        return None


def test_guard_cancelling_at_job_start_writes_nothing(tmp_path):
    runner, worker, page, clock = make(tmp_path)
    prepared = runner.prepare(SETTINGS, [REQ])
    guard = Guard(clock, cancel_after=0, reason="已手動取消")
    with pytest.raises(RunCancelled, match="已手動取消") as info:
        runner.run(prepared, SETTINGS, RUN_AT, guard=guard)
    assert isinstance(info.value, RunnerError)
    assert guard.calls == [1000.0]  # job 一開始就檢查
    assert page.pasted == [] and page.log == []  # 沒有建立寫入器、沒有任何操作
    assert worker.holds == [True, False]  # 取消後照常關閉 Edge


def test_guard_cancelling_while_waiting_for_opening_time_writes_nothing(tmp_path):
    runner, worker, page, clock = make(tmp_path)
    prepared = runner.prepare(SETTINGS, [REQ])
    guard = Guard(clock, cancel_after=1300.0)  # 等待到一半時設定被改變
    with pytest.raises(RunCancelled, match="自動預約時間已變更"):
        runner.run(prepared, SETTINGS, RUN_AT, guard=guard)
    assert page.pasted == []
    assert ("202610", "B10") not in page.cells
    assert 1300.0 <= clock.t < 1301.0  # 在下一個刻度就停止等待


def test_guard_is_checked_every_tick_until_opening_time_but_never_after(tmp_path):
    runner, worker, page, clock = make(tmp_path)
    prepared = runner.prepare(SETTINGS, [REQ])
    guard = Guard(clock, page=page)
    (result,) = runner.run(prepared, SETTINGS, RUN_AT, guard=guard)
    assert result.status is ItemStatus.SUCCESS
    assert guard.calls[0] == 1000.0
    assert len(guard.calls) > 600 / 0.05  # 等待期間每個刻度都檢查
    assert len(page.pasted) == 1 and set(guard.pasted_before) == {0}  # 開放時間到了之後（寫入、驗證等待）不再檢查
    assert clock.t > 1000.0 + 600 + 4.9  # 有經過寫入後的 5 秒驗證等待


def test_preparing_again_reuses_the_standby_edge(tmp_path):
    # 預檢後清單被修改 → 服務不關閉 Edge、直接重新預檢：沿用保留中的同一個 session
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON)
    log = []
    worker = BrowserWorker(lambda: FakeSession(log, request=XlsxRequest(wb)))
    try:
        runner = BookingRunner(worker, snapshot_dir=tmp_path / "snap",
                               sync=lambda: ClockSync(ClockSource.NTP, (T0 - 600) - 1000.0),
                               local_now=lambda: 1000.0)
        runner.prepare(SETTINGS, [REQ])
        runner.prepare(SETTINGS, [])
        assert [e for e, _ in log] == ["start"]
    finally:
        worker.shutdown()
