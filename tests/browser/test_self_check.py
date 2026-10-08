from instrument_booking.browser.self_check import run_self_check


class FakePage:
    def __init__(self, log):
        self.log = log

    def goto(self, url):
        self.log.append(("goto", url))


class FakeBrowser:
    version = "141.0.3537.57"

    def __init__(self, log):
        self.log = log

    def new_page(self):
        return FakePage(self.log)

    def close(self):
        self.log.append("close")


class FakeChromium:
    def __init__(self, log, fail=None):
        self.log, self.fail = log, fail

    def launch(self, **kwargs):
        self.log.append(("launch", kwargs))
        if self.fail:
            raise self.fail
        return FakeBrowser(self.log)


class FakePlaywright:
    def __init__(self, log, fail=None):
        self.chromium = FakeChromium(log, fail)
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.log.append("stop")
        return False


def test_self_check_launches_system_edge_headless_and_closes():
    log = []
    ok, message = run_self_check(playwright_factory=lambda: FakePlaywright(log))
    assert ok and message == "Edge 自動化正常（版本 141.0.3537.57）"
    assert log == [("launch", {"channel": "msedge", "headless": True}), ("goto", "about:blank"), "close", "stop"]


def test_self_check_reports_failure_without_raising():
    log = []
    ok, message = run_self_check(playwright_factory=lambda: FakePlaywright(log, RuntimeError("找不到 msedge")))
    assert not ok and message == "無法以自動化啟動 Edge：RuntimeError：找不到 msedge"
    assert log[-1] == "stop"
