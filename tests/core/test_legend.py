from datetime import date

from instrument_booking.core.legend import lookup_gas, oven_color, read_tube_legend, resolve_color
from instrument_booking.core.models import BookingRequest, Instrument
from sheet_builder import DEFAULT_LEGEND, add_oven_sheet, add_tube_sheet, new_workbook, solid

MON = date(2026, 10, 12)


def tube_ws(**kw):
    ws, _ = add_tube_sheet(new_workbook(), "202610", MON, **kw)
    return ws


def test_read_tube_legend():
    assert read_tube_legend(tube_ws()) == dict(DEFAULT_LEGEND)


def test_legend_stops_at_uncolored_cell():
    # 2021 年的表：圖例後面接著無底色的人名
    ws = tube_ws(legend=[("H2", "F4CCCC"), ("Ar", "A4C2F4"), ("RP", None), ("Ray", None)])
    assert read_tube_legend(ws) == {"H2": "F4CCCC", "Ar": "A4C2F4"}


def test_lookup_gas_ignores_spaces_and_case():
    legend = dict(DEFAULT_LEGEND)
    assert lookup_gas(legend, " ar/h2 ") == "DD7E6B"
    assert lookup_gas(legend, "Ar / H2") == "DD7E6B"
    assert lookup_gas(legend, "He") is None


def test_resolve_color_from_target_legend():
    ws = tube_ws(legend=[("Ar", "123456")])  # 目標表的圖例優先於選擇時的顏色
    r = BookingRequest("r", Instrument.TUBE_A, MON, 9, 10, "Ar", "A4C2F4")
    assert resolve_color(ws, r) == ("123456", None)


def test_resolve_color_falls_back_with_warning():
    r = BookingRequest("r", Instrument.TUBE_A, MON, 9, 10, "Ar/H2", "DD7E6B")
    color, warning = resolve_color(tube_ws(legend=[("Ar", "A4C2F4")]), r)
    assert color == "DD7E6B"
    assert "Ar/H2" in warning and "DD7E6B" in warning


def test_oven_color_from_header_and_default():
    ws, _ = add_oven_sheet(new_workbook(), "10月oven2026", MON)
    assert oven_color(ws, 0) == "FFE599"
    assert oven_color(ws, 1) == "A4C2F4"
    ws2, _ = add_oven_sheet(new_workbook(), "7月oven2024", MON, header=False)
    assert oven_color(ws2, 0) == "FFE599"
    ws["B1"].fill = solid("FFF2CC")  # 表上的顏色優先
    assert oven_color(ws, 0) == "FFF2CC"


def test_resolve_color_oven():
    ws, _ = add_oven_sheet(new_workbook(), "10月oven2026", MON)
    r = BookingRequest("r", Instrument.OVEN_B, MON, 9, 10, None, "A4C2F4")
    assert resolve_color(ws, r) == ("A4C2F4", None)
