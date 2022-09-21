"""
Test for basic functionality. This test file should grow and, one day, split.
"""
from ndscan.experiment import Fragment
from pytest import raises

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class MinimalCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


def test_cannot_make_bare_calibration(dataset_mgr):
    with raises(NotImplementedError):
        Calibration((None, dataset_mgr, None, None), fragment_path=[])


def test_can_make_minimal_calibration(dataset_mgr):
    MinimalCalibration((None, dataset_mgr, None, None), fragment_path=[])


def test_can_guess_own_state(dataset_mgr):
    c = MinimalCalibration((None, dataset_mgr, None, None), fragment_path=[])

    assert c.guess_own_state() == CalibrationResult.BAD_EXPIRED


def test_can_check_own_state(dataset_mgr):
    c = MinimalCalibration((None, dataset_mgr, None, None), fragment_path=[])

    assert c.check_own_state() == CalibrationResult.OK


def test_can_guess_all_states(dataset_mgr):
    c = MinimalCalibration((None, dataset_mgr, None, None), fragment_path=[])

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED


def test_can_check_all_states(dataset_mgr):
    c = MinimalCalibration((None, dataset_mgr, None, None), fragment_path=[])

    assert c.check_state() == CalibrationResult.OK


def test_can_make_fragment(dataset_mgr):
    class TestFragment(Fragment):
        def build_fragment(self):
            pass

    TestFragment((None, dataset_mgr, None, None), fragment_path=[])
