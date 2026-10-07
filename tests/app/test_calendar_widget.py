from datetime import date

from PySide6.QtCore import QPoint, Qt

from instrument_booking.app.calendar_widget import WeekCalendar, text_color_for
from instrument_booking.core.models import BookingRequest, Instrument

MON = date(2026, 10, 12)


def make(qtbot):
    cal = WeekCalendar()
    qtbot.addWidget(cal)
    cal.resize(56 + 7 * 100, 44 + 15 * 30)
    cal.set_week(MON)
    return cal


def point(day, hour):
    return QPoint(56 + day * 100 + 50, 44 + (hour - 9) * 30 + 15)


def test_drag_down_emits_range_including_both_cells(qtbot):
    cal = make(qtbot)
    with qtbot.waitSignal(cal.rangeSelected) as blocker:
        qtbot.mousePress(cal, Qt.MouseButton.LeftButton, pos=point(2, 10))
        qtbot.mouseMove(cal, point(2, 12))
        qtbot.mouseRelease(cal, Qt.MouseButton.LeftButton, pos=point(2, 12))
    assert blocker.args == [2, 10, 13]


def test_drag_upwards_and_single_click(qtbot):
    cal = make(qtbot)
    with qtbot.waitSignal(cal.rangeSelected) as blocker:
        qtbot.mousePress(cal, Qt.MouseButton.LeftButton, pos=point(0, 15))
        qtbot.mouseMove(cal, point(0, 13))
        qtbot.mouseRelease(cal, Qt.MouseButton.LeftButton, pos=point(0, 13))
    assert blocker.args == [0, 13, 16]
    with qtbot.waitSignal(cal.rangeSelected) as blocker:
        qtbot.mouseClick(cal, Qt.MouseButton.LeftButton, pos=point(6, 23))
    assert blocker.args == [6, 23, 24]


def test_click_on_block_emits_block_clicked_instead_of_range(qtbot):
    cal = make(qtbot)
    r = BookingRequest("r1", Instrument.TUBE_A, date(2026, 10, 13), 9, 12, "Ar", "A4C2F4")
    cal.set_bookings([r], {"r1": 1})
    with qtbot.assertNotEmitted(cal.rangeSelected):
        with qtbot.waitSignal(cal.blockClicked) as blocker:
            qtbot.mouseClick(cal, Qt.MouseButton.LeftButton, pos=point(1, 10))
    assert blocker.args[0] == "r1"


def test_disabled_calendar_ignores_mouse(qtbot):
    cal = make(qtbot)
    cal.setEnabled(False)
    with qtbot.assertNotEmitted(cal.rangeSelected):
        qtbot.mouseClick(cal, Qt.MouseButton.LeftButton, pos=point(1, 10))


def test_paint_with_everything_does_not_crash(qtbot):
    cal = make(qtbot)
    reqs = [BookingRequest("a", Instrument.TUBE_A, MON, 9, 12, "Ar/H2", "DD7E6B"),
            BookingRequest("b", Instrument.TUBE_A, MON, 10, 11, "Ar", "A4C2F4")]
    cal.set_bookings(reqs, {"a": 1, "b": 2})
    cal.set_occupancy({(0, 13), (3, 9)}, {6})
    assert not cal.grab().isNull()
    assert [b.request.id for b in cal.blocks()] == ["a", "b"]
    assert cal.blocks()[1].rect.left() > cal.blocks()[0].rect.left()  # 重疊時並排


def test_text_color_for_light_and_dark_backgrounds():
    assert text_color_for("FFE599").name() == "#000000"
    assert text_color_for("1F3864").name() == "#ffffff"
