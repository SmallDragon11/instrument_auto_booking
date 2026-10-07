import threading
from datetime import date, datetime

from instrument_booking.app.history_page import HistoryPage
from instrument_booking.app.tasks import BackgroundTasks
from instrument_booking.core.models import TAIPEI, BookingRequest, Instrument, ItemResult, ItemStatus
from instrument_booking.storage.json_store import JsonStore, RunRecord

MON = date(2026, 10, 12)


def run(started, *, error=None, results=()):
    req = BookingRequest("a", Instrument.TUBE_A, MON, 9, 12, "Ar", "A4C2F4")
    return RunRecord(target_monday=MON, started_at=started, late=False, clock_source="NTP", clock_diff=0.6,
                     error=error, requests=(req,), results=results)


def make(qtbot, store):
    page = HistoryPage(store, BackgroundTasks())
    qtbot.addWidget(page)
    return page


def test_shows_newest_run_first_with_results_and_errors(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    store.append_run(run(datetime(2026, 10, 2, 13, 0, tzinfo=TAIPEI), error="下載快照失敗"))
    store.append_run(run(datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI),
                         results=(ItemResult("a", ItemStatus.SUCCESS),)))
    page = make(qtbot, store)
    page.reload()
    qtbot.waitUntil(lambda: len(page.cards()) == 2)
    newest, oldest = sorted(page.cards(), key=lambda c: page._cards.indexOf(c))
    assert "10/9（五）" in newest.meta_label.text()
    assert newest.rows[0].status == "✅ 成功"
    assert oldest.error_label.text() == "未完成：下載快照失敗"
    assert not page.empty_label.isVisibleTo(page)


def test_empty_history_and_corrupt_warning(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "20261009-130000-000000.json").write_text("{壞掉", encoding="utf-8")
    page = make(qtbot, store)
    page.reload()
    qtbot.waitUntil(lambda: not page._loading)
    assert page.cards() == []
    assert page.empty_label.isVisibleTo(page)
    assert "1 個執行紀錄檔損毀" in page.notice_label.text()


def test_reload_replaces_cards(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    store.append_run(run(datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)))
    page = make(qtbot, store)
    page.reload()
    qtbot.waitUntil(lambda: len(page.cards()) == 1)
    store.append_run(run(datetime(2026, 10, 16, 13, 0, tzinfo=TAIPEI)))
    page.reload()
    qtbot.waitUntil(lambda: not page._loading)
    qtbot.waitUntil(lambda: len(page.cards()) == 2)


def test_reload_during_load_is_not_dropped(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    gate = threading.Event()
    calls = []
    real_load = store.load_runs

    def slow_load():
        calls.append(1)
        if len(calls) == 1:
            gate.wait(5)  # 第一次讀取進行中
        return real_load()

    store.load_runs = slow_load
    page = make(qtbot, store)
    page.reload()
    qtbot.waitUntil(lambda: len(calls) == 1)
    store.append_run(run(datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)))  # 讀取期間新增的紀錄
    page.reload()  # 讀取進行中又要求重新整理：不可被丟掉
    gate.set()
    qtbot.waitUntil(lambda: len(calls) == 2 and not page._loading)
    qtbot.waitUntil(lambda: len(page.cards()) == 1)
