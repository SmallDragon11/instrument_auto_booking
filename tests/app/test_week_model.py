from datetime import date, datetime, time

import pytest

from instrument_booking.app.week_model import (
    change_gas,
    copy_previous_week,
    day_label,
    describe,
    displayed_week,
    drag_hours,
    layout_lanes,
    make_request,
    remove,
    reorder,
    slot_warnings,
)
from instrument_booking.core.models import TAIPEI, BookingRequest, Instrument
from instrument_booking.service.occupancy import WeekView
from instrument_booking.service.settings import Settings

MON = date(2026, 10, 12)
LEGEND = {"Ar": "A4C2F4", "Ar/H2": "DD7E6B"}


def req(id, start, end, *, inst=Instrument.TUBE_A, day=MON, gas="Ar", color="A4C2F4"):
    return BookingRequest(id, inst, day, start, end, gas, color)


def ids():
    counter = iter(range(1, 100))
    return lambda: f"n{next(counter)}"


def test_labels():
    assert day_label(date(2026, 10, 12)) == "10/12（一）"
    assert day_label(date(2026, 10, 18)) == "10/18（日）"
    assert describe(req("a", 9, 12)) == "10/12（一）A-牆 09:00–12:00 Ar"
    oven = BookingRequest("b", Instrument.OVEN_B, MON, 23, 24, None, "A4C2F4")
    assert describe(oven) == "10/12（一）ovenB 23:00–24:00"


def test_drag_hours_includes_both_cells_in_any_direction():
    assert drag_hours(9, 11) == (9, 12)
    assert drag_hours(11, 9) == (9, 12)
    assert drag_hours(23, 23) == (23, 24)
    with pytest.raises(ValueError):
        drag_hours(8, 10)
    with pytest.raises(ValueError):
        drag_hours(22, 24)


def test_make_request_uses_legend_color_for_tube_and_fixed_color_for_oven():
    tube = make_request(Instrument.TUBE_B, MON, 9, 12, "Ar/H2", LEGEND, id_factory=lambda: "x")
    assert tube == BookingRequest("x", Instrument.TUBE_B, MON, 9, 12, "Ar/H2", "DD7E6B")
    oven_a = make_request(Instrument.OVEN_A, MON, 9, 10, "Ar", LEGEND, id_factory=lambda: "y")
    assert (oven_a.gas, oven_a.color) == (None, "FFE599")  # 烘箱忽略氣體
    oven_b = make_request(Instrument.OVEN_B, MON, 9, 10, None, {}, id_factory=lambda: "z")
    assert oven_b.color == "A4C2F4"


def test_make_request_rejects_missing_or_unknown_gas_for_tube():
    with pytest.raises(ValueError, match="請先選擇氣體"):
        make_request(Instrument.TUBE_A, MON, 9, 10, None, LEGEND)
    with pytest.raises(ValueError, match="N2"):
        make_request(Instrument.TUBE_A, MON, 9, 10, "N2", LEGEND)


def test_change_gas_remove_and_reorder_keep_other_requests():
    a, b = req("a", 9, 12), req("b", 13, 15)
    changed = change_gas([a, b], "b", "Ar/H2", LEGEND)
    assert changed[0] == a and (changed[1].gas, changed[1].color) == ("Ar/H2", "DD7E6B")
    assert remove([a, b], "a") == [b]
    assert reorder([a, b], ["b", "a"]) == [b, a]
    with pytest.raises(ValueError):
        reorder([a, b], ["b"])


def test_copy_previous_week_shifts_dates_keeps_order_and_skips_duplicates():
    last = [req("p1", 9, 12, day=date(2026, 10, 5)), req("p2", 13, 15, day=date(2026, 10, 6), inst=Instrument.TUBE_C)]
    current = [req("c1", 9, 12, day=MON)]  # 與 p1 +7 天相同
    result, added = copy_previous_week(last, current, id_factory=ids())
    assert added == 1
    assert result[0].id == "c1"
    assert (result[1].id, result[1].date, result[1].instrument) == ("n2", date(2026, 10, 13), Instrument.TUBE_C)


def test_layout_lanes_puts_overlapping_blocks_side_by_side():
    reqs = [
        req("a", 9, 12),
        req("b", 10, 11),
        req("c", 11, 14),   # 與 a 重疊；b 結束後可用第 2 欄
        req("d", 15, 16),   # 不重疊 → 獨佔
        req("e", 9, 10, inst=Instrument.TUBE_B),  # 不同儀器 → 獨立
        req("f", 9, 10, day=date(2026, 10, 13)),  # 不同天 → 獨立
    ]
    assert layout_lanes(reqs) == {
        "a": (0, 2), "b": (1, 2), "c": (1, 2),
        "d": (0, 1), "e": (0, 1), "f": (0, 1),
    }


def test_layout_lanes_adjacent_blocks_do_not_overlap():
    assert layout_lanes([req("a", 9, 10), req("b", 10, 11)]) == {"a": (0, 1), "b": (0, 1)}


def test_layout_lanes_three_way_overlap():
    lanes = layout_lanes([req("a", 9, 12), req("b", 9, 12), req("c", 10, 11)])
    assert sorted(lanes.values()) == [(0, 3), (1, 3), (2, 3)]


def test_slot_warnings_report_occupied_hours_and_missing_day():
    view = WeekView(MON, frozenset({(Instrument.TUBE_A, MON, 10), (Instrument.TUBE_B, MON, 9)}),
                    frozenset({("oven", MON)}), LEGEND)
    assert slot_warnings(req("a", 9, 12), view) == ["10:00 目前已被預約，到時這筆會被略過"]
    oven = BookingRequest("o", Instrument.OVEN_A, MON, 9, 10, None, "FFE599")
    assert slot_warnings(oven, view) == ["預約表中找不到 10/12（一）（工作表可能尚未建立）"]
    assert slot_warnings(req("a", 9, 12), None) == []


def test_displayed_week_prefers_service_target_then_settings():
    now = datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI)  # 週三
    assert displayed_week(date(2026, 10, 5), now, Settings()) == date(2026, 10, 5)  # 服務正在補跑上一週期
    assert displayed_week(None, now, Settings()) == MON  # 週五 13:00 → 下週一
    assert displayed_week(None, now, Settings(run_weekday=0, run_time=time(9, 0))) == date(2026, 10, 19)
    assert displayed_week(None, now, Settings(run_weekday=9)) == MON  # 星期無效時用預設
