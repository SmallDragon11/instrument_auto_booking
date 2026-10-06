from pathlib import Path

import pytest

from instrument_booking.core.clipboard_html import (
    ClipboardFormatError,
    build_booking_html,
    count_rows,
    drop_last_row,
    parse_cells,
    parse_rgb,
)
from instrument_booking.core.models import CellFont, CellState

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "clipboard"
LAB_FONT = CellFont(12.0, True, "center")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("css,expected", [
    ("rgb(164, 194, 244)", "A4C2F4"),
    ("rgb(255,229,153)", "FFE599"),
    ("rgb(255, 255, 255)", None),
    ("rgba(0, 0, 0, 0)", None),
    ("rgba(164, 194, 244, 1)", "A4C2F4"),
    ("transparent", None),
    (None, None),
    ("", None),
    ("#a4c2f4", "unknown"),
    ("hsl(0, 0%, 50%)", "unknown"),
])
def test_parse_rgb(css, expected):
    assert parse_rgb(css) == expected


def test_parse_booked_range():
    cells = parse_cells(fixture("booked_5rows.html"))
    assert cells == [CellState("X", "A4C2F4")] + [CellState(None, "A4C2F4")] * 4


def test_parse_empty_range():
    assert parse_cells(fixture("empty_3rows.html")) == [CellState(None, None)] * 3


def test_single_cell_span_is_rejected():
    with pytest.raises(ClipboardFormatError):
        parse_cells(fixture("single_cell_span.html"))


def test_count_rows():
    assert count_rows(fixture("booked_5rows.html")) == 5
    assert count_rows(fixture("single_cell_span.html")) == 0
    assert count_rows(None) == 0
    assert count_rows("") == 0


def test_parse_unescapes_text_and_strips_tags():
    html = ('<table><tbody><tr><td style="background-color: rgb(255, 255, 255);">'
            'A&amp;B<br>c</td></tr></tbody></table>')
    assert parse_cells(html) == [CellState("A&Bc", None)]


def test_missing_background_is_empty():
    html = '<table><tbody><tr><td style="padding: 0px 3px;"></td></tr></tbody></table>'
    assert parse_cells(html) == [CellState(None, None)]


def test_drop_last_row():
    html = drop_last_row(fixture("empty_3rows.html"))
    assert count_rows(html) == 2
    assert html.endswith("</tbody></table></google-sheets-html-origin>")


def test_drop_last_row_needs_two_rows():
    one = drop_last_row(drop_last_row(fixture("empty_3rows.html")))
    with pytest.raises(ClipboardFormatError):
        drop_last_row(one)


def test_build_booking_html_rewrites_colour_name_and_fonts():
    src = fixture("empty_3rows.html")
    out = build_booking_html(src, color="A4C2F4", name="Zoe", fonts=[LAB_FONT, LAB_FONT, CellFont(10.0, False, None)])
    assert parse_cells(out) == [CellState("Zoe", "A4C2F4"), CellState(None, "A4C2F4"), CellState(None, "A4C2F4")]
    assert out.count("font-size: 12pt; font-weight: bold; text-align: center;") == 2
    assert "font-size: 10pt; font-weight: normal;" in out
    assert "rgb(255, 255, 255)" not in out


def test_build_booking_html_keeps_borders_and_wrapper():
    src = fixture("empty_3rows.html")
    out = build_booking_html(src, color="FFE599", name="Zoe", fonts=[LAB_FONT] * 3)
    assert out.startswith(src[:src.index("<tbody>")])  # 外層與表格屬性不變
    for border in ("border-width: 2px 2px 1px 1px;", "border-width: 1px 2px 1px 1px;"):
        assert border in out
    assert out.count("padding: 0px 3px;") == 3


def test_build_booking_html_replaces_existing_name_and_font():
    out = build_booking_html(fixture("booked_5rows.html"), color="F4CCCC", name="Zoe", fonts=[LAB_FONT] * 5)
    assert parse_cells(out)[0] == CellState("Zoe", "F4CCCC")
    assert ">X<" not in out
    assert out.count("font-size: 12pt") == 5  # 每格恰好一組字型宣告（原第一格的舊宣告已移除）
    assert out.count("font-weight: bold") == 5


def test_build_booking_html_escapes_name():
    out = build_booking_html(fixture("empty_3rows.html"), color="A4C2F4", name="<b>A&B</b>", fonts=[LAB_FONT] * 3)
    assert parse_cells(out)[0].value == "<b>A&B</b>"
    assert "<b>" not in out


def test_build_booking_html_row_count_must_match_fonts():
    with pytest.raises(ClipboardFormatError):
        build_booking_html(fixture("empty_3rows.html"), color="A4C2F4", name="Zoe", fonts=[LAB_FONT] * 2)


def test_build_booking_html_rejects_span():
    with pytest.raises(ClipboardFormatError):
        build_booking_html(fixture("single_cell_span.html"), color="A4C2F4", name="Zoe", fonts=[LAB_FONT])
