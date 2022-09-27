from example.example_monitor import MonitorMaster
from qbutler.calibration import CalibrationResult


def test_monitor_can_both_pass_and_fail(fragment_factory):
    c = fragment_factory(MonitorMaster)

    c.init_params()

    states = [c.check_state() for _ in range(20)]
    assert not all([s == CalibrationResult.OK for s in states])
    assert not all([s == CalibrationResult.BAD_DATA for s in states])
