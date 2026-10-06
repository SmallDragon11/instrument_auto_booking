from datetime import date

import pytest

from instrument_booking.core.clock import ClockSource, ClockSync
from instrument_booking.service.connection import run_connection_test, start_login
from instrument_booking.service.settings import NotConfigured, Settings
from instrument_booking.storage.json_store import JsonStore
from service.fakes import FakeSession, InlineWorker, XlsxRequest
from sheet_builder import add_oven_sheet, add_tube_sheet, new_workbook

URL = "https://docs.google.com/spreadsheets/d/FILEID/edit"
NOW_EPOCH = 1_791_522_000.0 - 3 * 86400  # 2026-10-06（二）13:00 台北


def make(tmp_path, *, settings=Settings(name="Zoe", spreadsheet_url=URL), request="xlsx", tube=True):
    store = JsonStore(tmp_path / "data")
    store.save_settings(settings)
    wb = new_workbook()
    if tube:
        add_tube_sheet(wb, "202610", date(2026, 10, 12), legend=[("Ar", "A4C2F4")])
    else:
        add_oven_sheet(wb, "10月oven2026", date(2026, 10, 12))
    session = FakeSession([], request=XlsxRequest(wb) if request == "xlsx" else request)
    worker = InlineWorker(session)
    kwargs = dict(sync=lambda: ClockSync(ClockSource.NTP, NOW_EPOCH - 50.0), local_now=lambda: 50.0,
                  wall=lambda: NOW_EPOCH - 0.6)
    return store, worker, kwargs


def test_successful_connection_test_reads_and_caches_legend(tmp_path):
    store, worker, kwargs = make(tmp_path)
    report = run_connection_test(store, worker, tmp_path / "snap", **kwargs)
    assert report.ok and "1 種氣體" in report.message
    assert report.clock_source == "NTP"
    assert report.clock_diff == pytest.approx(0.6)  # 本機時鐘慢 0.6 秒
    assert report.legend == {"Ar": "A4C2F4"} == store.load_legend()
    assert worker.jobs == [False]


def test_unconfigured_settings_are_reported_without_browser(tmp_path):
    store, worker, kwargs = make(tmp_path, settings=Settings())

    def no_sync():
        raise AssertionError("設定無效時不可做網路校時")
    report = run_connection_test(store, worker, tmp_path / "snap", **{**kwargs, "sync": no_sync})
    assert not report.ok and "名字" in report.message
    assert (report.clock_source, report.clock_diff) == ("未校時", 0.0)
    assert worker.jobs == []


def test_download_failure_is_reported_in_chinese(tmp_path):
    store, worker, kwargs = make(tmp_path, request=None)
    report = run_connection_test(store, worker, tmp_path / "snap", **kwargs)
    assert not report.ok and "下載快照失敗" in report.message


def test_start_login_closes_automation_browser_first(tmp_path):
    worker = InlineWorker(FakeSession([]))
    calls = []
    start_login(worker, Settings(name="Zoe", spreadsheet_url=URL), tmp_path / "profile", popen=calls.append)
    assert worker.closed == 1
    (args,) = calls
    assert args[-1] == URL and f"--user-data-dir={tmp_path / 'profile'}" in args


def test_snapshot_without_gas_legend_is_not_ok(tmp_path):
    store, worker, kwargs = make(tmp_path, tube=False)
    report = run_connection_test(store, worker, tmp_path / "snap", **kwargs)
    assert not report.ok
    assert report.message == "已下載預約表，但找不到氣體圖例"
    assert report.legend == {} and store.load_legend() == {}


def test_start_login_without_spreadsheet_url_opens_google_sign_in(tmp_path):
    worker = InlineWorker(FakeSession([]))
    calls = []
    start_login(worker, Settings(), tmp_path / "profile", popen=calls.append)
    assert worker.closed == 1
    assert calls[0][-1] == "https://accounts.google.com"


def test_start_login_with_invalid_url_raises_not_configured(tmp_path):
    worker = InlineWorker(FakeSession([]))
    calls = []
    with pytest.raises(NotConfigured, match="請先完成設定：預約表網址必須是 Google 試算表網址"):
        start_login(worker, Settings(spreadsheet_url="https://example.com/x"), tmp_path / "profile",
                    popen=calls.append)
    assert calls == [] and worker.closed == 0


def test_unreadable_settings_are_reported_not_raised(tmp_path):
    store, worker, kwargs = make(tmp_path)
    (tmp_path / "data" / "settings.json").write_text("{not json", encoding="utf-8")
    report = run_connection_test(store, worker, tmp_path / "snap", **kwargs)
    assert not report.ok and "settings.json" in report.message
    assert (report.clock_source, report.clock_diff) == ("未校時", 0.0)
    assert worker.jobs == []
