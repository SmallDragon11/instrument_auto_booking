from datetime import date, datetime, timedelta, timezone

import pytest

from openpyxl.styles import Alignment, Font

from instrument_booking.core.models import TAIPEI, BookingRequest, CellFont, Instrument, ItemStatus
from instrument_booking.core.planner import make_plan
from instrument_booking.core.sheet_locator import SheetIndex
from sheet_builder import add_oven_sheet, add_tube_sheet, new_workbook, solid

MON = date(2026, 10, 12)
NOW = datetime(2026, 10, 9, 12, 50, tzinfo=TAIPEI)


@pytest.fixture
def wb():
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON, blank_row_after_hour=22)
    add_oven_sheet(wb, "10月oven2026", MON)
    return wb


def plan_for(wb, *requests, now=NOW):
    return make_plan(wb, SheetIndex.build(wb), list(requests), now)


def tube(id, start, end, gas="Ar", color="A4C2F4", day=MON, inst=Instrument.TUBE_A):
    return BookingRequest(id, inst, day, start, end, gas, color)


def test_all_free_requests_are_planned_in_order(wb):
    oven = BookingRequest("o1", Instrument.OVEN_A, MON, 10, 18, None, "FFE599")
    p = plan_for(wb, tube("t1", 13, 17, gas="H2", color="F4CCCC"), oven)
    assert [w.request.id for w in p.writes] == ["t1", "o1"]
    assert [w.color for w in p.writes] == ["F4CCCC", "FFE599"]
    assert p.writes[0].target.a1 == "B10:B13"
    assert p.skipped == ()
    assert p.order == ("t1", "o1")


def test_value_in_snapshot_is_taken(wb):
    wb["202610"]["B11"] = "Ping"
    p = plan_for(wb, tube("t1", 13, 17))
    assert p.writes == ()
    (r,) = p.skipped
    assert r.status is ItemStatus.TAKEN_IN_SNAPSHOT
    assert r.reason == "B11 已有「Ping」"


def test_color_only_cell_is_taken(wb):
    wb["202610"]["B12"].fill = solid("FDE49A")
    (r,) = plan_for(wb, tube("t1", 13, 17)).skipped
    assert r.status is ItemStatus.TAKEN_IN_SNAPSHOT
    assert r.reason == "B12 已塗色"


def test_inserted_blank_row_is_checked_too(wb):
    # 22 點在第 19 列，誤插入的列是第 20 列，23 點在第 21 列
    wb["202610"]["B20"].fill = solid("A4C2F4")
    (r,) = plan_for(wb, tube("t1", 22, 24)).skipped
    assert r.reason == "B20 已塗色"


def test_missing_sheet_fails(wb):
    (r,) = plan_for(wb, tube("t1", 9, 10, day=date(2026, 11, 2))).skipped
    assert r.status is ItemStatus.FAILED
    assert "找不到" in r.reason


def test_unknown_gas_uses_request_color_with_warning(wb):
    (w,) = plan_for(wb, tube("t1", 9, 10, gas="He", color="ABCDEF")).writes
    assert w.color == "ABCDEF"
    assert "He" in w.warning


def test_past_slot_fails(wb):
    now = datetime(2026, 10, 12, 13, 0, tzinfo=TAIPEI)
    p = plan_for(wb, tube("past", 9, 10), tube("future", 14, 15), now=now)
    assert [w.request.id for w in p.writes] == ["future"]
    assert p.skipped[0].status is ItemStatus.FAILED
    assert p.skipped[0].reason == "時段已開始或已過去"


def test_self_overlap_is_left_to_execution(wb):
    p = plan_for(wb, tube("t1", 13, 17), tube("t2", 15, 19))
    assert [w.request.id for w in p.writes] == ["t1", "t2"]


def test_fonts_are_read_per_cell_from_snapshot(wb):
    ws = wb["202610"]
    ws["B10"].font = Font(size=12, bold=True)
    ws["B10"].alignment = Alignment(horizontal="center")
    (w,) = plan_for(wb, tube("t1", 13, 15)).writes  # B10:B11
    assert w.fonts == (CellFont(12.0, True, "center"), CellFont(11.0, False, None))


def test_naive_now_is_rejected(wb):
    with pytest.raises(ValueError):
        plan_for(wb, tube("t1", 13, 17), now=datetime(2026, 10, 9, 12, 50))


def test_now_in_other_timezone_is_compared_in_taipei(wb):
    # UTC+9 的 10/12 13:30＝台北 12:30：13 點的時段尚未開始，12 點的已開始
    now = datetime(2026, 10, 12, 13, 30, tzinfo=timezone(timedelta(hours=9)))
    p = plan_for(wb, tube("past", 12, 13), tube("future", 13, 14), now=now)
    assert [w.request.id for w in p.writes] == ["future"]
    assert [r.request_id for r in p.skipped] == ["past"]
