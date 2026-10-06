import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from instrument_booking.browser.sheet_page import ACTIVE_SHEET_TAB, NAME_BOX, READ_HTML_JS, WRITE_JS, PlaywrightSheetPage
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
        self.keyboard = FakeKeyboard(self)

    def locator(self, selector):
        assert selector in (NAME_BOX, ACTIVE_SHEET_TAB)
        return FakeLocator(self, selector)

    def evaluate(self, js, arg=None):
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
