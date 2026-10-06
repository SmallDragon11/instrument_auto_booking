import json
from datetime import date, datetime, time

import pytest

from instrument_booking.core.models import TAIPEI, BookingRequest, Instrument, ItemResult, ItemStatus
from instrument_booking.service.settings import Settings
from instrument_booking.storage.json_store import JsonStore, RunRecord, StoreError

MON = date(2026, 10, 12)
TUBE = BookingRequest("a", Instrument.TUBE_A, MON, 13, 17, "Ar", "A4C2F4")
OVEN = BookingRequest("b", Instrument.OVEN_B, date(2026, 10, 13), 9, 12, None, "A4C2F4")


def record(started, monday=MON, error=None):
    results = () if error else (
        ItemResult("a", ItemStatus.SUCCESS, written_at=datetime(2026, 10, 9, 13, 0, 0, 400_000, tzinfo=TAIPEI)),
        ItemResult("b", ItemStatus.LIVE_CONFLICT, reason="C5 已有「Ping」"),
    )
    return RunRecord(target_monday=monday, started_at=started, late=False, clock_source="NTP", clock_diff=0.61,
                     preflight_error=error, requests=(TUBE, OVEN), results=results)


def test_settings_default_when_missing(tmp_path):
    assert JsonStore(tmp_path).load_settings() == Settings()


def test_settings_round_trip(tmp_path):
    store = JsonStore(tmp_path)
    s = Settings(name="Zoe", spreadsheet_url="https://docs.google.com/spreadsheets/d/X/edit",
                 run_weekday=3, run_time=time(12, 30), autostart=False, minimize_to_tray=False, theme="dark")
    store.save_settings(s)
    assert JsonStore(tmp_path).load_settings() == s
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["run_time"] == "12:30"


def test_settings_ignore_unknown_keys(tmp_path):
    (tmp_path / "settings.json").write_text('{"name": "Zoe", "future_option": 1}', encoding="utf-8")
    assert JsonStore(tmp_path).load_settings().name == "Zoe"


def test_bookings_round_trip_keep_priority_order(tmp_path):
    store = JsonStore(tmp_path)
    assert store.load_bookings(MON) == []
    store.save_bookings(MON, [OVEN, TUBE])
    assert JsonStore(tmp_path).load_bookings(MON) == [OVEN, TUBE]
    assert (tmp_path / "bookings" / "2026-10-12.json").exists()


def test_runs_newest_first_and_executed_weeks(tmp_path):
    store = JsonStore(tmp_path)
    assert store.load_runs() == [] and store.executed_weeks() == set()
    older = record(datetime(2026, 10, 2, 13, 0, 1, tzinfo=TAIPEI), monday=date(2026, 10, 5))
    newer = record(datetime(2026, 10, 9, 13, 0, 1, tzinfo=TAIPEI), error="需要重新登入 Google")
    store.append_run(older)
    store.append_run(newer)
    assert JsonStore(tmp_path).load_runs() == [newer, older]
    assert store.executed_weeks() == {date(2026, 10, 5), MON}


def test_legend_cache(tmp_path):
    store = JsonStore(tmp_path)
    assert store.load_legend() == {}
    store.save_legend({"Ar": "A4C2F4", "H2": "F4CCCC"})
    assert JsonStore(tmp_path).load_legend() == {"Ar": "A4C2F4", "H2": "F4CCCC"}


def test_chinese_is_stored_readably(tmp_path):
    JsonStore(tmp_path).save_settings(Settings(name="小融"))
    assert "小融" in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_write_leaves_no_temp_file(tmp_path):
    JsonStore(tmp_path).save_bookings(MON, [TUBE])
    assert not list(tmp_path.rglob("*.tmp"))


def test_corrupted_file_raises_store_error(tmp_path):
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError, match="settings.json"):
        JsonStore(tmp_path).load_settings()


def test_corrupted_booking_raises_store_error(tmp_path):
    (tmp_path / "bookings").mkdir()
    (tmp_path / "bookings" / "2026-10-12.json").write_text('{"requests": [{"id": "a"}]}', encoding="utf-8")
    with pytest.raises(StoreError, match="損毀"):
        JsonStore(tmp_path).load_bookings(MON)
