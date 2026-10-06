"""以本機歷史 xlsx 驗證定位規則能套用到所有工作表（CLAUDE.md 第 3 條）。"""
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pytest

from instrument_booking.core.models import BookingRequest, Instrument
from instrument_booking.core.sheet_locator import ALL_HOURS, SUB_COUNT, AmbiguousDate, SheetIndex, sheet_kind

XLSX = Path(__file__).resolve().parents[2] / "tube furnace reservation table.xlsx"
pytestmark = pytest.mark.skipif(not XLSX.exists(), reason="本機沒有歷史 xlsx（不進 git）")


@pytest.fixture(scope="module")
def wb():
    return openpyxl.load_workbook(XLSX, data_only=True)


@pytest.fixture(scope="module")
def index(wb):
    return SheetIndex.build(wb)


def month_sheets(wb):
    return [ws.title for ws in wb.worksheets if sheet_kind(ws.title)]


def test_every_month_sheet_has_day_blocks(wb, index):
    counts = {t: 0 for t in month_sheets(wb)}
    for b in index.all_blocks():
        counts[b.sheet] += 1
    assert len(counts) >= 137  # 2026-10-06 時的月份工作表數，之後只會增加
    assert min(counts.values()) >= 26, {t: n for t, n in counts.items() if n < 26}


def test_every_block_has_all_hours_in_order(index):
    for b in index.all_blocks():
        rows = [b.hour_rows[h] for h in ALL_HOURS]
        assert rows == sorted(rows), (b.sheet, b.date)
        assert rows[0] > b.date_row, (b.sheet, b.date)


def test_every_block_is_wide_enough(index):
    for b in index.all_blocks():
        assert b.width >= SUB_COUNT[sheet_kind(b.sheet)], (b.sheet, b.date)


def test_recent_tube_dates_appear_exactly_once(index):
    d = date(2025, 6, 30)
    while d <= date(2026, 11, 1):
        index.day("tube", d)  # 不可拋出例外
        d += timedelta(days=1)


def test_recent_oven_dates_except_known_errors(index):
    # 2月oven2026 的 D35/D51 誤寫為 1/20、1/27 → 1/20、1/27 重複，2/17、2/24 缺漏
    known = {date(2026, 1, 20), date(2026, 1, 27), date(2026, 2, 17), date(2026, 2, 24)}
    d = date(2025, 6, 30)
    while d <= date(2026, 11, 1):
        if d not in known:
            index.day("oven", d)
        d += timedelta(days=1)
    with pytest.raises(AmbiguousDate):
        index.day("oven", date(2026, 1, 20))


def test_known_reservation_is_located(wb, index):
    t = index.locate(BookingRequest("r", Instrument.TUBE_A, date(2026, 10, 6), 11, 12, "Ar", "A4C2F4"))
    assert (t.sheet, t.first_cell) == ("202610", "E8")
    assert wb["202610"]["E8"].value == "Zoe"


def test_inserted_row_in_202609_is_covered(index):
    t = index.locate(BookingRequest("r", Instrument.TUBE_A, date(2026, 9, 28), 22, 24, "Ar", "A4C2F4"))
    assert (t.sheet, t.first_row, t.last_row) == ("202609", 83, 85)


def test_cross_month_week_belongs_to_previous_sheet(index):
    assert index.day("tube", date(2026, 10, 1)).sheet == "202609"
