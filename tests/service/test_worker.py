import threading

import pytest

from instrument_booking.browser.session import NotLoggedIn
from instrument_booking.service.worker import BrowserWorker
from service.fakes import FakeSession


@pytest.fixture
def setup():
    log = []
    sessions = []

    def factory():
        s = FakeSession(log)
        sessions.append(s)
        return s
    worker = BrowserWorker(factory)
    yield worker, log, sessions
    worker.shutdown()


def test_all_browser_work_runs_on_one_dedicated_thread(setup):
    worker, log, _ = setup
    ids = [worker.submit(lambda s: threading.get_ident()).result() for _ in range(3)]
    assert len(set(ids)) == 1 and ids[0] != threading.get_ident()
    assert {tid for _, tid in log} == {ids[0]}  # start／close 也在同一條執行緒


def test_session_starts_lazily_and_closes_after_job_by_default(setup):
    worker, log, sessions = setup
    assert log == []
    assert worker.submit(lambda s: "ok").result() == "ok"
    assert [e for e, _ in log] == ["start", "close"]


def test_keep_open_reuses_session_until_a_closing_job(setup):
    worker, log, sessions = setup
    worker.submit(lambda s: s, keep_open=True).result()
    worker.submit(lambda s: s, keep_open=True).result()
    worker.submit(lambda s: s).result()
    assert [e for e, _ in log] == ["start", "close"]
    assert len(sessions) == 1


def test_failed_keep_open_job_leaves_session_for_next_job(setup):
    worker, log, _ = setup

    def boom(s):
        raise RuntimeError("網頁錯誤")
    with pytest.raises(RuntimeError):
        worker.submit(boom, keep_open=True).result()
    worker.submit(lambda s: None).result()
    assert [e for e, _ in log] == ["start", "close"]  # keep_open 的工作失敗時不關閉，下一個工作沿用


def test_failed_job_without_keep_open_closes_browser(setup):
    worker, log, _ = setup

    def boom(s):
        raise RuntimeError("下載失敗")
    with pytest.raises(RuntimeError):
        worker.submit(boom).result()
    assert [e for e, _ in log] == ["start", "close"]  # 失敗也不會留下開著的 Edge


def test_start_failure_propagates_and_next_job_retries():
    log = []
    attempts = []

    def factory():
        attempts.append(1)
        return FakeSession(log, fail_start=NotLoggedIn("x") if len(attempts) == 1 else None)
    worker = BrowserWorker(factory)
    try:
        with pytest.raises(NotLoggedIn):
            worker.submit(lambda s: None).result()
        assert worker.submit(lambda s: "ok").result() == "ok"
        assert len(attempts) == 2
    finally:
        worker.shutdown()


def test_close_session_runs_after_queued_jobs(setup):
    worker, log, _ = setup
    worker.submit(lambda s: None, keep_open=True)
    worker.close_session().result()
    assert [e for e, _ in log] == ["start", "close"]


def test_hold_keeps_session_open_after_closing_jobs_until_released(setup):
    worker, log, sessions = setup
    worker.submit(lambda s: None, keep_open=True, hold=True).result()  # 預檢：保留待命的 Edge
    worker.submit(lambda s: None).result()                              # 例如佔用重新整理、連線測試
    assert [e for e, _ in log] == ["start"]  # 保留中：一般工作結束後不關閉
    worker.submit(lambda s: None, hold=False).result()                  # 寫入：解除保留，結束後關閉
    assert [e for e, _ in log] == ["start", "close"]
    assert len(sessions) == 1


def test_failed_job_while_held_does_not_close(setup):
    worker, log, _ = setup
    worker.submit(lambda s: None, keep_open=True, hold=True).result()

    def boom(s):
        raise RuntimeError("下載失敗")
    with pytest.raises(RuntimeError):
        worker.submit(boom).result()
    assert [e for e, _ in log] == ["start"]


def test_close_session_closes_and_releases_hold(setup):
    worker, log, _ = setup
    worker.submit(lambda s: None, keep_open=True, hold=True).result()
    worker.close_session().result()  # 使用者明確要求（例如重新登入）一律關閉
    assert [e for e, _ in log] == ["start", "close"]
    worker.submit(lambda s: None).result()
    assert [e for e, _ in log] == ["start", "close", "start", "close"]  # 已解除保留
