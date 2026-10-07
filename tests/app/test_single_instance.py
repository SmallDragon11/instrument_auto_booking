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


def test_lock_keeps_single_instance_when_probe_misses(qtbot, monkeypatch):
    # 即使第一次探查失敗（對方剛啟動、還沒監聽），檔案鎖也保證只有一個實例成功啟動
    key = f"InstrumentBooking.test.{uuid.uuid4().hex}"
    winner = SingleInstance(key)
    assert winner.acquire()
    loser = SingleInstance(key)

    # 模擬探查失敗：只讓後起者的第一次探查失敗
    real = SingleInstance._notify_existing
    first_call = [True]

    def failing_then_real(self):
        if first_call[0]:
            first_call[0] = False
            return False
        return real(self)

    monkeypatch.setattr(loser, "_notify_existing", failing_then_real.__get__(loser, SingleInstance))

    with qtbot.waitSignal(winner.activated, timeout=5000):
        assert loser.acquire() is False
    winner.release()


def test_release_frees_lock_for_next_instance(tmp_path):
    # 釋放後，同名的其他實例可以取得鎖
    key = f"InstrumentBooking.test.{uuid.uuid4().hex}"
    lock_path = tmp_path / "test.lock"

    a = SingleInstance(key, lock_path=lock_path)
    assert a.acquire()
    a.release()

    b = SingleInstance(key, lock_path=lock_path)
    assert b.acquire()
    b.release()
