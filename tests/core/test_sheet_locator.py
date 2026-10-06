from datetime import date, datetime, timedelta

import pytest

from instrument_booking.core.models import BookingRequest, Instrument
from instrument_booking.core.sheet_locator import (
    AmbiguousDate,
    SheetIndex,
    SheetMissing,
    parse_time_label,
    sheet_kind,
)
from sheet_builder import add_oven_sheet, add_tube_sheet, new_workbook

MON = date(2026, 10, 12)


def req(instrument, day, start, end):
    tube = instrument.kind == "tube"
    return BookingRequest(id="r", instrument=instrument, date=day, start_hour=start, end_hour=end,
                          gas="Ar" if tube else None, color="A4C2F4" if tube else "FFE599")


@pytest.mark.parametrize("title,kind", [
    ("202610", "tube"), ("10月oven2026", "oven"), ("9月oven2026", "oven"), ("6月oven", "oven"),
    ("1月oven2023 ", "oven"), ("Sheet13", None), ("oven blank", None), ("oven", None), ("工作表5", None),
])
def test_sheet_kind(title, kind):
    assert sheet_kind(title) == kind


@pytest.mark.parametrize("value,hour", [
    ("0900~1000", 9), ("2300~", 23), (" 1300~1400 ", 13), (None, None), ("°C", None), ("0800~0900", None),
])
def test_parse_time_label(value, hour):
    assert parse_time_label(value) == hour


def test_locate_tube():
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON)
    t = SheetIndex.build(wb).locate(req(Instrument.TUBE_B, MON + timedelta(days=1), 13, 17))
    # 週二起始欄 E(5)，B-窗 → F(6)；日期列 5，09 點在第 6 列 → 13 點第 10 列、16 點第 13 列
    assert (t.sheet, t.column, t.first_row, t.last_row) == ("202610", 6, 10, 13)
    assert t.a1 == "F10:F13"
    assert t.first_cell == "F10"
    assert list(t.rows) == [10, 11, 12, 13]


def test_locate_oven():
    wb = new_workbook()
    add_oven_sheet(wb, "10月oven2026", MON)
    t = SheetIndex.build(wb).locate(req(Instrument.OVEN_B, MON, 10, 12))
    # 週一起始欄 B(2)，ovenB → C(3)；日期列 3，10 點在第 5 列、11 點第 6 列
    assert t.a1 == "C5:C6"


def test_second_week_block():
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON, weeks=2)
    t = SheetIndex.build(wb).locate(req(Instrument.TUBE_A, MON + timedelta(days=7), 9, 10))
    assert t.a1 == "B22:B22"  # 第二週日期列 21


def test_cross_month_week_lives_in_previous_sheet():
    wb = new_workbook()
    add_tube_sheet(wb, "202609", date(2026, 9, 28))
    add_tube_sheet(wb, "202610", date(2026, 10, 5))
    assert SheetIndex.build(wb).day("tube", date(2026, 10, 1)).sheet == "202609"


def test_inserted_blank_row_is_skipped_but_covered():
    wb = new_workbook()
    ws, layout = add_tube_sheet(wb, "202609", MON, blank_row_after_hour=22)
    hours = layout[MON][2]
    assert hours[23] == hours[22] + 2  # 中間夾一列空白
    index = SheetIndex.build(wb)
    t = index.locate(req(Instrument.TUBE_A, MON, 22, 24))
    assert (t.first_row, t.last_row) == (hours[22], hours[23])
    assert len(t.rows) == 3  # 包含誤插入的列
    t2 = index.locate(req(Instrument.TUBE_A, MON, 23, 24))
    assert (t2.first_row, t2.last_row) == (hours[23], hours[23])


def test_header_labels_are_not_used():
    wb = new_workbook()
    ws, _ = add_tube_sheet(wb, "202511", MON)
    ws["H3"] = None  # 標頭被刪
    t = SheetIndex.build(wb).locate(req(Instrument.TUBE_A, MON + timedelta(days=2), 9, 10))
    assert t.column == 8  # 週三起始欄 H


def test_four_wide_friday_shifts_weekend():
    wb = new_workbook()
    add_tube_sheet(wb, "202212", MON, widths=(3, 3, 3, 3, 4, 3, 3))
    index = SheetIndex.build(wb)
    assert index.locate(req(Instrument.TUBE_C, MON + timedelta(days=4), 9, 10)).column == 16  # 週五 N..Q，C → P
    assert index.locate(req(Instrument.TUBE_A, MON + timedelta(days=5), 9, 10)).column == 18  # 週六從 R 開始


def test_missing_date_raises_sheet_missing():
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON)
    with pytest.raises(SheetMissing, match="11/02"):
        SheetIndex.build(wb).locate(req(Instrument.TUBE_A, date(2026, 11, 2), 9, 10))


def test_duplicate_date_raises_ambiguous():
    wb = new_workbook()
    add_oven_sheet(wb, "1月oven2026", MON)
    add_oven_sheet(wb, "2月oven2026", MON)
    with pytest.raises(AmbiguousDate, match="1月oven2026"):
        SheetIndex.build(wb).day("oven", MON)


def test_stray_merged_date_without_time_rows_is_ignored():
    wb = new_workbook()
    ws, _ = add_oven_sheet(wb, "8月oven2023", MON)
    ws.cell(83, 2, datetime.combine(MON, datetime.min.time()))
    ws.merge_cells("B83:C83")
    assert SheetIndex.build(wb).day("oven", MON).date_row == 3


def test_unmerged_date_is_ignored():
    wb = new_workbook()
    ws, _ = add_tube_sheet(wb, "202610", MON)
    ws["X5"] = datetime.combine(MON, datetime.min.time())  # 右側備註區的日期
    assert SheetIndex.build(wb).day("tube", MON).first_col == 2


def test_non_month_sheets_are_ignored():
    wb = new_workbook()
    add_tube_sheet(wb, "Sheet13", MON)
    with pytest.raises(SheetMissing):
        SheetIndex.build(wb).day("tube", MON)


def test_tube_and_oven_are_indexed_separately():
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON)
    add_oven_sheet(wb, "10月oven2026", MON)
    index = SheetIndex.build(wb)
    assert index.day("tube", MON).sheet == "202610"
    assert index.day("oven", MON).sheet == "10月oven2026"
    assert len(index.all_blocks()) == 14
