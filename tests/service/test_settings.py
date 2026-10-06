from datetime import time

import pytest

from instrument_booking.service.settings import Settings, validate_settings

URL = "https://docs.google.com/spreadsheets/d/1621E29JjZYnJGj1lnLgJ-GiU_bCVJCdh/edit"


def ok(**kw):
    base = dict(name="Zoe", spreadsheet_url=URL)
    base.update(kw)
    return Settings(**base)


def test_defaults():
    s = Settings()
    assert (s.run_weekday, s.run_time, s.autostart, s.minimize_to_tray, s.theme) == (4, time(13, 0), True, True, "system")


def test_valid_settings_have_no_errors():
    assert validate_settings(ok()) == []


@pytest.mark.parametrize("name,msg", [
    ("", "請填寫"), ("   ", "請填寫"), (" Zoe", "前後不可有空白"), ("Zoe ", "前後不可有空白"),
    ("Z\toe", "Tab 或換行"), ("Z\noe", "Tab 或換行"),
])
def test_name_rules(name, msg):
    (error,) = validate_settings(ok(name=name))
    assert msg in error


def test_url_must_be_google_sheet():
    (error,) = validate_settings(ok(spreadsheet_url="https://example.com/x"))
    assert "Google 試算表" in error


def test_weekday_time_and_theme():
    errors = validate_settings(ok(run_weekday=7, run_time=time(13, 0, 30), theme="pink"))
    assert len(errors) == 3


def test_unconfigured_settings_report_name_and_url():
    assert len(validate_settings(Settings())) == 2
