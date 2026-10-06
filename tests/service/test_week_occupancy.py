from datetime import date, timedelta

from instrument_booking.core.models import Instrument
from instrument_booking.core.sheet_locator import SheetIndex
import pytest

from instrument_booking.service.housekeeping import describe_error
from instrument_booking.service.occupancy import OccupancyService, legend_for_week, week_view
from instrument_booking.service.settings import NotConfigured, Settings
from instrument_booking.storage.json_store import JsonStore
from service.fakes import FakeSession, InlineWorker, XlsxRequest
from sheet_builder import add_oven_sheet, add_tube_sheet, new_workbook, solid

MON = date(2026, 10, 12)
URL = "https://docs.google.com/spreadsheets/d/FILEID/edit"


def build():
    wb = new_workbook()
    ws, layout = add_tube_sheet(wb, "202610", MON, legend=[("Ar", "A4C2F4"), ("N2", "D5A6BD")])
    oven, oven_layout = add_oven_sheet(wb, "10月oven2026", MON)
    return wb, ws, layout, oven, oven_layout


def test_week_view_marks_value_and_colour_cells():
    wb, ws, layout, oven, oven_layout = build()
    date_row, col, hours = layout[MON + timedelta(days=1)]  # 週二
    ws.cell(hours[13], col + 1, "Ping")                     # B-窗 13 點有字
    ws.cell(hours[14], col + 1).fill = solid("A4C2F4")       # 14 點只有底色
    o_row, o_col, o_hours = oven_layout[MON]
    oven.cell(o_hours[9], o_col).fill = solid("FFE599")       # ovenA 週一 9 點
    view = week_view(wb, SheetIndex.build(wb), MON)
    tue = MON + timedelta(days=1)
    assert view.occupied == {(Instrument.TUBE_B, tue, 13), (Instrument.TUBE_B, tue, 14), (Instrument.OVEN_A, MON, 9)}
    assert view.unavailable == frozenset()
    assert view.legend == {"Ar": "A4C2F4", "N2": "D5A6BD"}


def test_missing_sheet_days_are_unavailable():
    wb, *_ = build()
    next_mon = MON + timedelta(days=7)
    view = week_view(wb, SheetIndex.build(wb), next_mon)
    assert len(view.unavailable) == 14  # 管型爐＋烘箱各 7 天
    assert view.occupied == frozenset()
    assert view.legend == {"Ar": "A4C2F4", "N2": "D5A6BD"}  # 目標週的表還沒建立 → 用最新的管型爐表


def test_legend_prefers_target_week_sheet():
    wb = new_workbook()
    add_tube_sheet(wb, "202609", MON - timedelta(days=14), legend=[("H2", "F4CCCC")])
    add_tube_sheet(wb, "202610", MON, legend=[("Ar", "A4C2F4")])
    index = SheetIndex.build(wb)
    assert legend_for_week(wb, index, MON - timedelta(days=14)) == {"H2": "F4CCCC"}
    assert legend_for_week(wb, index, MON + timedelta(days=21)) == {"Ar": "A4C2F4"}  # 最新的表


def test_legend_empty_without_tube_sheets():
    wb = new_workbook()
    add_oven_sheet(wb, "10月oven2026", MON)
    assert legend_for_week(wb, SheetIndex.build(wb), MON) == {}


def test_service_downloads_computes_caches_legend_and_closes_browser(tmp_path):
    wb, *_ = build()
    request = XlsxRequest(wb)
    worker = InlineWorker(FakeSession([], request=request))
    store = JsonStore(tmp_path / "data")
    store.save_settings(Settings(name="Zoe", spreadsheet_url=URL))
    service = OccupancyService(worker, snapshot_dir=tmp_path / "snap", store=store)
    view = service.refresh(MON)
    assert request.urls == ["https://docs.google.com/spreadsheets/d/FILEID/export?format=xlsx"]
    assert view.legend == {"Ar": "A4C2F4", "N2": "D5A6BD"}
    assert store.load_legend() == view.legend
    assert worker.jobs == [False]  # 下載完就關閉瀏覽器


@pytest.mark.parametrize("settings", [Settings(), Settings(name="Zoe", spreadsheet_url="https://example.com/x")],
                         ids=["empty", "bad_url"])
def test_refresh_without_valid_settings_raises_not_configured(tmp_path, settings):
    worker = InlineWorker(FakeSession([]))
    store = JsonStore(tmp_path / "data")
    store.save_settings(settings)
    service = OccupancyService(worker, snapshot_dir=tmp_path / "snap", store=store)
    with pytest.raises(NotConfigured) as info:
        service.refresh(MON)
    assert describe_error(info.value).startswith("請先完成設定：")
    assert "預約表網址必須是 Google 試算表網址" in describe_error(info.value)
    assert worker.jobs == []  # 不開瀏覽器
