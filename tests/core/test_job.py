from datetime import date, datetime

import pytest
from openpyxl.utils import get_column_letter, range_boundaries

from instrument_booking.core.job import (
    EarlyWriteError,
    WriterCrashed,
    WriterError,
    execute,
    preflight,
    wait_until,
)
from instrument_booking.core.models import TAIPEI, BookingRequest, CellFont, CellState, Instrument, ItemStatus
from instrument_booking.core.planner import Plan, PlannedWrite
from instrument_booking.core.sheet_locator import Target
from sheet_builder import add_tube_sheet, new_workbook

LAB_FONT = CellFont(12.0, True, "center")

MON = date(2026, 10, 12)
NAME = "Zoe"
T0 = 1_791_522_000.0  # 10/9 13:00:00（台北）


class FakeClock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeWriter:
    """以 {(sheet, "B10"): CellState} 模擬試算表。hook 可注入他人操作。"""

    def __init__(self, clock):
        self.clock = clock
        self.cells = {}
        self.log = []
        self.before_read = None   # fn(writer, sheet, a1, nth_read)
        self.after_paste = None   # fn(writer, sheet, a1)
        self.fail_paste = {}      # a1 -> 例外（貼上前拋出）
        self.crash_after_paste = set()  # a1：貼上成功後拋出 WriterCrashed（僅一次）
        self.fail_prewarm = None  # 預熱時拋出的例外
        self.fail_recover = None  # recover() 時拋出的例外
        self.on_recover = None    # fn(writer)：recover() 成功時呼叫
        self.reads = 0

    @staticmethod
    def names(a1):
        c1, r1, c2, r2 = range_boundaries(a1)
        return [f"{get_column_letter(c)}{r}" for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]

    def prewarm(self, sheets):
        self.log.append(("prewarm", tuple(sheets)))
        if self.fail_prewarm:
            raise self.fail_prewarm

    def read_range(self, sheet, a1):
        self.reads += 1
        if self.before_read:
            self.before_read(self, sheet, a1, self.reads)
        self.log.append(("read", a1))
        return [self.cells.get((sheet, n), CellState(None, None)) for n in self.names(a1)]

    def paste_booking(self, sheet, a1, color, name, fonts):
        if a1 in self.fail_paste:
            raise self.fail_paste.pop(a1)
        assert len(fonts) == len(self.names(a1)), "每一格都必須提供字型"
        for i, n in enumerate(self.names(a1)):
            self.cells[(sheet, n)] = CellState(name if i == 0 else None, color)
        self.log.append(("paste", a1, self.clock.now(), tuple(fonts)))
        if self.after_paste:
            self.after_paste(self, sheet, a1)
        if a1 in self.crash_after_paste:
            self.crash_after_paste.discard(a1)
            raise WriterCrashed("瀏覽器當掉")

    def recover(self):
        self.log.append(("recover",))
        if self.fail_recover:
            raise self.fail_recover
        if self.on_recover:
            self.on_recover(self)


def pw(id, col, first, last, color="A4C2F4", inst=Instrument.TUBE_A, start=13, end=17, warning=None):
    req = BookingRequest(id, inst, MON, start, end, "Ar", color)
    fonts = (LAB_FONT,) * (last - first + 1)
    return PlannedWrite(req, Target("202610", col, first, last), color, fonts, warning)


def run(writes, writer=None, clock=None, skipped=(), not_before=T0):
    clock = clock or FakeClock(T0 - 60)
    writer = writer or FakeWriter(clock)
    plan = Plan(tuple(writes), tuple(skipped), tuple(w.request.id for w in writes) + tuple(s.request_id for s in skipped))
    results = execute(plan, writer, NAME, clock, not_before, sleep=clock.sleep, verify_delay=5.0)
    return results, writer, clock


def test_wait_until():
    c = FakeClock(10.0)
    wait_until(c, 12.0, c.sleep)
    assert 12.0 <= c.t < 12.06


def test_happy_path_waits_then_writes_and_verifies():
    results, w, _ = run([pw("a", 2, 10, 13), pw("b", 6, 10, 11, inst=Instrument.TUBE_B, start=13, end=15)])
    assert [r.status for r in results] == [ItemStatus.SUCCESS, ItemStatus.SUCCESS]
    assert w.log[0] == ("prewarm", ("202610",))
    pastes = [e for e in w.log if e[0] == "paste"]
    assert all(t >= T0 for _, _, t, _ in pastes)  # 絕不偷跑
    assert results[0].written_at == datetime.fromtimestamp(pastes[0][2], TAIPEI)
    assert pastes[0][3] == (LAB_FONT,) * 4  # 每格字型都有傳給寫入器
    assert w.cells[("202610", "B10")] == CellState(NAME, "A4C2F4")
    assert w.cells[("202610", "B13")] == CellState(None, "A4C2F4")


def test_live_conflict_skips_without_writing():
    def someone_pasted(w, sheet, a1, n):
        if n == 1:
            w.cells[("202610", "B11")] = CellState(None, "FDE49A")  # 只有底色
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.before_read = someone_pasted
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert r.status is ItemStatus.LIVE_CONFLICT
    assert r.reason == "B11 已塗色"
    assert not [e for e in w.log if e[0] == "paste"]


def test_self_overlap_skipped_after_higher_priority_succeeds():
    results, w, _ = run([pw("a", 2, 10, 13), pw("b", 2, 12, 15, start=15, end=19)])
    assert results[1].status is ItemStatus.SELF_OVERLAP
    assert "a" in results[1].reason


def test_self_overlap_falls_back_when_higher_priority_conflicts():
    def taken(w, sheet, a1, n):
        if n == 1:
            w.cells[("202610", "B10")] = CellState("Ping", "A4C2F4")
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.before_read = taken
    # b 與 a 重疊（15–19 vs 13–17）；a 被搶走沒寫入 → b 照常嘗試
    results, w, _ = run([pw("a", 2, 10, 13), pw("b", 2, 12, 15, start=15, end=19)], writer, clock)
    assert [r.status for r in results] == [ItemStatus.LIVE_CONFLICT, ItemStatus.SUCCESS]


def test_suspected_clash_when_overwritten_before_verification():
    def clobber(w, sheet, a1):
        w.cells[("202610", "B10")] = CellState("Rolling", "A4C2F4")
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.after_paste = clobber
    (r,), _, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert r.status is ItemStatus.SUSPECTED_CLASH
    assert "Rolling" in r.reason


def test_same_colour_overwrite_inside_range_is_suspected_clash():
    def paste_over_tail(w, sheet, a1):
        w.cells[("202610", "B12")] = CellState("Rolling", "A4C2F4")  # 別人用同一氣體色貼進我們範圍後段
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.after_paste = paste_over_tail
    (r,), _, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert r.status is ItemStatus.SUSPECTED_CLASH
    assert r.reason == "驗證時 B12 有「Rolling」"


def test_short_read_never_pastes():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.read_range = lambda sheet, a1: []  # 例如剪貼簿解析失敗
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert r.status is ItemStatus.FAILED
    assert "為安全起見不寫入" in r.reason
    assert not [e for e in w.log if e[0] == "paste"]


def test_short_read_on_verification_is_unverified_success():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    original = writer.read_range
    calls = []

    def read(sheet, a1):
        calls.append(a1)
        return original(sheet, a1) if len(calls) == 1 else []
    writer.read_range = read
    (r,), _, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert r.status is ItemStatus.SUCCESS and "無法驗證" in r.warning


def test_writer_error_fails_item_and_continues():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_paste["B10:B13"] = WriterError("調色盤逾時")
    results, _, _ = run([pw("a", 2, 10, 13), pw("b", 3, 10, 13, inst=Instrument.TUBE_B)], writer, clock)
    assert results[0].status is ItemStatus.FAILED and "調色盤逾時" in results[0].reason
    assert results[1].status is ItemStatus.SUCCESS


def test_crash_after_paste_is_verified_as_success():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.crash_after_paste.add("B10:B13")
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert ("recover",) in w.log
    assert r.status is ItemStatus.SUCCESS


def pastes(w):
    return [e for e in w.log if e[0] == "paste"]


def test_crash_before_paste_recovers_and_retries():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_paste["B10:B13"] = WriterCrashed("瀏覽器當掉")
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert w.log.count(("recover",)) == 1
    assert r.status is ItemStatus.SUCCESS
    assert len(pastes(w)) == 1 and pastes(w)[0][2] >= T0
    assert w.cells[("202610", "B10")] == CellState(NAME, "A4C2F4")


def test_unknown_paste_exception_is_treated_as_crash_and_retried():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_paste["B10:B13"] = RuntimeError("未預期的錯誤")
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert ("recover",) in w.log
    assert r.status is ItemStatus.SUCCESS
    assert len(pastes(w)) == 1


@pytest.mark.parametrize("exc", [WriterCrashed("當掉"), RuntimeError("未知")])
def test_first_read_crash_recovers_and_retries(exc):
    def crash_first(w, sheet, a1, n):
        if n == 1:
            raise exc
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.before_read = crash_first
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert w.log.count(("recover",)) == 1
    assert r.status is ItemStatus.SUCCESS
    assert len(pastes(w)) == 1


def test_crash_after_paste_retry_sees_own_booking_and_overlap_is_self_overlap():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.crash_after_paste.add("B10:B13")
    # b（15–19，B12:B15）與 a（13–17）重疊；a 貼上後才當掉，但貼上其實已生效
    results, w, _ = run([pw("a", 2, 10, 13), pw("b", 2, 12, 15, start=15, end=19)], writer, clock)
    assert [r.status for r in results] == [ItemStatus.SUCCESS, ItemStatus.SELF_OVERLAP]
    assert "a" in results[1].reason
    assert len(pastes(w)) == 1  # 已寫入的不重寫


def test_crash_again_on_retry_is_left_to_verification_without_more_retries():
    def crash_twice(w, sheet, a1, n):
        if n in (1, 2):
            raise WriterCrashed("當掉")
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.before_read = crash_twice
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert w.log.count(("recover",)) == 2
    assert writer.reads == 3  # 第一次＋重試＋驗證，不再重試
    assert not pastes(w)
    assert r.status is ItemStatus.FAILED and "寫入未完成" in r.reason


def test_crash_again_on_retry_after_effective_paste_is_verified_success():
    def crash_on_retry_read(w, sheet, a1, n):
        if n == 2:
            raise RuntimeError("未知")
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.crash_after_paste.add("B10:B13")
    writer.before_read = crash_on_retry_read
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert w.log.count(("recover",)) == 2
    assert len(pastes(w)) == 1
    assert r.status is ItemStatus.SUCCESS and r.written_at is None  # 貼上時間不確定


def test_short_read_on_retry_never_pastes():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_paste["B10:B13"] = WriterCrashed("當掉")
    original = writer.read_range
    calls = []

    def read(sheet, a1):
        calls.append(a1)
        return [] if len(calls) == 2 else original(sheet, a1)
    writer.read_range = read
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert len(calls) == 3  # 第一次＋重試＋驗證
    assert not pastes(w)
    assert r.status is ItemStatus.FAILED and "得到 0 格" in r.reason


def test_retry_after_recover_still_refuses_early_write():
    def jump_back(w):
        w.clock.t = T0 - 10  # 復原期間系統時間被校正
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_paste["B10:B13"] = WriterCrashed("當掉")
    writer.on_recover = jump_back
    results, w, _ = run([pw("a", 2, 10, 13), pw("b", 3, 10, 13, inst=Instrument.TUBE_B)], writer, clock)
    assert not pastes(w)
    assert [r.status for r in results] == [ItemStatus.FAILED, ItemStatus.FAILED]
    assert all("時間未到" in r.reason for r in results)


def test_writer_side_early_write_refusal_aborts_remaining_writes():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_paste["B10:B13"] = EarlyWriteError("寫入器：時間未到，拒絕貼上")
    results, w, _ = run([pw("a", 2, 10, 13), pw("b", 3, 10, 13, inst=Instrument.TUBE_B)], writer, clock)
    assert not pastes(w)
    assert ("recover",) not in w.log
    assert [r.status for r in results] == [ItemStatus.FAILED, ItemStatus.FAILED]
    assert all("時間未到" in r.reason for r in results)


def test_recover_failure_keeps_earlier_success_and_fails_the_rest():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_paste["C10:C13"] = WriterCrashed("當掉")
    writer.fail_recover = RuntimeError("Edge 無法啟動")
    results, w, _ = run([pw("a", 2, 10, 13),
                         pw("b", 3, 10, 13, inst=Instrument.TUBE_B),
                         pw("c", 4, 10, 13, inst=Instrument.TUBE_C)], writer, clock)
    assert [r.status for r in results] == [ItemStatus.SUCCESS, ItemStatus.FAILED, ItemStatus.FAILED]
    assert all("瀏覽器無法復原" in r.reason and "Edge 無法啟動" in r.reason for r in results[1:])
    assert [e[1] for e in pastes(w)] == ["B10:B13"]


def test_dead_browser_still_reports_every_item():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.crash_after_paste.add("C10:C13")
    writer.fail_recover = RuntimeError("Edge 無法啟動")

    def dead(w, sheet, a1, n):
        if ("recover",) in w.log:
            raise WriterCrashed("瀏覽器已關閉")
    writer.before_read = dead
    results, w, _ = run([pw("a", 2, 10, 13),
                         pw("b", 3, 10, 13, inst=Instrument.TUBE_B),
                         pw("c", 4, 10, 13, inst=Instrument.TUBE_C)], writer, clock)
    assert [r.request_id for r in results] == ["a", "b", "c"]
    a, b, c = results
    assert a.status is ItemStatus.SUCCESS and "無法驗證" in a.warning  # 已貼上的仍回報成功
    assert b.status is ItemStatus.FAILED and "瀏覽器無法復原" in b.reason and "無法確認" in b.reason
    assert c.status is ItemStatus.FAILED and "瀏覽器無法復原" in c.reason


@pytest.mark.parametrize("exc,recovers", [
    (WriterError("逾時"), False), (WriterCrashed("當掉"), True), (RuntimeError("未知"), True)])
def test_prewarm_failure_does_not_stop_writes(exc, recovers):
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_prewarm = exc
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert (("recover",) in w.log) is recovers
    assert r.status is ItemStatus.SUCCESS


def test_prewarm_crash_with_failed_recover_fails_all_without_raising():
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.fail_prewarm = WriterCrashed("當掉")
    writer.fail_recover = WriterCrashed("Edge 無法啟動")
    results, w, _ = run([pw("a", 2, 10, 13), pw("b", 3, 10, 13, inst=Instrument.TUBE_B)], writer, clock)
    assert [r.status for r in results] == [ItemStatus.FAILED, ItemStatus.FAILED]
    assert all("瀏覽器無法復原" in r.reason for r in results)
    assert not pastes(w)


def test_unknown_exception_on_verification_keeps_success_with_warning():
    def fail_on_verify(w, sheet, a1, n):
        if n == 2:
            raise RuntimeError("未知")
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.before_read = fail_on_verify
    (r,), w, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert ("recover",) in w.log
    assert r.status is ItemStatus.SUCCESS and "無法驗證" in r.warning


def test_clock_jump_backwards_aborts_remaining_writes():
    def jump_back(w, sheet, a1, n):
        if n == 1:
            w.clock.t = T0 - 10  # 例如系統時間被校正
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.before_read = jump_back
    results, w, _ = run([pw("a", 2, 10, 13), pw("b", 3, 10, 13, inst=Instrument.TUBE_B)], writer, clock)
    assert [r.status for r in results] == [ItemStatus.FAILED, ItemStatus.FAILED]
    assert all("時間未到" in r.reason for r in results)
    assert not [e for e in w.log if e[0] == "paste"]


def test_warning_is_kept_on_success():
    (r,), _, _ = run([pw("a", 2, 10, 13, warning="圖例找不到 He")])
    assert r.status is ItemStatus.SUCCESS and r.warning == "圖例找不到 He"


def test_verification_read_failure_keeps_success_with_warning():
    def fail_on_verify(w, sheet, a1, n):
        if n == 2:
            raise WriterError("讀取逾時")
    clock = FakeClock(T0 - 60)
    writer = FakeWriter(clock)
    writer.before_read = fail_on_verify
    (r,), _, _ = run([pw("a", 2, 10, 13)], writer, clock)
    assert r.status is ItemStatus.SUCCESS and "無法驗證" in r.warning


def test_results_follow_plan_order_including_skipped():
    from instrument_booking.core.models import ItemResult
    skipped = (ItemResult("x", ItemStatus.FAILED, reason="找不到"),)
    results, _, _ = run([pw("a", 2, 10, 13)], skipped=skipped)
    assert [r.request_id for r in results] == ["a", "x"]


def test_preflight_downloads_and_plans(tmp_path):
    wb = new_workbook()
    add_tube_sheet(wb, "202610", MON)
    path = tmp_path / "snapshot.xlsx"
    wb.save(path)

    class Source:
        def download(self):
            return path

    req = BookingRequest("a", Instrument.TUBE_A, MON, 13, 17, "Ar", "A4C2F4")
    plan = preflight(Source(), [req], datetime(2026, 10, 9, 12, 50, tzinfo=TAIPEI))
    assert [w.target.a1 for w in plan.writes] == ["B10:B13"]
