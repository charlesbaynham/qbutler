"""
Test that Calibrations behave as correctly as `EnvFragments` and can return data
from their `run_once` methods.
"""
from ndscan.experiment import run_fragment_once

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class CalibrationWithResults(Calibration):
    def build_calibration(self):
        pass

    def run_once(self):
        self.status.push(CalibrationResult.OK)
        self.data.push(99.0)


def test_can_get_results_from_run_once(fragment_factory):
    c = fragment_factory(CalibrationWithResults)

    results = run_fragment_once(c)
    assert results[c.data] is not None
    assert results[c.status] is not None
    print(results)


def test_can_check_state(fragment_factory):
    c: Calibration = fragment_factory(CalibrationWithResults)

    c.check_state()
