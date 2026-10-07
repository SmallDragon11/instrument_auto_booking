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
