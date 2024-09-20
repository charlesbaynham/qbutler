"""
Test for basic functionality. This test file should grow and, one day, split.
"""

from ndscan.experiment import Fragment
from ndscan.experiment import run_fragment_once
from pytest import raises

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class MinimalCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_cannot_make_bare_calibration(fragment_factory):
    with raises(NotImplementedError):
        fragment_factory(Calibration)


def test_can_make_minimal_calibration(fragment_factory):
    fragment_factory(MinimalCalibration)


def test_can__guess_own_state(fragment_factory):
    c = fragment_factory(MinimalCalibration)

    assert c._guess_own_state() == CalibrationResult.BAD_EXPIRED


def test_can_check_own_state(fragment_factory):
    c = fragment_factory(MinimalCalibration)

    assert c.check_own_state()[0] == CalibrationResult.OK


def test_can_guess_all_states(fragment_factory):
    c = fragment_factory(MinimalCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED


def test_can_check_all_states(fragment_factory):
    c = fragment_factory(MinimalCalibration)

    assert c.check_state() == (CalibrationResult.OK, None)


def test_can_make_fragment(fragment_factory):
    class TestFragment(Fragment):
        def build_fragment(self):
            pass

    fragment_factory(TestFragment)


def test_run_once(fragment_factory):
    c = fragment_factory(MinimalCalibration)

    run_fragment_once(c)


class ParamsCalibration(Calibration):
    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0, 1, default=0.5)

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_can_make_params_calibration(fragment_factory):
    fragment_factory(ParamsCalibration)
