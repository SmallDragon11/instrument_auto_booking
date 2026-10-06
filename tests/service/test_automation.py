import logging
import threading
import time as time_module
from datetime import date, datetime, time

import pytest

from instrument_booking.browser.downloader import file_id_from_url
from instrument_booking.browser.session import NotLoggedIn
from instrument_booking.core.clock import Clock, ClockSource, ClockSync
from instrument_booking.core.models import TAIPEI, BookingRequest, Instrument, ItemResult, ItemStatus
from instrument_booking.core.planner import Plan
from instrument_booking.service.automation import (AutomationService, ServicePhase, ServiceStatus, summarize,
                                                   upcoming)
from instrument_booking.service.runner import Prepared, RunCancelled
from instrument_booking.service.settings import Settings
from instrument_booking.storage.json_store import JsonStore, StoreError

MON = date(2026, 10, 12)
RUN_AT = datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)
SETTINGS = Settings(name="Zoe", spreadsheet_url="https://docs.google.com/spreadsheets/d/FILEID/edit")
REQ = BookingRequest("a", Instrument.TUBE_A, MON, 13, 15, "Ar", "A4C2F4")
REQ2 = BookingRequest("b", Instrument.TUBE_B, MON, 9, 11, "Ar", "A4C2F4")
REQ3 = BookingRequest("c", Instrument.TUBE_C, MON, 9, 10, "Ar", "A4C2F4")
NEW_URL = "https://docs.google.com/spreadsheets/d/NEWID/edit"
OK = (ItemResult("a", ItemStatus.SUCCESS),)
SETTINGS_SAVED = datetime(2026, 9, 1, 9, 0, tzinfo=TAIPEI)  # 設定早已存在


def t(hh, mm, ss=0, day=9):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=TAIPEI)


class FakeRunner:
    """offset＝真實時間 − 本機時鐘（秒）；local_now 由 env 的 make() 接到測試用的本機時鐘。"""

    def __init__(self, prepare_errors=(), run_result=OK, run_error=None, offset=0.0, while_waiting=(),
                 source=ClockSource.NTP):
        self.prepare_errors = list(prepare_errors)
        self.run_result, self.run_error = run_result, run_error
        self.offset, self.source = offset, source
        self.local_now = time_module.time
        self.calls = []
        self.ran_with = []  # run 收到的 Prepared
        self.while_waiting = list(while_waiting)  # 等待開放時間期間依序發生的事（每件之後檢查一次 guard）
        self.guard_results = []

    def prepare(self, settings, requests):
        self.calls.append(("prepare", tuple(requests)))
        if self.prepare_errors and (error := self.prepare_errors.pop(0)) is not None:  # None＝這次成功
            raise error
        sync = ClockSync(self.source, self.offset)
        return Prepared(Plan((), (), ()), sync, Clock(sync, lambda: self.local_now()), t(12, 50),
                        file_id_from_url(settings.spreadsheet_url))

    def run(self, prepared, settings, run_at, *, guard=None):
        """模擬 BookingRunner.run：job 一開始檢查 guard，等待期間每件事發生後再檢查。"""
        self.calls.append(("run", run_at))
        self.ran_with.append(prepared)
        for event in [None, *self.while_waiting]:
            if event is not None:
                event()
            reason = guard() if guard is not None else None
            self.guard_results.append(reason)
            if reason:
                raise RunCancelled(reason)
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
    store.save_settings(SETTINGS, now=SETTINGS_SAVED)
    store.save_bookings(MON, [REQ])
    clock = {"now": t(12, 0)}
    notes = Notes()

    def make(runner):
        runner.local_now = lambda: clock["now"].timestamp()
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
    assert "重新登入" in notes.items[0][1] and "12:58 會再試一次" in notes.items[0][1]
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
    store.save_settings(Settings(name="Zoe", spreadsheet_url=SETTINGS.spreadsheet_url, run_time=time(14, 0)),
                        now=t(12, 55))
    drive(service, clock, [t(12, 59)])
    assert service.state.run_at == datetime(2026, 10, 9, 14, 0, tzinfo=TAIPEI)
    assert service.state.preflight_attempts == 0


def test_moving_schedule_earlier_does_not_catch_up(env):
    # 週三 10:00 把週五改成週一：本週一 13:00（目標週＝MON）已過去，但不可立刻為 MON 那週寫入
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    store.save_settings(Settings(name="Zoe", spreadsheet_url=SETTINGS.spreadsheet_url, run_weekday=0),
                        now=t(10, 0, day=7))
    drive(service, clock, [t(10, 0, 1, day=7), t(10, 5, day=7), t(13, 0, day=7)])
    assert runner.calls == [] and store.load_runs() == []
    assert service.state.run_at == datetime(2026, 10, 12, 13, 0, tzinfo=TAIPEI)  # 等下週一


def test_cycle_before_first_settings_save_is_not_caught_up(tmp_path):
    store = JsonStore(tmp_path)
    store.save_bookings(MON, [REQ])
    store.save_settings(SETTINGS, now=t(13, 20))  # 第一次存設定時，週五 13:00 已經過去
    runner = FakeRunner()
    service = AutomationService(store=store, runner=runner, notifier=Notes(), now=lambda: t(13, 30))
    service.step()
    assert runner.calls == []
    assert service.state.run_at == datetime(2026, 10, 16, 13, 0, tzinfo=TAIPEI)


def test_failed_catch_up_preflight_retries_one_minute_later(env):
    store, clock, notes, make = env
    runner = FakeRunner(prepare_errors=[NotLoggedIn("x")])
    service = make(runner)
    drive(service, clock, [t(13, 30), t(13, 30, 30), t(13, 30, 59)])
    assert [c[0] for c in runner.calls] == ["prepare", "abandon"]  # 1 分鐘內不重試
    assert service.state.retry_at == t(13, 31)
    assert "13:31 會再試一次" in notes.items[0][1]
    drive(service, clock, [t(13, 31), t(13, 31, 1)])
    assert [c[0] for c in runner.calls] == ["prepare", "abandon", "prepare", "run"]


def test_execute_failure_after_catch_up_is_marked_late(env):
    store, clock, notes, make = env
    runner = FakeRunner(run_error=RuntimeError("worker 掛了"))
    drive(make(runner), clock, [t(13, 30), t(13, 30, 1)])
    (record,) = store.load_runs()
    assert record.late is True and "RuntimeError" in record.error


# --- 紀錄寫入失敗：不可重跑、一定通知 ---

def broken_append(monkeypatch, store):
    calls = []

    def append_run(record):
        calls.append(record)
        raise OSError("磁碟已滿")
    monkeypatch.setattr(store, "append_run", append_run)
    return calls


def test_record_write_failure_after_execute_does_not_run_again(env, monkeypatch):
    store, clock, notes, make = env
    appended = broken_append(monkeypatch, store)
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 59), t(12, 59, 1), t(13, 0), t(13, 5)])
    assert runner.calls == [("prepare", (REQ,)), ("run", RUN_AT)]  # 只寫入一次
    assert len(appended) == 1
    assert service.state.finished and service.state.run_at == RUN_AT
    ((title, message),) = notes.items
    assert title == "自動預約完成" and "1 成功" in message and "紀錄寫入失敗" in message


def test_record_write_failure_after_execute_error_still_notifies(env, monkeypatch):
    store, clock, notes, make = env
    appended = broken_append(monkeypatch, store)
    runner = FakeRunner(run_error=RuntimeError("worker 掛了"))
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 59), t(12, 59, 1), t(13, 0)])
    assert [c[0] for c in runner.calls] == ["prepare", "run"] and len(appended) == 1
    assert service.state.finished
    ((title, message),) = notes.items
    assert title == "自動預約失敗" and "RuntimeError" in message and "紀錄寫入失敗" in message


def test_record_write_failure_after_give_up_does_not_repeat(env, monkeypatch):
    store, clock, notes, make = env
    appended = broken_append(monkeypatch, store)
    runner = FakeRunner(prepare_errors=[NotLoggedIn("x"), NotLoggedIn("x")])
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 58), t(12, 58, 1), t(12, 59), t(13, 0)])
    assert [c[0] for c in runner.calls] == ["prepare", "abandon", "prepare", "abandon"]
    assert len(appended) == 1 and service.state.finished
    assert notes.items[-1][0] == "自動預約未執行" and "紀錄寫入失敗" in notes.items[-1][1]
    assert len(notes.items) == 3  # 兩次預檢失敗＋一次放棄


# --- 丟棄已準備的規劃 ---

def test_changing_spreadsheet_after_preflight_discards_plan_and_preflights_again(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    store.save_settings(Settings(name="Zoe", spreadsheet_url=NEW_URL))
    drive(service, clock, [t(12, 52), t(12, 59)])
    assert [c[0] for c in runner.calls] == ["prepare", "abandon", "prepare", "run"]
    assert runner.ran_with[0].file_id == "NEWID"  # 用新試算表的規劃寫入


@pytest.mark.parametrize("change", ["run_time", "empty_list", "invalid_settings"])
def test_abandoning_a_ready_plan_closes_edge_once(env, change):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    if change == "run_time":
        store.save_settings(Settings(name="Zoe", spreadsheet_url=SETTINGS.spreadsheet_url, run_time=time(14, 0)),
                            now=t(12, 54))
    elif change == "empty_list":
        store.save_bookings(MON, [])
    else:
        store.save_settings(Settings(spreadsheet_url=SETTINGS.spreadsheet_url), now=t(12, 54))  # 名字被清空
    drive(service, clock, [t(12, 55), t(12, 56), t(12, 59)])
    assert runner.calls == [("prepare", (REQ,)), ("abandon",)]


def test_plan_discarded_by_empty_list_is_prepared_again_when_list_returns(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    store.save_bookings(MON, [])
    drive(service, clock, [t(12, 55)])
    store.save_bookings(MON, [REQ])
    drive(service, clock, [t(12, 56), t(12, 59)])
    assert [c[0] for c in runner.calls] == ["prepare", "abandon", "prepare", "run"]
    assert store.load_runs()[0].results == OK


def test_record_keeps_the_requests_that_were_planned(env):
    store, clock, notes, make = env
    runner = FakeRunner(while_waiting=[lambda: store.save_bookings(MON, [REQ, REQ2])])  # 寫入開始後才新增
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 59)])
    assert [c[0] for c in runner.calls] == ["prepare", "run"]
    (record,) = store.load_runs()
    assert record.requests == (REQ,)


def test_execute_uses_synced_clock_when_local_clock_is_slow(env):
    store, clock, notes, make = env
    runner = FakeRunner(offset=120.0)  # 本機時鐘慢 2 分鐘
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 56, 59)])  # 本機 12:56:59＝真實 12:58:59
    assert [c[0] for c in runner.calls] == ["prepare"]
    drive(service, clock, [t(12, 57)])  # 本機 T−3＝真實 T−1
    assert [c[0] for c in runner.calls] == ["prepare", "run"]
    (record,) = store.load_runs()
    assert record.started_at == t(12, 59) and record.late is False


# --- 服務持續失敗 ---

class ScriptedStore:
    """load_settings 依序拋出 script 中的例外（None＝正常，回傳尚未設定的 Settings）；用完就停止服務。"""

    def __init__(self, script, stop):
        self.script, self.stop = list(script), stop

    def load_settings(self):
        item = self.script.pop(0)
        if not self.script:
            self.stop.set()
        if item is not None:
            raise item
        return Settings()


def run_script(script):
    notes = Notes()
    stop = threading.Event()
    service = AutomationService(store=ScriptedStore(script, stop), runner=FakeRunner(), notifier=notes)
    thread = threading.Thread(target=service.run_forever, args=(stop, 0.001))
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    return notes.items


def test_repeated_service_error_is_notified_once_per_message():
    disk, broken = OSError("磁碟錯誤"), StoreError("settings.json 內容損毀")
    items = run_script([disk, disk, disk, broken, broken])
    assert items == [("自動預約服務發生錯誤", "網路或檔案錯誤：磁碟錯誤"),
                     ("自動預約服務發生錯誤", "settings.json 內容損毀")]


def test_service_error_is_notified_again_after_recovery():
    disk = OSError("磁碟錯誤")
    items = run_script([disk, disk, None, disk])
    assert [title for title, _ in items] == ["自動預約服務發生錯誤"] * 2


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


def test_give_up_during_catch_up_is_marked_late(env):
    store, clock, notes, make = env
    runner = FakeRunner(prepare_errors=[NotLoggedIn("x"), NotLoggedIn("x")])
    drive(make(runner), clock, [t(13, 30), t(13, 31), t(13, 31, 1)])
    (record,) = store.load_runs()
    assert record.late is True and record.results == ()


# --- 寫入前可取消（偷跑防護）---

def settings_with(**changes):
    values = dict(name="Zoe", spreadsheet_url=SETTINGS.spreadsheet_url)
    values.update(changes)
    return Settings(**values)


@pytest.mark.parametrize("change, reason", [
    (lambda store: store.save_settings(settings_with(run_time=time(14, 0)), now=t(12, 59, 30)),
     "自動預約時間已變更，取消本次寫入"),
    (lambda store: store.save_settings(settings_with(run_weekday=3), now=t(12, 59, 30)),
     "自動預約時間已變更，取消本次寫入"),
    (lambda store: store.save_settings(settings_with(spreadsheet_url=NEW_URL), now=t(12, 59, 30)),
     "預約表網址已變更，取消本次寫入"),
    (lambda store: store.save_settings(settings_with(name=""), now=t(12, 59, 30)),
     "設定無效，取消本次寫入"),
], ids=["run_time", "weekday", "spreadsheet", "invalid"])
def test_settings_changed_while_waiting_cancels_the_write(env, change, reason):
    store, clock, notes, make = env
    runner = FakeRunner(while_waiting=[lambda: None, lambda: change(store)])
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 59)])
    assert runner.guard_results == [None, None, reason]  # 改變之後的下一次檢查就取消
    (record,) = store.load_runs()
    assert (record.error, record.results, record.requests) == (reason, (), (REQ,))
    assert notes.items == [("自動預約已取消", f"{reason}（本週不會再自動執行）")]
    assert service.state.finished


def test_cancelled_week_is_not_run_again_at_the_new_time(env):
    store, clock, notes, make = env
    runner = FakeRunner(while_waiting=[
        lambda: store.save_settings(settings_with(run_time=time(14, 0)), now=t(12, 59, 30))])
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 59), t(13, 0), t(13, 50), t(13, 59), t(14, 0, 30)])
    assert [c[0] for c in runner.calls] == ["prepare", "run"]  # 14:00 不再為同一週預檢或寫入
    assert store.executed_weeks() == {MON}
    assert len(store.load_runs()) == 1


def test_unrelated_settings_change_while_waiting_does_not_cancel(env):
    store, clock, notes, make = env
    runner = FakeRunner(while_waiting=[lambda: store.save_settings(settings_with(theme="dark"), now=t(12, 59, 30))])
    drive(make(runner), clock, [t(12, 50), t(12, 59)])
    assert runner.guard_results == [None, None]
    assert store.load_runs()[0].results == OK and notes.items == [("自動預約完成", "1 成功")]


def test_cancel_current_while_waiting_cancels_the_write(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    runner.while_waiting = [service.cancel_current]
    drive(service, clock, [t(12, 50), t(12, 59), t(13, 0)])
    assert runner.guard_results == [None, "已手動取消"]
    (record,) = store.load_runs()
    assert record.error == "已手動取消" and record.results == ()
    assert notes.items == [("自動預約已取消", "已手動取消（本週不會再自動執行）")]
    assert [c[0] for c in runner.calls] == ["prepare", "run"]


def test_cancel_current_while_plan_is_ready_closes_edge_and_skips_the_week(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    service.cancel_current()
    drive(service, clock, [t(12, 55), t(12, 59), t(13, 0, 30)])
    assert [c[0] for c in runner.calls] == ["prepare", "abandon"]  # 不寫入
    (record,) = store.load_runs()
    assert record.error == "已手動取消" and record.results == () and record.target_monday == MON
    assert (record.snapshot_at, record.clock_source) == (t(12, 50), "NTP")  # 先記錄再關閉 Edge
    assert notes.items == [("自動預約已取消", "已手動取消（本週不會再自動執行）")]


def test_cancel_current_before_preflight_window_does_nothing(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 0)])
    service.cancel_current()  # 還沒到 T−10：沒有進行中的自動預約可以取消
    drive(service, clock, [t(12, 1), t(12, 50), t(12, 59)])
    assert [c[0] for c in runner.calls] == ["prepare", "run"]
    assert store.load_runs()[0].results == OK


def test_guard_on_browser_thread_does_not_need_the_service_lock(env):
    # 實際的 guard 在瀏覽器執行緒被呼叫，而服務執行緒此時持有 _lock 並等待寫入結束
    store, clock, notes, make = env
    results = []

    class OtherThreadRunner(FakeRunner):
        def run(self, prepared, settings, run_at, *, guard=None):
            th = threading.Thread(target=lambda: results.append(guard()))
            th.start()
            th.join(timeout=2)
            assert not th.is_alive(), "guard 等待 _lock：死結"
            return OK
    runner = OtherThreadRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 59)])
    assert results == [None]


def test_cancel_current_does_not_block_while_step_holds_the_lock(env):
    store, clock, notes, make = env
    done = threading.Event()

    class BlockingRunner(FakeRunner):
        def run(self, prepared, settings, run_at, *, guard=None):
            th = threading.Thread(target=lambda: (service.cancel_current(), done.set()))
            th.start()
            th.join(timeout=2)
            return super().run(prepared, settings, run_at, guard=guard)
    runner = BlockingRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 59)])
    assert done.is_set()
    assert store.load_runs()[0].error == "已手動取消"


# --- 預檢後清單被修改：重新預檢（保留待命的 Edge）---

def test_deleting_a_request_after_preflight_replans_without_closing_edge(env):
    store, clock, notes, make = env
    store.save_bookings(MON, [REQ, REQ2])
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50), t(12, 52)])
    store.save_bookings(MON, [REQ])
    drive(service, clock, [t(12, 55), t(12, 56), t(12, 59)])
    assert runner.calls == [("prepare", (REQ, REQ2)), ("prepare", (REQ,)), ("run", RUN_AT)]  # 沒有 abandon
    (record,) = store.load_runs()
    assert record.requests == (REQ,)


@pytest.mark.parametrize("new_list", [[REQ2, REQ], [REQ, REQ2, REQ3]], ids=["reordered", "added"])
def test_reordering_or_adding_after_preflight_replans(env, new_list):
    store, clock, notes, make = env
    store.save_bookings(MON, [REQ, REQ2])
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    store.save_bookings(MON, new_list)
    drive(service, clock, [t(12, 55), t(12, 59)])
    assert runner.calls == [("prepare", (REQ, REQ2)), ("prepare", tuple(new_list)), ("run", RUN_AT)]
    assert store.load_runs()[0].requests == tuple(new_list)


def test_replanning_does_not_use_up_the_retry(env):
    store, clock, notes, make = env
    runner = FakeRunner(prepare_errors=[None, NotLoggedIn("x")])  # 第一次成功、重新預檢失敗
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    store.save_bookings(MON, [REQ, REQ2])
    drive(service, clock, [t(12, 55), t(12, 56), t(12, 58), t(12, 59)])
    assert [c[0] for c in runner.calls] == ["prepare", "prepare", "abandon", "prepare", "run"]
    assert "12:58 會再試一次" in notes.items[0][1]  # 仍有一次重試，不是直接放棄
    assert store.load_runs()[0].results == OK


# --- 給 GUI 的狀態快照 ---

def observe_status(service, runner, method):
    """在 runner 的 prepare 或 run 進行中讀取 status()。"""
    seen = []
    real = getattr(runner, method)

    def wrapper(*args, **kwargs):
        seen.append(service.status())
        return real(*args, **kwargs)
    setattr(runner, method, wrapper)
    return seen


def test_status_through_a_full_cycle(env):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    assert service.status() == ServiceStatus(ServicePhase.IDLE)  # 尚未 step
    preparing = observe_status(service, runner, "prepare")
    running = observe_status(service, runner, "run")

    drive(service, clock, [t(12, 0)])
    assert service.status() == ServiceStatus(ServicePhase.IDLE, run_at=RUN_AT, target_monday=MON)
    drive(service, clock, [t(12, 50)])
    assert preparing[0].phase is ServicePhase.PREPARING and preparing[0].editing_locked
    assert service.status() == ServiceStatus(ServicePhase.READY, run_at=RUN_AT, target_monday=MON,
                                             editing_locked=True)
    drive(service, clock, [t(12, 59)])
    assert running[0] == ServiceStatus(ServicePhase.RUNNING, run_at=RUN_AT, target_monday=MON, editing_locked=True)
    assert service.status() == ServiceStatus(ServicePhase.DONE, run_at=RUN_AT, target_monday=MON)
    drive(service, clock, [t(13, 0, 30)])
    next_run = datetime(2026, 10, 16, 13, 0, tzinfo=TAIPEI)
    assert service.status() == ServiceStatus(ServicePhase.IDLE, run_at=next_run, target_monday=date(2026, 10, 19))


def test_status_while_waiting_for_retry_and_after_giving_up(env):
    store, clock, notes, make = env
    runner = FakeRunner(prepare_errors=[NotLoggedIn("x"), NotLoggedIn("x")])
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    status = service.status()
    assert (status.phase, status.retry_at, status.editing_locked) == (ServicePhase.RETRY_WAIT, t(12, 58), True)
    assert "重新登入" in status.last_error
    drive(service, clock, [t(12, 58), t(12, 58, 1)])
    status = service.status()
    assert (status.phase, status.editing_locked, status.retry_at) == (ServicePhase.DONE, False, None)
    assert "重新登入" in status.last_error


def test_status_when_not_configured_or_without_requests(env):
    store, clock, notes, make = env
    service = make(FakeRunner())
    store.save_bookings(MON, [])
    drive(service, clock, [t(12, 0)])
    assert service.status() == ServiceStatus(ServicePhase.IDLE, run_at=RUN_AT, target_monday=MON)
    drive(service, clock, [t(12, 51)])  # T−10 之後：即使沒有清單也鎖定編輯
    assert service.status() == ServiceStatus(ServicePhase.IDLE, run_at=RUN_AT, target_monday=MON,
                                             editing_locked=True)
    store.save_settings(Settings(), now=t(12, 52))
    drive(service, clock, [t(12, 52)])
    assert service.status() == ServiceStatus(ServicePhase.NOT_CONFIGURED)


def test_status_returns_immediately_while_writing(env):
    store, clock, notes, make = env
    entered, release = threading.Event(), threading.Event()

    class BlockingRunner(FakeRunner):
        def run(self, prepared, settings, run_at, *, guard=None):
            entered.set()
            release.wait(5)
            return OK
    service = make(BlockingRunner())
    drive(service, clock, [t(12, 50)])
    clock["now"] = t(12, 59)
    stepper = threading.Thread(target=service.step)
    stepper.start()
    try:
        assert entered.wait(5)
        got = []
        reader = threading.Thread(target=lambda: got.append(service.status()))
        reader.start()
        reader.join(timeout=1)
        assert not reader.is_alive(), "status() 被寫入中的 step 阻塞"
        assert got[0].phase is ServicePhase.RUNNING and got[0].editing_locked
    finally:
        release.set()
        stepper.join(timeout=5)
    assert service.status().phase is ServicePhase.DONE


def test_status_reports_service_error_and_recovery():
    stop = threading.Event()
    store = ScriptedStore([OSError("磁碟錯誤")], stop)
    service = AutomationService(store=store, runner=FakeRunner(), notifier=Notes())
    thread = threading.Thread(target=service.run_forever, args=(stop, 0.001))
    thread.start()
    thread.join(timeout=5)
    status = service.status()
    assert (status.phase, status.service_error) == (ServicePhase.ERROR, "網路或檔案錯誤：磁碟錯誤")
    store.script, stop = [None], threading.Event()
    store.stop = stop
    thread = threading.Thread(target=service.run_forever, args=(stop, 0.001))
    thread.start()
    thread.join(timeout=5)
    assert service.status() == ServiceStatus(ServicePhase.NOT_CONFIGURED)


def test_upcoming_matches_the_service(env):
    store, clock, notes, make = env
    assert upcoming(t(12, 0), SETTINGS, store) == (RUN_AT, MON)
    assert upcoming(t(13, 30), SETTINGS, store) == (RUN_AT, MON)  # 錯過且有清單：補跑
    runner = FakeRunner()
    drive(make(runner), clock, [t(12, 50), t(12, 59)])
    assert upcoming(t(13, 30), SETTINGS, store) == (datetime(2026, 10, 16, 13, 0, tzinfo=TAIPEI), date(2026, 10, 19))


# --- 紀錄：開始時間、延遲判斷、快照時間、校時來源 ---

def test_record_started_at_is_when_the_write_job_starts(env):
    store, clock, notes, make = env

    class QueuedRunner(FakeRunner):
        def run(self, prepared, settings, run_at, *, guard=None):
            clock["now"] = t(12, 59, 20)  # 瀏覽器執行緒先做完排在前面的工作，job 才開始
            return super().run(prepared, settings, run_at, guard=guard)
    drive(make(QueuedRunner()), clock, [t(12, 50), t(12, 59)])
    (record,) = store.load_runs()
    assert record.started_at == t(12, 59, 20)


def test_late_is_judged_by_the_first_actual_write(env):
    store, clock, notes, make = env
    slow = (ItemResult("a", ItemStatus.SUCCESS, written_at=t(13, 2)),)  # 例如瀏覽器當掉復原後才寫入
    drive(make(FakeRunner(run_result=slow)), clock, [t(12, 50), t(12, 59)])
    (record,) = store.load_runs()
    assert record.late is True and record.started_at == t(12, 59)
    assert notes.items == [("自動預約完成", "（延遲執行）1 成功")]


def test_on_time_first_write_is_not_late_even_if_later_writes_are(env):
    store, clock, notes, make = env
    results = (ItemResult("a", ItemStatus.SUCCESS, written_at=t(13, 0, 1)),
               ItemResult("b", ItemStatus.SUCCESS, written_at=t(13, 3)))
    store.save_bookings(MON, [REQ, REQ2])
    drive(make(FakeRunner(run_result=results)), clock, [t(12, 50), t(12, 59)])
    assert store.load_runs()[0].late is False


def test_late_without_writes_is_judged_by_when_the_run_ended(env):
    store, clock, notes, make = env
    conflict = (ItemResult("a", ItemStatus.LIVE_CONFLICT, reason="B10 已有「Ping」"),)
    runner = FakeRunner(run_result=conflict, while_waiting=[lambda: clock.__setitem__("now", t(13, 1, 30))])
    drive(make(runner), clock, [t(12, 50), t(12, 59)])
    assert store.load_runs()[0].late is True


def test_record_keeps_snapshot_time(env):
    store, clock, notes, make = env
    drive(make(FakeRunner()), clock, [t(12, 50), t(12, 59)])
    assert store.load_runs()[0].snapshot_at == t(12, 50)  # Prepared.prepared_at


def test_preflight_logs_clock_source_and_difference(env, caplog):
    store, clock, notes, make = env
    caplog.set_level(logging.INFO, logger="instrument_booking")
    drive(make(FakeRunner()), clock, [t(12, 50)])
    assert any("校時來源 NTP" in r.getMessage() and "時鐘差" in r.getMessage() for r in caplog.records)


def test_completion_notice_warns_when_clock_was_not_synced(env):
    store, clock, notes, make = env
    drive(make(FakeRunner(source=ClockSource.LOCAL)), clock, [t(12, 50), t(12, 59)])
    assert notes.items == [("自動預約完成", "1 成功（未能網路校時，使用本機時間＋3 秒餘量）")]
    assert store.load_runs()[0].clock_source == "本機時間"


# --- 服務錯誤時不可讓待命的 Edge 無限期留著 ---

def test_service_error_after_preflight_discards_plan_and_closes_edge(env, monkeypatch):
    store, clock, notes, make = env
    runner = FakeRunner()
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    real_load = store.load_bookings
    stop = threading.Event()

    def broken(monday):
        stop.set()
        raise OSError("磁碟錯誤")
    monkeypatch.setattr(store, "load_bookings", broken)
    thread = threading.Thread(target=service.run_forever, args=(stop, 0.001))
    thread.start()
    thread.join(timeout=5)
    assert runner.calls == [("prepare", (REQ,)), ("abandon",)]
    assert service.status().phase is ServicePhase.ERROR
    monkeypatch.setattr(store, "load_bookings", real_load)
    drive(service, clock, [t(12, 51), t(12, 59)])  # 恢復後立刻重新預檢（不算失敗重試）
    assert [c[0] for c in runner.calls] == ["prepare", "abandon", "prepare", "run"]


def test_service_error_while_closing_edge_is_still_reported(env, monkeypatch):
    store, clock, notes, make = env

    class BrokenAbandon(FakeRunner):
        def abandon(self):
            super().abandon()
            raise RuntimeError("Edge 關不掉")
    runner = BrokenAbandon()
    service = make(runner)
    drive(service, clock, [t(12, 50)])
    stop = threading.Event()

    def broken(monday):
        stop.set()
        raise OSError("磁碟錯誤")
    monkeypatch.setattr(store, "load_bookings", broken)
    thread = threading.Thread(target=service.run_forever, args=(stop, 0.001))
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert notes.items == [("自動預約服務發生錯誤", "網路或檔案錯誤：磁碟錯誤")]
    assert runner.calls[-1] == ("abandon",)
