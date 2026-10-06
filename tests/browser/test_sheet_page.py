import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from instrument_booking.browser.sheet_page import (
    ACTIVE_SHEET_TAB,
    CLIPBOARD_TIMEOUT_MARK,
    CLIPBOARD_TIMEOUT_MS,
    NAME_BOX,
    OP_TIMEOUT_MS,
    READ_HTML_JS,
    WRITE_JS,
    PlaywrightSheetPage,
)
from instrument_booking.core.job import WriterCrashed, WriterError


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def click(self):
        self.page.calls.append("click")

    def fill(self, text):
        self.page.calls.append(("fill", text))

    def press(self, key):
        self.page.calls.append(("box-press", key))

    def input_value(self):
        return "B10:B13"

    @property
    def first(self):
        return self

    def inner_text(self):
        return " 202610 " if self.selector == ACTIVE_SHEET_TAB else ""


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, keys):
        if self.page.raise_on_key:
            raise self.page.raise_on_key
        self.page.calls.append(("key", keys))


class FakeRawPage:
    def __init__(self):
        self.calls = []
        self.raise_on_key = None
        self.raise_on_evaluate = None
        self.default_timeout = None
        self.keyboard = FakeKeyboard(self)

    def set_default_timeout(self, ms):
        self.default_timeout = ms

    def locator(self, selector):
        assert selector in (NAME_BOX, ACTIVE_SHEET_TAB)
        return FakeLocator(self, selector)

    def evaluate(self, js, arg=None):
        if self.raise_on_evaluate:
            raise self.raise_on_evaluate
        self.calls.append(("evaluate", js, arg))
        return "<table></table>"

    def wait_for_timeout(self, ms):
        self.calls.append(("wait", ms))


def test_jump_types_into_name_box():
    raw = FakeRawPage()
    PlaywrightSheetPage(raw).jump("'202610'!B10:B13")
    assert raw.calls == ["click", ("fill", "'202610'!B10:B13"), ("box-press", "Enter")]


def test_name_box_and_keys_and_wait():
    raw = FakeRawPage()
    page = PlaywrightSheetPage(raw)
    assert page.name_box() == "B10:B13"
    page.press("Control+C")
    page.wait(30)
    assert raw.calls == [("key", "Control+C"), ("wait", 30)]


def test_clipboard_read_and_write():
    raw = FakeRawPage()
    page = PlaywrightSheetPage(raw)
    assert page.read_clipboard_html() == "<table></table>"
    page.write_clipboard(None, "")
    page.write_clipboard("<table/>", "Zoe")
    assert raw.calls == [("evaluate", READ_HTML_JS, None), ("evaluate", WRITE_JS, ["", ""]),
                         ("evaluate", WRITE_JS, ["<table/>", "Zoe"])]


def test_timeout_becomes_writer_error():
    raw = FakeRawPage()
    raw.raise_on_key = PlaywrightTimeoutError("Timeout 30000ms exceeded")
    with pytest.raises(WriterError, match="逾時"):
        PlaywrightSheetPage(raw).press("Control+C")


def test_other_playwright_errors_become_writer_crashed():
    raw = FakeRawPage()
    raw.raise_on_key = PlaywrightError("Target page, context or browser has been closed")
    with pytest.raises(WriterCrashed, match="瀏覽器異常"):
        PlaywrightSheetPage(raw).press("Control+C")


def test_active_sheet_reads_tab_name():
    assert PlaywrightSheetPage(FakeRawPage()).active_sheet() == "202610"


def test_every_playwright_call_has_a_time_limit():
    raw = FakeRawPage()
    PlaywrightSheetPage(raw)
    assert OP_TIMEOUT_MS == 3000
    assert raw.default_timeout == OP_TIMEOUT_MS


@pytest.mark.parametrize("js", [READ_HTML_JS, WRITE_JS], ids=["read", "write"])
def test_clipboard_scripts_race_against_a_timeout(js):
    assert CLIPBOARD_TIMEOUT_MS == 2000
    assert "Promise.race" in js
    assert CLIPBOARD_TIMEOUT_MARK in js
    assert str(CLIPBOARD_TIMEOUT_MS) in js


@pytest.mark.parametrize("call", [
    lambda page: page.read_clipboard_html(),
    lambda page: page.write_clipboard("<table/>", "Zoe"),
])
def test_clipboard_timeout_becomes_writer_error(call):
    raw = FakeRawPage()
    raw.raise_on_evaluate = PlaywrightError(f"Error: {CLIPBOARD_TIMEOUT_MARK}\n    at <anonymous>:4:46")
    with pytest.raises(WriterError, match="剪貼簿操作逾時") as info:
        call(PlaywrightSheetPage(raw))
    assert not isinstance(info.value, WriterCrashed)


def test_other_evaluate_errors_become_writer_crashed():
    raw = FakeRawPage()
    raw.raise_on_evaluate = PlaywrightError("Execution context was destroyed")
    with pytest.raises(WriterCrashed):
        PlaywrightSheetPage(raw).read_clipboard_html()
