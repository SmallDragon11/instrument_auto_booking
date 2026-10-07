import threading
from datetime import date, datetime, timedelta

import pytest

from instrument_booking.app.tasks import BackgroundTasks
from instrument_booking.app.week_page import ID_ROLE, WeekPage
from instrument_booking.core.models import TAIPEI, BookingRequest, Instrument
from instrument_booking.service.automation import ServicePhase, ServiceStatus
from instrument_booking.service.occupancy import WeekView
from instrument_booking.storage.json_store import JsonStore

MON = date(2026, 10, 12)
RUN = datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)
LEGEND = {"Ar": "A4C2F4", "Ar/H2": "DD7E6B"}


class FakeOccupancy:
    def __init__(self, view=None, error=None):
        self.view, self.error = view, error
        self.calls = []

    def __call__(self, monday):
        self.calls.append(monday)
        if self.error:
            raise self.error
        if self.view is not None and self.view.monday == monday:
            return self.view
        return WeekView(monday, frozenset(), frozenset(), LEGEND)


@pytest.fixture
def store(tmp_path):
    s = JsonStore(tmp_path)
    s.save_legend(LEGEND)
    return s


def make_page(qtbot, store, occupancy=None, cancels=None, confirm=lambda *a: True):
    tasks = BackgroundTasks()
    occupancy = occupancy or FakeOccupancy()
    cancels = cancels if cancels is not None else []
    page = WeekPage(store, tasks, refresh_occupancy=occupancy, cancel_run=lambda: cancels.append(1),
                    confirm=confirm)
    qtbot.addWidget(page)
    page.resize(1200, 760)
    return page, occupancy


def wait_refreshed(qtbot, page):
    qtbot.waitUntil(lambda: page._refreshing_for is None)


def listed(page):
    return [page.priority_list.item(i).text() for i in range(page.priority_list.count())]


def test_set_week_loads_saved_list_and_refreshes_occupancy(qtbot, store):
    saved = BookingRequest("a", Instrument.TUBE_B, MON, 9, 12, "Ar", "A4C2F4")
    store.save_bookings(MON, [saved])
    page, occupancy = make_page(qtbot, store)
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    assert page.week_label.text() == "目標週：10/12（一）– 10/18（日）"
    assert listed(page) == ["1. 10/12（一）B-窗 09:00–12:00 Ar"]
    assert occupancy.calls == [MON]
    assert page.occupancy_label.text().startswith("佔用資料更新於")


def test_drag_on_tube_tab_adds_request_with_selected_gas(qtbot, store):
    page, _ = make_page(qtbot, store)
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    page.gas_combo.setCurrentText("Ar/H2")
    page.calendar.rangeSelected.emit(1, 9, 12)
    (r,) = store.load_bookings(MON)
    assert (r.instrument, r.date, r.start_hour, r.end_hour, r.gas, r.color) == \
        (Instrument.TUBE_A, date(2026, 10, 13), 9, 12, "Ar/H2", "DD7E6B")
    assert page.calendar.blocks()[0].request == r


def test_oven_tab_uses_fixed_color_and_hides_gas(qtbot, store):
    page, _ = make_page(qtbot, store)
    page.show()
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    page._set_instrument(Instrument.OVEN_A)
    assert not page.gas_combo.isVisible()
    page.calendar.rangeSelected.emit(0, 23, 24)
    (r,) = store.load_bookings(MON)
    assert (r.instrument, r.gas, r.color, r.start_hour) == (Instrument.OVEN_A, None, "FFE599", 23)


def test_tube_without_legend_warns_and_adds_nothing(qtbot, tmp_path):
    store = JsonStore(tmp_path)
    page, _ = make_page(qtbot, store, occupancy=FakeOccupancy(error=OSError("離線")))
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    assert "無法更新佔用" in page.occupancy_label.text()
    page.calendar.rangeSelected.emit(0, 9, 10)
    assert store.load_bookings(MON) == []


def test_occupied_slots_shown_for_current_instrument_only(qtbot, store):
    view = WeekView(MON, frozenset({(Instrument.TUBE_A, MON, 10), (Instrument.OVEN_A, MON, 9)}),
                    frozenset({("oven", MON + timedelta(days=6))}), LEGEND)
    page, _ = make_page(qtbot, store, occupancy=FakeOccupancy(view))
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    assert page.calendar._occupied == {(0, 10)} and page.calendar._unavailable == set()
    page._set_instrument(Instrument.OVEN_A)
    assert page.calendar._occupied == {(0, 9)} and page.calendar._unavailable == {6}


def test_block_menu_actions_change_gas_and_delete(qtbot, store):
    page, _ = make_page(qtbot, store)
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    page.calendar.rangeSelected.emit(0, 9, 10)
    (r,) = store.load_bookings(MON)
    page._change_gas(r.id, "Ar/H2")
    assert store.load_bookings(MON)[0].color == "DD7E6B"
    page.priority_list.setCurrentRow(0)
    page._delete_selected()
    assert store.load_bookings(MON) == []


def test_dragging_priority_list_reorders_and_saves(qtbot, store):
    page, _ = make_page(qtbot, store)
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    page.calendar.rangeSelected.emit(0, 9, 10)
    page.calendar.rangeSelected.emit(1, 9, 10)
    first, second = [r.id for r in store.load_bookings(MON)]
    model = page.priority_list.model()
    model.moveRow(model.index(0, 0).parent(), 1, model.index(0, 0).parent(), 0)
    qtbot.waitUntil(lambda: [r.id for r in store.load_bookings(MON)] == [second, first])
    qtbot.waitUntil(lambda: listed(page)[0].startswith("1. 10/13"))
    assert page.priority_list.item(0).data(ID_ROLE) == second


def test_copy_previous_week_adds_shifted_requests(qtbot, store):
    store.save_bookings(MON - timedelta(days=7), [BookingRequest("old", Instrument.TUBE_C, date(2026, 10, 7), 13, 15,
                                                                 "Ar", "A4C2F4")])
    page, _ = make_page(qtbot, store)
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    page._copy_previous_week()
    (r,) = store.load_bookings(MON)
    assert (r.date, r.instrument, r.start_hour) == (date(2026, 10, 14), Instrument.TUBE_C, 13) and r.id != "old"


def test_lock_disables_editing_and_offers_cancel_before_run(qtbot, store):
    cancels = []
    page, _ = make_page(qtbot, store, cancels=cancels)
    page.show()
    page.set_week(MON)
    wait_refreshed(qtbot, page)
    locked = ServiceStatus(ServicePhase.READY, run_at=RUN, target_monday=MON, editing_locked=True)
    page.apply_status(locked, RUN - timedelta(minutes=1))
    assert page.lock_bar.isVisible() and page.cancel_button.isVisible()
    assert not page.calendar.isEnabled() and not page.refresh_button.isEnabled()
    page.calendar.rangeSelected.emit(0, 9, 10)  # 鎖定時忽略
    assert store.load_bookings(MON) == []
    page._on_cancel_clicked()
    assert cancels == [1]
    page.apply_status(locked, RUN + timedelta(seconds=1))  # 已到開放時間：不能取消
    assert not page.cancel_button.isVisible()
    page.apply_status(ServiceStatus(ServicePhase.DONE, run_at=RUN, target_monday=MON), RUN + timedelta(minutes=1))
    assert not page.lock_bar.isVisible() and page.calendar.isEnabled()


def test_cancel_needs_confirmation(qtbot, store):
    cancels = []
    page, _ = make_page(qtbot, store, cancels=cancels, confirm=lambda *a: False)
    page._on_cancel_clicked()
    assert cancels == []


def test_stale_refresh_result_is_ignored_after_week_change(qtbot, store):
    nxt = MON + timedelta(days=7)
    gates = {MON: threading.Event(), nxt: threading.Event()}
    done = []

    def occupancy(monday):
        gates[monday].wait(5)
        done.append(monday)
        return WeekView(monday, frozenset(), frozenset(), {"N2": "FF0000"} if monday == MON else LEGEND)
    page, _ = make_page(qtbot, store, occupancy=occupancy)
    page.set_week(MON)
    page.set_week(nxt)  # 前一週的下載尚未完成
    gates[nxt].set()
    qtbot.waitUntil(lambda: page._view is not None)
    gates[MON].set()  # 舊的結果最後才到
    qtbot.waitUntil(lambda: len(done) == 2)
    qtbot.wait(50)
    assert page._view.monday == nxt
    assert [page.gas_combo.itemText(i) for i in range(page.gas_combo.count())] == ["Ar", "Ar/H2"]
