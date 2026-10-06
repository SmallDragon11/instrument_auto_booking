from datetime import date

import pytest

from instrument_booking.core.models import (
    BookingRequest,
    CellFont,
    CellState,
    Instrument,
    ItemResult,
    ItemStatus,
)


def make(**kw):
    base = dict(id="r1", instrument=Instrument.TUBE_A, date=date(2026, 10, 12),
                start_hour=13, end_hour=17, gas="Ar", color="A4C2F4")
    base.update(kw)
    return BookingRequest(**base)


def test_instrument_metadata():
    assert Instrument.TUBE_C.kind == "tube"
    assert Instrument.TUBE_C.sub_index == 2
    assert Instrument.TUBE_C.label == "C-小房間"
    assert Instrument.OVEN_B.kind == "oven"
    assert Instrument.OVEN_B.sub_index == 1
    assert Instrument.OVEN_B.label == "ovenB"


def test_hours_range():
    assert list(make().hours) == [13, 14, 15, 16]


@pytest.mark.parametrize("start,end", [(8, 10), (13, 13), (15, 14), (23, 25)])
def test_invalid_hours_rejected(start, end):
    with pytest.raises(ValueError):
        make(start_hour=start, end_hour=end)


def test_last_slot_allowed():
    assert list(make(start_hour=23, end_hour=24).hours) == [23]


def test_tube_requires_gas():
    with pytest.raises(ValueError):
        make(gas=None)


def test_oven_rejects_gas():
    with pytest.raises(ValueError):
        make(instrument=Instrument.OVEN_A, gas="Ar")
    make(instrument=Instrument.OVEN_A, gas=None, color="FFE599")


@pytest.mark.parametrize("color", ["a4c2f4", "#A4C2F4", "FFA4C2F4", ""])
def test_color_must_be_6_uppercase_hex(color):
    with pytest.raises(ValueError):
        make(color=color)


def test_overlaps():
    a = make(start_hour=13, end_hour=17)
    assert a.overlaps(make(id="r2", start_hour=16, end_hour=18))
    assert not a.overlaps(make(id="r2", start_hour=17, end_hour=19))  # 相鄰不算重疊
    assert not a.overlaps(make(id="r2", instrument=Instrument.TUBE_B))
    assert not a.overlaps(make(id="r2", date=date(2026, 10, 13)))


def test_cell_state_font_and_item_result_defaults():
    assert CellState(None, None) == CellState(value=None, color=None)
    assert CellFont(12.0, True, "center") == CellFont(size=12.0, bold=True, h_align="center")
    r = ItemResult("r1", ItemStatus.SUCCESS)
    assert r.reason is None and r.warning is None and r.written_at is None
