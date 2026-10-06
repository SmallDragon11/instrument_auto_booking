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
                     error=error, requests=(TUBE, OVEN), results=results)


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


def test_corrupted_legend_raises_store_error(tmp_path):
    (tmp_path / "legend.json").write_text('["不是物件"]', encoding="utf-8")
    with pytest.raises(StoreError, match="legend.json"):
        JsonStore(tmp_path).load_legend()


def test_run_error_field_is_named_error_and_old_name_still_loads(tmp_path):
    store = JsonStore(tmp_path)
    store.append_run(record(datetime(2026, 10, 9, 13, 0, 1, tzinfo=TAIPEI), error="預檢失敗"))
    (path,) = (tmp_path / "runs").glob("*.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["error"] == "預檢失敗" and "preflight_error" not in data
    data["preflight_error"] = data.pop("error")  # 舊版紀錄檔
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert JsonStore(tmp_path).load_runs()[0].error == "預檢失敗"


SAVED_AT = datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI)


def test_schedule_since_recorded_on_first_save(tmp_path):
    store = JsonStore(tmp_path)
    assert store.schedule_since() is None
    store.save_settings(Settings(name="Zoe"), now=SAVED_AT)
    assert JsonStore(tmp_path).schedule_since() == SAVED_AT
    assert JsonStore(tmp_path).load_settings() == Settings(name="Zoe")  # 不屬於 Settings


def test_schedule_since_changes_only_when_weekday_or_time_changes(tmp_path):
    store = JsonStore(tmp_path)
    store.save_settings(Settings(name="Zoe"), now=SAVED_AT)
    later = datetime(2026, 10, 8, 9, 0, tzinfo=TAIPEI)
    store.save_settings(Settings(name="小融", theme="dark"), now=later)  # 星期與時間不變
    assert store.schedule_since() == SAVED_AT
    store.save_settings(Settings(name="小融", run_weekday=0), now=later)
    assert store.schedule_since() == later
    even_later = datetime(2026, 10, 9, 9, 0, tzinfo=TAIPEI)
    store.save_settings(Settings(name="小融", run_weekday=0, run_time=time(12, 30)), now=even_later)
    assert store.schedule_since() == even_later


def test_schedule_since_defaults_to_now_in_taipei(tmp_path):
    store = JsonStore(tmp_path)
    before = datetime.now(TAIPEI)
    store.save_settings(Settings(name="Zoe"))
    since = store.schedule_since()
    assert since.tzinfo is not None and before <= since <= datetime.now(TAIPEI)


def test_corrupted_settings_file_counts_as_schedule_change(tmp_path):
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    store = JsonStore(tmp_path)
    store.save_settings(Settings(name="Zoe"), now=SAVED_AT)
    assert store.schedule_since() == SAVED_AT


def test_corrupted_schedule_since_raises_store_error(tmp_path):
    (tmp_path / "settings.json").write_text('{"name": "Zoe", "schedule_since": "昨天"}', encoding="utf-8")
    with pytest.raises(StoreError, match="settings.json"):
        JsonStore(tmp_path).schedule_since()
