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

    def run_once(self) -> None:
        self.status.push(CalibrationResult.OK)


def test_cannot_make_bare_calibration(experiment_factory):
    with raises(NotImplementedError):
        experiment_factory(Calibration)


def test_can_make_minimal_calibration(experiment_factory):
    experiment_factory(MinimalCalibration)


def test_can__guess_own_state(experiment_factory):
    c = experiment_factory(MinimalCalibration)

    assert c._guess_own_state() == CalibrationResult.BAD_EXPIRED


def test_can_check_own_state(experiment_factory):
    c = experiment_factory(MinimalCalibration)

    assert c.check_own_state() == CalibrationResult.OK


def test_can_guess_all_states(experiment_factory):
    c = experiment_factory(MinimalCalibration)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED


def test_can_check_all_states(experiment_factory):
    c = experiment_factory(MinimalCalibration)

    assert c.check_state() == CalibrationResult.OK


def test_can_make_fragment(experiment_factory):
    class TestFragment(Fragment):
        def build_fragment(self):
            pass

    experiment_factory(TestFragment)


@pytest.mark.xfail
def test_run_once(experiment_factory):
    c = experiment_factory(MinimalCalibration)

    c.run_once()


class ParamsCalibration(Calibration):
    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0, 1)

    def run_once(self) -> None:
        self.status.push(CalibrationResult.OK)


@pytest.mark.xfail
def test_can_make_params_calibration(experiment_factory):
    experiment_factory(ParamsCalibration)
