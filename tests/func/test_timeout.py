from time import sleep

import pytest

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class ImmediateTimeoutCalibration(Calibration):
    def build_calibration(self):
        self.set_check_timeout(0)

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_immediate_timeout(fragment_factory):
    c: Calibration = fragment_factory(ImmediateTimeoutCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
    assert c.check_state()[0] == CalibrationResult.OK
    assert c.guess_state() == CalibrationResult.BAD_EXPIRED


class ShortTimeoutCalibration(Calibration):
    def build_calibration(self):
        self.set_check_timeout(0.1)

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_short_timeout(fragment_factory):
    c: Calibration = fragment_factory(ShortTimeoutCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
    assert c.check_state()[0] == CalibrationResult.OK
    assert c.guess_state() == CalibrationResult.OK
    sleep(0.2)
    assert c.guess_state() == CalibrationResult.BAD_EXPIRED


class LegacyTimeoutCalibration(Calibration):
    """Still on the deprecated set_timeout: must behave exactly like
    set_check_timeout."""

    def build_calibration(self):
        self.set_timeout(0.1)

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_legacy_set_timeout_is_the_check_timeout(fragment_factory):
    with pytest.deprecated_call():
        c: Calibration = fragment_factory(LegacyTimeoutCalibration)

    assert c.get_timeout() == 0.1
    assert c.get_check_timeout() == 0.1
    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
    assert c.check_state()[0] == CalibrationResult.OK
    assert c.guess_state() == CalibrationResult.OK
    sleep(0.2)
    assert c.guess_state() == CalibrationResult.BAD_EXPIRED
