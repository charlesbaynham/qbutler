from time import sleep

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class ImmediateTimeoutCalibration(Calibration):
    def build_calibration(self):
        self.set_timeout(0)

    def run_once(self) -> None:
        self.status.push(CalibrationResult.OK)


def test_immediate_timeout(fragment_factory):
    c: Calibration = fragment_factory(ImmediateTimeoutCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
    assert c.check_state() == CalibrationResult.OK
    assert c.guess_state() == CalibrationResult.BAD_EXPIRED


class ShortTimeoutCalibration(Calibration):
    def build_calibration(self):
        self.set_timeout(0.1)

    def run_once(self) -> None:
        self.status.push(CalibrationResult.OK)


def test_short_timeout(fragment_factory):
    c: Calibration = fragment_factory(ShortTimeoutCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
    assert c.check_state() == CalibrationResult.OK
    assert c.guess_state() == CalibrationResult.OK
    sleep(0.2)
    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
