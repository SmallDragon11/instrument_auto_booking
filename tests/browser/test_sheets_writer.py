import pytest

from instrument_booking.browser.sheets_writer import BrowserSheetWriter
from instrument_booking.core.clipboard_html import count_rows
from instrument_booking.core.job import EarlyWriteError, WriterCrashed, WriterError
from instrument_booking.core.models import CellFont, CellState
from fake_sheet_page import FakeSheetPage, google_html

T0 = 1_791_522_000.0
LAB = CellFont(12.0, True, "center")


class FixedClock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t


def make(page=None, *, clock_t=T0 + 1, restart=None):
    page = page or FakeSheetPage()
    writer = BrowserSheetWriter(page, restart=restart or (lambda: page), clock=FixedClock(clock_t),
                                not_before=T0, monotonic=page.now)
    return writer, page


def test_read_range_returns_cells_in_row_order():
    writer, page = make()
    page.cells[("202610", "B11")] = CellState("Ping", "FDE49A")
    assert writer.read_range("202610", "B10:B12") == [
        CellState(None, None), CellState("Ping", "FDE49A"), CellState(None, None)]
    assert ("jump", "'202610'!B10:B12") in page.log


def test_single_cell_reads_two_rows_and_drops_the_extra():
    writer, page = make()
    page.cells[("202610", "B11")] = CellState("Ping", "FDE49A")  # 多讀的下一列不可影響結果
    assert writer.read_range("202610", "B10:B10") == [CellState(None, None)]
    assert ("jump", "'202610'!B10:B11") in page.log


def test_single_cell_colour_is_visible():
    writer, page = make()
    page.cells[("202610", "B10")] = CellState(None, "A4C2F4")
    assert writer.read_range("202610", "B10:B10") == [CellState(None, "A4C2F4")]


def test_stale_copy_is_retried():
    writer, page = make()
    page.stale_copies = 2  # 剛切換工作表：前兩次複製到錯的內容
    assert writer.read_range("10月oven2026", "C5:C6") == [CellState(None, None)] * 2
    assert [e for e in page.log if e == ("press", "Control+C")] == [("press", "Control+C")] * 3


def test_name_box_lag_is_retried_without_copying_wrong_cells():
    writer, page = make()
    page.name_box_lag = 1
    writer.read_range("202610", "B10:B11")
    assert [e for e in page.log if e[0] == "press"] == [("press", "Control+C")]  # 名稱方塊不對時不按 Ctrl+C


def test_read_gives_up_after_timeout():
    writer, page = make()
    page.stale_copies = 10_000
    with pytest.raises(WriterError, match="無法正確讀取"):
        writer.read_range("202610", "B10:B11")
    assert page.elapsed >= 3.0


def test_multi_column_range_is_rejected():
    writer, _ = make()
    with pytest.raises(WriterError):
        writer.read_range("202610", "B10:C11")


def test_paste_uses_the_draft_and_writes_name_colour_and_fonts():
    writer, page = make()
    writer.read_range("202610", "B10:B12")
    writer.paste_booking("202610", "B10:B12", "A4C2F4", "Zoe", [LAB, LAB, CellFont(10.0, False, None)])
    assert page.cells[("202610", "B10")] == CellState("Zoe", "A4C2F4")
    assert page.cells[("202610", "B12")] == CellState(None, "A4C2F4")
    sheet, first, html = page.pasted[-1]
    assert (sheet, first) == ("202610", "B10")
    assert html.count("font-size: 12pt; font-weight: bold; text-align: center;") == 2
    assert "font-size: 10pt; font-weight: normal;" in html
    assert page.clipboard_text == "Zoe\n\n"


def test_single_cell_paste_is_one_row_table():
    writer, page = make()
    writer.read_range("202610", "B10:B10")
    writer.paste_booking("202610", "B10:B10", "A4C2F4", "Zoe", [LAB])
    assert count_rows(page.pasted[-1][2]) == 1
    assert ("202610", "B11") not in page.cells  # 多讀的那一列沒有被寫入


def test_paste_before_opening_time_is_refused():
    writer, page = make(clock_t=T0 - 0.01)
    writer.read_range("202610", "B10:B11")
    with pytest.raises(EarlyWriteError):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    assert page.pasted == []


def test_paste_without_draft_is_refused():
    writer, page = make()
    with pytest.raises(WriterError, match="底稿"):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    assert page.pasted == []


def test_paste_with_draft_of_another_range_is_refused():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    with pytest.raises(WriterError, match="底稿"):
        writer.paste_booking("202610", "E10:E11", "A4C2F4", "Zoe", [LAB, LAB])
    assert page.pasted == []


def test_draft_is_used_only_once():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    with pytest.raises(WriterError):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    assert len(page.pasted) == 1


def test_failed_read_clears_old_draft():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    page.stale_copies = 10_000
    with pytest.raises(WriterError):
        writer.read_range("202610", "B10:B11")
    with pytest.raises(WriterError, match="底稿"):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])


def test_wrong_selection_before_paste_is_refused():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    page.name_box_lag = 2  # 跳到第一格後名稱方塊仍顯示別處
    with pytest.raises(WriterError, match="選取位置錯誤"):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    assert page.pasted == []


def test_font_count_mismatch_is_refused():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    with pytest.raises(WriterError):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB])
    assert page.pasted == []


def test_prewarm_visits_each_sheet():
    writer, page = make()
    writer.prewarm(["202610", "10月oven2026"])
    assert [e for e in page.log if e[0] == "jump"] == [("jump", "'202610'!A1"), ("jump", "'10月oven2026'!A1")]


def test_recover_restarts_page_and_drops_draft():
    first, second = FakeSheetPage(), FakeSheetPage()
    writer, _ = make(first, restart=lambda: second)
    writer.read_range("202610", "B10:B11")
    writer.recover()
    with pytest.raises(WriterError, match="底稿"):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    writer.read_range("202610", "B10:B11")
    assert ("jump", "'202610'!B10:B11") in second.log


def test_page_errors_propagate_unchanged():
    writer, page = make()
    page.fail["Control+C"] = WriterCrashed("瀏覽器當掉")
    with pytest.raises(WriterCrashed):
        writer.read_range("202610", "B10:B11")


def test_read_waits_until_target_sheet_is_active():
    writer, page = make()
    page.active_lag = 2  # 前兩次跳轉後工作表尚未切換完成
    assert writer.read_range("10月oven2026", "C5:C6") == [CellState(None, None)] * 2
    assert [e for e in page.log if e[0] == "press"] == [("press", "Control+C")]  # 工作表不對時不按 Ctrl+C


def test_switching_sheet_waits_for_load_once():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    writer.read_range("10月oven2026", "C5:C6")
    assert page.elapsed == pytest.approx(0.6)  # 兩次切換各等 0.3 秒
    writer.read_range("10月oven2026", "C7:C8")
    assert page.elapsed == pytest.approx(0.6)  # 同一工作表不再等待


def test_paste_refused_when_target_sheet_not_active():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    page.active_lag = 1
    with pytest.raises(WriterError, match="選取位置錯誤"):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    assert page.pasted == []


def test_paste_refused_when_clipboard_changed_by_another_program():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    original_jump = page.jump

    def jump(ref):
        original_jump(ref)
        if ref == "'202610'!B10":  # 我們放入剪貼簿之後、貼上之前，別的程式複製了東西
            page.clipboard_html = google_html([CellState("別人", None), CellState(None, None)])
    page.jump = jump
    with pytest.raises(WriterError, match="剪貼簿"):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    assert page.pasted == []


def test_ctrl_v_failure_is_reported_as_crash():
    writer, page = make()
    writer.read_range("202610", "B10:B11")
    page.fail["Control+V"] = WriterError("操作逾時")
    with pytest.raises(WriterCrashed, match="無法確定是否已貼上"):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])


def test_refused_early_paste_drops_the_draft():
    writer, page = make(clock_t=T0 - 0.01)
    writer.read_range("202610", "B10:B11")
    with pytest.raises(EarlyWriteError):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    writer._clock.t = T0 + 1
    with pytest.raises(WriterError, match="底稿"):
        writer.paste_booking("202610", "B10:B11", "A4C2F4", "Zoe", [LAB, LAB])
    assert page.pasted == []


# ── 23:00 單格：下一列是下一週合併的日期列（實測：選 U20:U21 → 名稱方塊顯示 T20:V21）──

def test_fake_expands_selection_over_merged_row():
    page = FakeSheetPage()
    page.merged_rows["202610"] = {21}
    page.jump("'202610'!U20:U21")
    assert page.name_box() == "T20:V21"
    page.jump("'202610'!U19:U20")
    assert page.name_box() == "U19:U20"


def test_last_slot_of_day_reads_previous_row_instead():
    writer, page = make()
    page.merged_rows["202610"] = {21}
    page.cells[("202610", "U19")] = CellState("Ping", "FDE49A")  # 多讀的上一列不可影響結果
    page.cells[("202610", "U20")] = CellState(None, "A4C2F4")
    page.cells[("202610", "U21")] = CellState("2026/10/19", None)
    assert writer.read_range("202610", "U20:U20") == [CellState(None, "A4C2F4")]
    jumps = [e[1] for e in page.log if e[0] == "jump"]
    assert jumps == ["'202610'!U20:U21", "'202610'!U19:U20"]  # 被擴張後立刻改選上一列＋本格
    assert [e for e in page.log if e[0] == "press"] == [("press", "Control+C")]  # 只按一次有效的 Ctrl+C
    assert page.elapsed == pytest.approx(0.3)  # 只有切換工作表的等待，沒有等到逾時


def test_last_slot_paste_targets_only_the_cell():
    writer, page = make()
    page.merged_rows["202610"] = {21}
    writer.read_range("202610", "U20:U20")
    writer.paste_booking("202610", "U20:U20", "A4C2F4", "Zoe", [LAB])
    sheet, first, html = page.pasted[-1]
    assert (sheet, first) == ("202610", "U20")
    assert count_rows(html) == 1
    assert page.cells[("202610", "U20")] == CellState("Zoe", "A4C2F4")
    assert ("202610", "U19") not in page.cells  # 多讀的上一列沒有被寫入


def test_cell_between_two_merged_rows_is_refused():
    writer, page = make()
    page.merged_rows["202610"] = {19, 21}  # 理論上不會發生：上下兩列都被合併
    with pytest.raises(WriterError):
        writer.read_range("202610", "U20:U20")
    assert [e for e in page.log if e[0] == "press"] == []
    assert page.elapsed < 1.0  # 不等到逾時


def test_first_row_with_merged_next_row_is_refused():
    writer, page = make()
    page.merged_rows["202610"] = {2}
    with pytest.raises(WriterError):
        writer.read_range("202610", "U1:U1")
    assert [e for e in page.log if e[0] == "press"] == []
    assert page.elapsed < 1.0


def test_expanded_multi_row_range_fails_fast():
    writer, page = make()
    page.merged_rows["202610"] = {21}
    with pytest.raises(WriterError):
        writer.read_range("202610", "U19:U21")
    assert [e for e in page.log if e[0] == "press"] == []
    assert page.elapsed < 1.0
