"""core.job.execute 搭配真正的 BrowserSheetWriter（網頁以 FakeSheetPage 模擬）：驗證兩層的約定接得起來。"""
from datetime import date

from instrument_booking.browser.sheets_writer import BrowserSheetWriter
from instrument_booking.core.job import WriterCrashed, execute
from instrument_booking.core.models import BookingRequest, CellFont, CellState, Instrument, ItemStatus
from instrument_booking.core.planner import Plan, PlannedWrite
from instrument_booking.core.sheet_locator import Target
from fake_sheet_page import FakeSheetPage

T0 = 1_791_522_000.0
MON = date(2026, 10, 12)
LAB = CellFont(12.0, True, "center")


class FakeClock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def planned(id, col, first, last, inst=Instrument.TUBE_A, start=13, end=17, color="A4C2F4"):
    req = BookingRequest(id, inst, MON, start, end, "Ar", color)
    return PlannedWrite(req, Target("202610", col, first, last), color, (LAB,) * (last - first + 1))


def run(writes, page, restart=None):
    clock = FakeClock(T0 - 30)
    writer = BrowserSheetWriter(page, restart=restart or (lambda: page), clock=clock, not_before=T0,
                                monotonic=page.now)
    plan = Plan(tuple(writes), (), tuple(w.request.id for w in writes))
    return execute(plan, writer, "Zoe", clock, T0, sleep=clock.sleep)


def test_books_free_ranges_and_skips_taken_one():
    page = FakeSheetPage()
    page.cells[("202610", "E11")] = CellState("Ping", "FDE49A")
    results = run([planned("a", 2, 10, 13), planned("b", 5, 10, 13, inst=Instrument.TUBE_B),
                   planned("c", 3, 10, 10, inst=Instrument.TUBE_C, start=13, end=14)], page)
    assert [r.status for r in results] == [ItemStatus.SUCCESS, ItemStatus.LIVE_CONFLICT, ItemStatus.SUCCESS]
    assert results[1].reason == "E11 已有「Ping」"
    assert page.cells[("202610", "B10")] == CellState("Zoe", "A4C2F4")
    assert page.cells[("202610", "C10")] == CellState("Zoe", "A4C2F4")
    assert ("202610", "C11") not in page.cells  # 1 格預約沒有寫到下一列
    assert ("202610", "E10") not in page.cells  # 衝突的那筆完全沒有貼上
    assert page.cells[("202610", "E11")] == CellState("Ping", "FDE49A")


def test_crash_during_copy_is_recovered_and_retried():
    page = FakeSheetPage()
    page.fail["Control+C"] = WriterCrashed("瀏覽器當掉")
    (r,) = run([planned("a", 2, 10, 13)], page)
    assert r.status is ItemStatus.SUCCESS
    assert page.cells[("202610", "B10")] == CellState("Zoe", "A4C2F4")


def test_someone_else_pastes_after_us_is_suspected_clash():
    page = FakeSheetPage()
    original_press = page.press

    def press(keys):
        original_press(keys)
        if keys == "Control+V":  # 我們貼上後，別人立刻貼上同一格
            page.cells[("202610", "B10")] = CellState("Rolling", "A4C2F4")
    page.press = press
    (r,) = run([planned("a", 2, 10, 13)], page)
    assert r.status is ItemStatus.SUSPECTED_CLASH
    assert "Rolling" in r.reason
