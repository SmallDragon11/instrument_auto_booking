from datetime import date
from pathlib import Path

import pytest

from instrument_booking.core.clock import ClockSource, ClockSync
from instrument_booking.service.connection import run_connection_test, start_login
from instrument_booking.service.settings import Settings
from instrument_booking.storage.json_store import JsonStore
from service.fakes import FakeSession, InlineWorker, XlsxRequest
from sheet_builder import add_tube_sheet, new_workbook

URL = "https://docs.google.com/spreadsheets/d/FILEID/edit"
NOW_EPOCH = 1_791_522_000.0 - 3 * 86400  # 2026-10-06（二）13:00 台北


def make(tmp_path, *, settings=Settings(name="Zoe", spreadsheet_url=URL), request="xlsx"):
    store = JsonStore(tmp_path / "data")
    store.save_settings(settings)
    wb = new_workbook()
    add_tube_sheet(wb, "202610", date(2026, 10, 12), legend=[("Ar", "A4C2F4")])
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
    report = run_connection_test(store, worker, tmp_path / "snap", **kwargs)
    assert not report.ok and "名字" in report.message
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
