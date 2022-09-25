"""
Test for basic functionality. This test file should grow and, one day, split.
"""
import pytest
from ndscan.experiment import Fragment
from pytest import raises

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class MinimalCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


def test_cannot_make_bare_calibration(calibration_factory):
    with raises(NotImplementedError):
        calibration_factory(Calibration)


def test_can_make_minimal_calibration(calibration_factory):
    calibration_factory(MinimalCalibration)


def test_can_guess_own_state(calibration_factory):
    c = calibration_factory(MinimalCalibration)

    assert c.guess_own_state() == CalibrationResult.BAD_EXPIRED


def test_can_check_own_state(calibration_factory):
    c = calibration_factory(MinimalCalibration)

    assert c.check_own_state() == CalibrationResult.OK


def test_can_guess_all_states(calibration_factory):
    c = calibration_factory(MinimalCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED


def test_can_check_all_states(calibration_factory):
    c = calibration_factory(MinimalCalibration)

    assert c.check_state() == CalibrationResult.OK


def test_can_make_fragment(calibration_factory):
    class TestFragment(Fragment):
        def build_fragment(self):
            pass

    calibration_factory(TestFragment)


@pytest.mark.xfail
def test_run_once(calibration_factory):
    c = calibration_factory(MinimalCalibration)

    c.run_once()
