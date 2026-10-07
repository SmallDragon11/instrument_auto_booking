from instrument_booking.app.calendar_geometry import CalendarGeometry

GEO = CalendarGeometry(width=56 + 7 * 100, height=44 + 15 * 30)


def test_cell_at_maps_points_to_day_and_hour():
    assert GEO.cell_at(56 + 5, 44 + 5) == (0, 9)
    assert GEO.cell_at(56 + 6 * 100 + 99, 44 + 14 * 30 + 29) == (6, 23)
    assert GEO.cell_at(56 + 250, 44 + 61) == (2, 11)


def test_cell_at_outside_grid_is_none():
    assert GEO.cell_at(10, 100) is None        # 時間標籤欄
    assert GEO.cell_at(100, 20) is None        # 日期列
    assert GEO.cell_at(56 + 700, 100) is None  # 右邊界外
    assert GEO.cell_at(100, 44 + 450) is None  # 下邊界外
    assert CalendarGeometry(0, 0).cell_at(100, 100) is None


def test_hour_at_clamps_while_dragging():
    assert GEO.hour_at(0) == 9
    assert GEO.hour_at(44 + 95) == 12
    assert GEO.hour_at(10_000) == 23


def test_block_rect_splits_day_into_lanes_with_gap():
    assert GEO.block_rect(1, 9, 12) == (56 + 100 + 2, 44 + 2, 96, 86)
    assert GEO.block_rect(0, 10, 11, lane=1, lanes=2) == (56 + 50 + 2, 44 + 30 + 2, 46, 26)
    assert GEO.cell_rect(2, 23) == (56 + 200, 44 + 14 * 30, 100, 30)
