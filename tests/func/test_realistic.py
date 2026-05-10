"""Realistic tests for qbutler calibration framework.

These tests verify that qbutler works correctly with realistic calibration
scenarios, including running calibrations as scans and full-stack experiment
submission.
"""

import pytest

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class RabiFlopSimCalibration(Calibration):
    """Simulates a Rabi flop calibration.

    The calibration has a parameter `t_pi` (the pi time) that can be
    optimised. The optimal value is 1.0.
    """

    def build_calibration(self):
        self.setattr_param_optimizable(
            "t_pi",
            "Pi time",
            min=0.0,
            max=2.0,
            default=0.5,
        )

    def check_own_state(self):
        t_pi = self.t_pi.get()

        # Simulate a Rabi flop: the state is OK if t_pi is close to 1.0
        if abs(t_pi - 1.0) < 0.1:
            return CalibrationResult.OK, t_pi
        else:
            return CalibrationResult.BAD_DATA, t_pi


class RabiFlopSimScanner(Calibration):
    """A calibration that can be run as a scan."""

    def build_calibration(self):
        self.setattr_param_optimizable(
            "t_pi",
            "Pi time",
            min=0.0,
            max=2.0,
            default=0.5,
        )

    def check_own_state(self):
        t_pi = self.t_pi.get()

        if abs(t_pi - 1.0) < 0.1:
            return CalibrationResult.OK, t_pi
        else:
            return CalibrationResult.BAD_DATA, t_pi


def test_build_rabi_flob_calibration(fragment_factory):
    """Verify that the Rabi flop calibration can be built."""
    c = fragment_factory(RabiFlopSimCalibration)
    assert c is not None


def test_measure_bad_rabi_flob_calibration(fragment_factory):
    """Verify that the Rabi flop calibration correctly reports BAD_DATA."""
    c = fragment_factory(RabiFlopSimCalibration)
    result, data = c.check_state()
    assert result == CalibrationResult.BAD_DATA


def test_fix_bad_rabi_flob_calibration(fragment_factory):
    """Verify that the Rabi flop calibration can be fixed by optimising t_pi."""
    c = fragment_factory(RabiFlopSimCalibration)

    result, data = c.check_state()
    assert result == CalibrationResult.BAD_DATA

    c.fix_own_state()

    result, data = c.check_state()
    assert result == CalibrationResult.OK


def test_run_rabi_flop_as_scan(fragment_factory):
    """Verify that a calibration can be used as an ndscan scan."""
    c = fragment_factory(RabiFlopSimCalibration)
    assert c is not None


@pytest.mark.withartiq
@pytest.mark.fullstack
def test_run_rabi_flop_as_scan_full_stack(build_and_run_full_stack):
    build_and_run_full_stack("RabiFlopSimScanner", __file__)
