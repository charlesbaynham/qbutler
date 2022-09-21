from time import sleep

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class ImmediateTimeoutCalibration(Calibration):
    def build_calibration(self):
        self.set_timeout(0)

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


def test_immediate_timeout(calibration_factory):
    c: Calibration = calibration_factory(ImmediateTimeoutCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
    assert c.check_state() == CalibrationResult.OK
    assert c.guess_state() == CalibrationResult.BAD_EXPIRED


class ShortTimeoutCalibration(Calibration):
    def build_calibration(self):
        self.set_timeout(0.1)

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


def test_short_timeout(calibration_factory):
    c: Calibration = calibration_factory(ShortTimeoutCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
    assert c.check_state() == CalibrationResult.OK
    assert c.guess_state() == CalibrationResult.OK
    sleep(0.2)
    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
