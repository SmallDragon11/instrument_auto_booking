import uuid

from instrument_booking.app.single_instance import SingleInstance


def test_second_instance_activates_first_and_exits(qtbot):
    key = f"InstrumentBooking.test.{uuid.uuid4().hex}"
    first = SingleInstance(key)
    assert first.acquire()
    with qtbot.waitSignal(first.activated, timeout=3000):
        assert not SingleInstance(key).acquire()
    first.release()
    third = SingleInstance(key)
    assert third.acquire()  # 第一個結束後可以再啟動
    third.release()


def test_losing_a_simultaneous_start_hands_off_instead_of_crashing(qtbot, monkeypatch):
    # 測試當 listen 失敗時，會重新嘗試探查並連接到現有實例
    key = f"InstrumentBooking.test.{uuid.uuid4().hex}"
    winner = SingleInstance(key)
    assert winner.acquire()
    loser = SingleInstance(key)

    # 模擬 listen 失敗：第一次建立伺服器時 listen 會失敗
    from PySide6.QtNetwork import QLocalServer
    original_listen = QLocalServer.listen
    listen_should_fail = [True]

    def mock_listen(self, name):
        if listen_should_fail[0] and self is loser._server:
            listen_should_fail[0] = False
            return False
        return original_listen(self, name)

    monkeypatch.setattr(QLocalServer, "listen", mock_listen)

    with qtbot.waitSignal(winner.activated, timeout=3000):
        assert loser.acquire() is False
    winner.release()
