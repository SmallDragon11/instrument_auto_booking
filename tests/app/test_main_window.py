import logging
import threading
from datetime import date, datetime, time

import pytest

from instrument_booking.app.main_window import MainWindow, banner_text
from instrument_booking.app.notifier import QtNotifier
from instrument_booking.core.models import TAIPEI
from instrument_booking.service.automation import ServicePhase, ServiceStatus
from instrument_booking.service.container import build_services
from instrument_booking.service.housekeeping import LOGGER_NAME
from instrument_booking.service.settings import Settings
from instrument_booking.storage.json_store import RunRecord
from service.fakes import FakeSession

URL = "https://docs.google.com/spreadsheets/d/FILEID/edit"
NOW = datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI)  # 週三
RUN = datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)
MON = date(2026, 10, 12)


class FakeAutomation:
    def __init__(self):
        self.current = ServiceStatus(ServicePhase.IDLE, run_at=RUN, target_monday=MON)
        self.cancelled = 0

    def status(self):
        return self.current

    def cancel_current(self):
        self.cancelled += 1


class FakeRegistry:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        del self.values[name]


@pytest.fixture(autouse=True)
def clean_logger():
    yield
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


@pytest.fixture
def env(qtbot, tmp_path):
    notifier = QtNotifier()
    services = build_services(notifier, data_dir=tmp_path / "data", profile_dir=tmp_path / "profile",
                              session_factory=lambda url, profile: FakeSession([]))
    services.automation = FakeAutomation()
    services.store.save_settings(Settings(name="Zoe", spreadsheet_url=URL))
    state = {"answer": True, "asked": [], "quit": 0}
    registry = FakeRegistry()

    def confirm(parent, title, content):
        state["asked"].append(title)
        return state["answer"]

    def make():
        window = MainWindow(services, notifier, stop=threading.Event(), autostart_command='"x.exe" --background',
                            autostart_registry=registry, now=lambda: NOW, confirm=confirm,
                            quit_app=lambda: state.__setitem__("quit", state["quit"] + 1))
        qtbot.addWidget(window)
        return window
    yield services, notifier, state, registry, make
    services.shutdown()


def test_tick_shows_service_target_week_and_status(env):
    services, _, _, _, make = env
    window = make()
    assert window.week_page.monday == MON
    assert window.week_page.status_label.text() == "下次自動預約：10/9（五）13:00（還有 2 天 3 小時）"
    assert not window.banner.isVisibleTo(window)


def test_banner_for_unconfigured_and_service_error(env):
    services, _, _, _, make = env
    window = make()
    services.automation.current = ServiceStatus(ServicePhase.NOT_CONFIGURED)
    window.tick()
    assert window.banner.isVisibleTo(window) and "尚未完成設定" in window.banner.label.text()
    assert window.week_page.monday == MON  # 服務尚未排程：依設定推算
    services.automation.current = ServiceStatus(ServicePhase.ERROR, run_at=RUN, target_monday=MON,
                                                service_error="磁碟已滿")
    window.tick()
    assert window.banner.label.text() == "自動預約服務發生錯誤：磁碟已滿"
    services.automation.current = ServiceStatus(ServicePhase.IDLE, run_at=RUN, target_monday=MON)
    window.tick()
    assert not window.banner.isVisibleTo(window)


def test_banner_text_for_corrupt_settings():
    assert "設定檔無法讀取" in banner_text(ServiceStatus(ServicePhase.IDLE), "settings.json 內容損毀")


def test_ready_notifies_once_with_clipboard_hint(env, monkeypatch):
    services, _, _, _, make = env
    window = make()
    shown = []
    monkeypatch.setattr(window, "notify", lambda title, message: shown.append((title, message)))
    services.automation.current = ServiceStatus(ServicePhase.READY, run_at=RUN, target_monday=MON,
                                                editing_locked=True)
    window.tick()
    window.tick()
    assert len(shown) == 1 and "剪貼簿" in shown[0][1] and "13:00" in shown[0][1]


def test_service_notification_reloads_history(env, qtbot):
    services, notifier, _, _, make = env
    window = make()
    services.store.append_run(RunRecord(target_monday=MON, started_at=RUN, late=False, clock_source="NTP",
                                        clock_diff=0.1, error=None, requests=(), results=()))
    threading.Thread(target=notifier.notify, args=("自動預約完成", "沒有預約")).start()
    qtbot.waitUntil(lambda: len(window.history_page.cards()) == 1)


def test_close_hides_to_tray_by_default(env):
    _, _, state, _, make = env
    window = make()
    window.show()
    window.close()
    assert not window.isVisible() and state["quit"] == 0 and state["asked"] == []


def test_close_without_tray_quits_after_shutdown(env, qtbot):
    services, _, state, _, make = env
    services.store.save_settings(Settings(name="Zoe", spreadsheet_url=URL, minimize_to_tray=False))
    window = make()
    window.show()
    window.close()
    assert state["asked"] == ["結束實驗規劃助手"]
    assert window._stop.is_set()
    qtbot.waitUntil(lambda: state["quit"] == 1)
    assert services.automation.cancelled == 0


def test_quit_while_locked_warns_and_cancels(env, qtbot):
    services, _, state, _, make = env
    window = make()
    services.automation.current = ServiceStatus(ServicePhase.READY, run_at=RUN, target_monday=MON,
                                                editing_locked=True)
    state["answer"] = False
    window.request_quit()
    assert state["asked"] == ["自動預約進行中"] and not window._stop.is_set()
    state["answer"] = True
    window.request_quit()
    assert services.automation.cancelled == 1
    qtbot.waitUntil(lambda: state["quit"] == 1)


def test_autostart_follows_settings(env):
    services, _, _, registry, make = env
    window = make()
    assert registry.values == {"InstrumentBooking": '"x.exe" --background'}  # 預設開
    window._on_settings_saved(Settings(name="Zoe", spreadsheet_url=URL, autostart=False))
    assert registry.values == {}


def test_settings_change_moves_displayed_week_when_service_not_scheduled(env):
    services, _, _, _, make = env
    window = make()
    services.automation.current = ServiceStatus(ServicePhase.IDLE)  # 延後中：尚未依新設定排程
    window._on_settings_saved(Settings(name="Zoe", spreadsheet_url=URL, run_weekday=0, run_time=time(9, 0)))
    assert window.week_page.monday == date(2026, 10, 19)


def test_stop_is_set_before_services_shutdown(env, qtbot, monkeypatch):
    services, _, state, _, make = env
    window = make()
    recorded = []
    original_shutdown = services.shutdown

    def recorded_shutdown():
        recorded.append(("shutdown", window._stop.is_set()))
        original_shutdown()

    monkeypatch.setattr(services, "shutdown", recorded_shutdown)
    window.request_quit()
    qtbot.waitUntil(lambda: state["quit"] == 1)
    assert recorded == [("shutdown", True)]


def test_lock_starting_during_quit_dialog_still_cancels(env, qtbot):
    services, _, state, _, make = env
    window = make()

    def custom_confirm(parent, title, content):
        services.automation.current = ServiceStatus(ServicePhase.READY, run_at=RUN, target_monday=MON,
                                                    editing_locked=True)
        return True

    window._confirm = custom_confirm
    window.request_quit()
    qtbot.waitUntil(lambda: state["quit"] == 1)
    assert services.automation.cancelled == 1


def test_quit_dialog_is_not_reentrant(env):
    services, _, state, _, make = env
    window = make()
    call_count = [0]

    def reentrant_confirm(parent, title, content):
        call_count[0] += 1
        if call_count[0] == 1:
            window.request_quit()
        return False

    window._confirm = reentrant_confirm
    window.request_quit()
    assert call_count[0] == 1 and not window._stop.is_set()


def test_tick_survives_status_errors(env):
    services, _, _, _, make = env
    window = make()
    call_count = [0]
    original_status = services.automation.status

    def raising_status():
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("status check failed")
        return original_status()

    services.automation.status = raising_status
    window.tick()  # first call raises, should not propagate
    window.tick()  # second call succeeds, banner should update
    assert "下次自動預約" in window.week_page.status_label.text()
