import threading
from datetime import time

from PySide6.QtCore import QTime

from instrument_booking.app.settings_page import SettingsPage
from instrument_booking.app.tasks import BackgroundTasks
from instrument_booking.service.automation import ServicePhase, ServiceStatus
from instrument_booking.service.connection import ConnectionReport
from instrument_booking.service.settings import Settings
from instrument_booking.storage.json_store import JsonStore

URL = "https://docs.google.com/spreadsheets/d/FILEID/edit"


def make(qtbot, store, *, report=None, login=None, confirm=lambda *a: True):
    saved = []
    page = SettingsPage(store, BackgroundTasks(),
                        connection_test=lambda: report or ConnectionReport(True, "連線正常", "NTP", 0.6),
                        start_login=login or (lambda: None), on_saved=saved.append, confirm=confirm)
    qtbot.addWidget(page)
    return page, saved


def test_loads_existing_settings(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    s = Settings(name="Zoe", spreadsheet_url=URL, run_weekday=2, run_time=time(9, 30), autostart=False, theme="dark")
    store.save_settings(s)
    page, _ = make(qtbot, store)
    assert page.current() == s


def test_save_strips_and_persists_then_notifies(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    page, saved = make(qtbot, store)
    page.name_edit.setText(" Zoe ")
    page.url_edit.setText(URL + " ")
    page.weekday_combo.setCurrentIndex(0)
    page.time_picker.setTime(QTime(12, 45))
    assert page.save()
    expected = Settings(name="Zoe", spreadsheet_url=URL, run_weekday=0, run_time=time(12, 45))
    assert store.load_settings() == expected and saved == [expected]
    assert store.schedule_since() is not None  # GUI 不傳 now，由 store 記錄


def test_invalid_settings_are_not_saved(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    page, saved = make(qtbot, store)
    page.name_edit.setText("")
    page.url_edit.setText("https://example.com")
    assert not page.save()
    assert saved == [] and not (tmp_path / "settings.json").exists()


def test_changing_schedule_while_locked_needs_confirmation(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    store.save_settings(Settings(name="Zoe", spreadsheet_url=URL))
    page, saved = make(qtbot, store, confirm=lambda *a: False)
    page.apply_status(ServiceStatus(ServicePhase.READY, editing_locked=True))
    page.tray_switch.setChecked(False)
    assert page.save()  # 只改系統匣：不用確認
    page.time_picker.setTime(QTime(14, 0))
    assert not page.save()  # 改時間：使用者按了取消
    assert store.load_settings().run_time == time(13, 0)


def test_lock_disables_login_and_connection_test(qtbot, tmp_path):
    calls = []
    page, _ = make(qtbot, JsonStore(tmp_path), login=lambda: calls.append("login"))
    page.apply_status(ServiceStatus(ServicePhase.PREPARING, editing_locked=True))
    assert not page.login_button.isEnabled() and not page.test_button.isEnabled()
    page.start_login()
    page.run_connection_test()
    qtbot.wait(50)
    assert calls == []
    page.apply_status(ServiceStatus(ServicePhase.DONE))
    assert page.login_button.isEnabled()


def test_connection_test_runs_in_background_and_shows_result(qtbot, tmp_path):
    gui = threading.get_ident()
    threads = []

    def connection_test():
        threads.append(threading.get_ident())
        return ConnectionReport(False, "下載快照失敗：HTTP 403", "NTP", -0.25)
    page, _ = make(qtbot, JsonStore(tmp_path))
    page._connection_test = connection_test
    page.run_connection_test()
    assert not page.test_button.isEnabled()
    qtbot.waitUntil(lambda: "❌" in page.account_label.text())
    assert threads and threads[0] != gui
    assert "下載快照失敗：HTTP 403（校時：NTP（本機時鐘快 0.25 秒））" in page.account_label.text()
    assert page.test_button.isEnabled()


def test_unconfigured_connection_test_shows_message_without_clock(qtbot, tmp_path):
    page, _ = make(qtbot, JsonStore(tmp_path), report=ConnectionReport(False, "請填寫要寫入表格的名字", "未校時", 0.0))
    page.run_connection_test()
    qtbot.waitUntil(lambda: "❌" in page.account_label.text())
    assert page.account_label.text().endswith("❌ 請填寫要寫入表格的名字")


def test_login_runs_in_background_and_failure_reenables_button(qtbot, tmp_path):
    def login():
        raise RuntimeError("找不到 Edge")
    page, _ = make(qtbot, JsonStore(tmp_path), login=login)
    page.start_login()
    assert not page.login_button.isEnabled()
    qtbot.waitUntil(lambda: page.login_button.isEnabled())
