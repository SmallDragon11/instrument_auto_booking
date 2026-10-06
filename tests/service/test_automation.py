import threading
import time as time_module
from datetime import date, datetime, time

import pytest

from instrument_booking.browser.session import NotLoggedIn
from instrument_booking.core.clock import Clock, ClockSource, ClockSync
from instrument_booking.core.models import TAIPEI, BookingRequest, Instrument, ItemResult, ItemStatus
from instrument_booking.core.planner import Plan
from instrument_booking.service.automation import AutomationService, summarize
from instrument_booking.service.runner import Prepared
from instrument_booking.service.settings import Settings
from instrument_booking.storage.json_store import JsonStore

MON = date(2026, 10, 12)
RUN_AT = datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)
SETTINGS = Settings(name="Zoe", spreadsheet_url="https://docs.google.com/spreadsheets/d/FILEID/edit")
REQ = BookingRequest("a", Instrument.TUBE_A, MON, 13, 15, "Ar", "A4C2F4")
OK = (ItemResult("a", ItemStatus.SUCCESS),)


def t(hh, mm, ss=0, day=9):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=TAIPEI)


class FakeRunner:
    def __init__(self, prepare_errors=(), run_result=OK, run_error=None):
        self.prepare_errors = list(prepare_errors)
        self.run_result, self.run_error = run_result, run_error
        self.calls = []

    def prepare(self, settings, requests):
        self.calls.append(("prepare", tuple(requests)))
        if self.prepare_errors:
            raise self.prepare_errors.pop(0)
        return Prepared(Plan((), (), ()), ClockSync(ClockSource.NTP, 0.0), Clock(ClockSync(ClockSource.NTP, 0.0)), t(12, 50))

    def run(self, prepared, settings, run_at):
        self.calls.append(("run", run_at))
        if self.run_error:
            raise self.run_error
        return self.run_result

    def abandon(self):
        self.calls.append(("abandon",))


class Notes:
    def __init__(self):
        self.items = []

    def notify(self, title, message):
        self.items.append((title, message))


@pytest.fixture
def env(tmp_path):
    store = JsonStore(tmp_path)
    store.save_settings(SETTINGS)
    store.save_bookings(MON, [REQ])
    clock = {"now": t(12, 0)}
    notes = Notes()

    def make(runner):
        return AutomationService(store=store, runner=runner, notifier=notes, now=lambda: clock["now"])
    return store, clock, notes, make


def drive(service, clock, times):
    for now in times:
        clock["now"] = now
        service.step()


def test_full_cycle_preflight_then_execute_and_record(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 0), t(12, 49, 59), t(12, 50), t(12, 55), t(12, 59), t(13, 0, 30)])
    assert runner.calls == [("prepare", (REQ,)), ("run", RUN_AT)]
    (record,) = store.load_runs()
    assert (record.target_monday, record.late, record.clock_source, record.results) == (MON, False, "NTP", OK)
    assert record.started_at == t(12, 59)
    assert notes.items == [("自動預約完成", "1 成功")]
    assert store.executed_weeks() == {MON}
    assert service.state.run_at == datetime(2026, 10, 16, 13, 0, tzinfo=TAIPEI)  # 已換到下一個週期


def test_nothing_happens_without_requests_or_settings(env, tmp_path):
    store, clock, notes, make = env
    store.save_bookings(MON, [])
    runner = FakeRunner()
    drive(make(runner), clock, [t(12, 50), t(12, 59)])
    store.save_bookings(MON, [REQ])
    store.save_settings(Settings())  # 尚未設定名字與網址
    drive(make(runner), clock, [t(12, 50), t(12, 59)])
    assert runner.calls == [] and store.load_runs() == []


def test_failed_preflight_retries_at_t_minus_2_then_succeeds(env):
    store, clock, notes, make = env
    runner = FakeRunner(prepare_errors=[NotLoggedIn("x")])
    drive(make(runner), clock, [t(12, 50), t(12, 55), t(12, 58), t(12, 59)])
    assert [c[0] for c in runner.calls] == ["prepare", "abandon", "prepare", "run"]
    assert notes.items[0][0] == "自動預約預檢失敗"
    assert "重新登入" in notes.items[0][1] and "2 分鐘前會再試一次" in notes.items[0][1]
    assert store.load_runs()[0].results == OK


def test_two_failed_preflights_give_up_and_record_error(env):
    store, clock, notes, make = env
    runner = FakeRunner(prepare_errors=[NotLoggedIn("x"), NotLoggedIn("x")])
    drive(make(runner), clock, [t(12, 50), t(12, 58), t(12, 58, 1), t(13, 0)])
    assert [c[0] for c in runner.calls] == ["prepare", "abandon", "prepare", "abandon"]
    (record,) = store.load_runs()
    assert "重新登入" in record.error and record.results == ()
    assert notes.items[-1][0] == "自動預約未執行"


def test_execute_failure_is_recorded(env):
    store, clock, notes, make = env
    runner = FakeRunner(run_error=RuntimeError("worker 掛了"))
    drive(make(runner), clock, [t(12, 50), t(12, 59)])
    (record,) = store.load_runs()
    assert "RuntimeError" in record.error
    assert notes.items[-1][0] == "自動預約失敗"


def test_missed_run_is_caught_up_and_marked_late(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    drive(make(runner), clock, [t(13, 30), t(13, 30, 1)])  # 電腦睡到 13:30 才醒
    (record,) = store.load_runs()
    assert record.late is True
    assert notes.items[-1] == ("自動預約完成", "（延遲執行）1 成功")


def test_changing_run_time_resets_cycle(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    store.save_settings(Settings(name="Zoe", spreadsheet_url=SETTINGS.spreadsheet_url, run_time=time(14, 0)))
    drive(service, clock, [t(12, 59)])
    assert service.state.run_at == datetime(2026, 10, 9, 14, 0, tzinfo=TAIPEI)
    assert service.state.preflight_attempts == 0


def test_run_forever_survives_errors_and_stops():
    class BrokenStore:
        calls = 0

        def load_settings(self):
            BrokenStore.calls += 1
            raise RuntimeError("磁碟錯誤")
    service = AutomationService(store=BrokenStore(), runner=FakeRunner(), notifier=Notes())
    stop = threading.Event()
    thread = threading.Thread(target=service.run_forever, args=(stop, 0.01))
    thread.start()
    deadline = time_module.monotonic() + 2
    while BrokenStore.calls < 3 and time_module.monotonic() < deadline:
        time_module.sleep(0.01)
    stop.set()
    thread.join(timeout=2)
    assert BrokenStore.calls >= 3  # 例外不會讓服務停止
    assert not thread.is_alive()


def test_summarize():
    results = [ItemResult("a", ItemStatus.SUCCESS), ItemResult("b", ItemStatus.SUCCESS),
               ItemResult("c", ItemStatus.LIVE_CONFLICT), ItemResult("d", ItemStatus.FAILED)]
    assert summarize(results) == "2 成功、1 即時衝突、1 失敗"
    assert summarize([]) == "沒有預約"
