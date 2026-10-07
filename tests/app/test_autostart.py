import pytest

from instrument_booking.app.autostart import VALUE_NAME, apply_autostart, launch_command


class FakeRegistry:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = 0

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.writes += 1
        self.values[name] = value

    def delete(self, name):
        self.writes += 1
        del self.values[name]


def test_launch_command_only_for_packaged_exe():
    assert launch_command(frozen=False) is None
    assert launch_command(frozen=True, executable=r"C:\App\InstrumentBooking.exe") == \
        r'"C:\App\InstrumentBooking.exe" --background'


def test_enable_disable_and_no_redundant_writes():
    reg = FakeRegistry()
    apply_autostart(True, command='"a.exe" --background', registry=reg)
    assert reg.values == {VALUE_NAME: '"a.exe" --background'}
    apply_autostart(True, command='"a.exe" --background', registry=reg)
    assert reg.writes == 1
    apply_autostart(True, command='"b.exe" --background', registry=reg)  # 搬移位置後更新
    assert reg.values[VALUE_NAME] == '"b.exe" --background'
    apply_autostart(False, command='"b.exe" --background', registry=reg)
    assert reg.values == {}
    apply_autostart(False, command='"b.exe" --background', registry=reg)
    assert reg.writes == 3


def test_development_mode_never_touches_registry():
    reg = FakeRegistry({VALUE_NAME: "舊的"})
    apply_autostart(False, command=None, registry=reg)
    apply_autostart(True, command=None, registry=reg)
    assert reg.values == {VALUE_NAME: "舊的"} and reg.writes == 0


def test_registry_errors_propagate():
    class Broken(FakeRegistry):
        def set(self, name, value):
            raise PermissionError("拒絕存取")
    with pytest.raises(OSError):
        apply_autostart(True, command="x", registry=Broken())
