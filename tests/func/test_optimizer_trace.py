"""The live optimizer trace: every optimizer point is broadcast to
OPTIMIZER_DATASET as it is measured, so an applet can watch the scan happen.

Covers both driver loops — the host loop (host check_own_state) and the
resident kernel loop (kernel check_own_state) — and the per-fix reset.
"""

import pytest

from qbutler.calibration import OPTIMIZER_DATASET
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.optimizers import NUM_SCAN_POINT
from tests.func import kernel_calibrations


class TracedCalibration(Calibration):
    """Host check_own_state; data = 10 * test, optimum at test = 1."""

    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0.0, 1.0, default=0.5)

    def check_own_state(self):
        return CalibrationResult.OK, 10.0 * self.test.get()


def _entry(dataset_db, class_name):
    return dataset_db.get(OPTIMIZER_DATASET)[class_name]


def test_host_loop_traces_every_point(fragment_factory, dataset_db):
    c = fragment_factory(TracedCalibration)
    c.fix_state(force=True)

    entry = _entry(dataset_db, "TracedCalibration")
    assert entry["param_names"] == ["test"]
    assert len(entry["points"]) == NUM_SCAN_POINT
    assert len(entry["data"]) == NUM_SCAN_POINT
    assert len(entry["status"]) == NUM_SCAN_POINT
    # The trace is the actual grid: data = 10 * swept value.
    for point, data in zip(entry["points"], entry["data"]):
        assert data == pytest.approx(10.0 * point[0])
    assert entry["status"] == [int(CalibrationResult.OK)] * NUM_SCAN_POINT


def test_trace_resets_each_fix(fragment_factory, dataset_db):
    c = fragment_factory(TracedCalibration)
    c.fix_state(force=True)
    c.fix_state(force=True)

    # A second fix replaces the trace, it does not append to the first.
    entry = _entry(dataset_db, "TracedCalibration")
    assert len(entry["points"]) == NUM_SCAN_POINT


@pytest.mark.withartiq
def test_kernel_loop_traces_every_point(fragment_factory, dataset_db):
    """The resident kernel loop emits each point over its per-point RPC, so
    the trace is populated even though the sweep runs inside one kernel."""
    c = fragment_factory(kernel_calibrations.KernelOptimizableCalibration)
    c.fix_own_state()

    entry = _entry(dataset_db, "KernelOptimizableCalibration")
    assert entry["param_names"] == ["param1"]
    assert len(entry["points"]) == NUM_SCAN_POINT
    assert len(entry["data"]) == NUM_SCAN_POINT
    # data = 10 - |p - 7| (the calibration's synthetic parabola, optimum at 7).
    for point, data in zip(entry["points"], entry["data"]):
        assert data == pytest.approx(10.0 - abs(point[0] - 7.0))
    # At least one point sits inside the OK window (data > 8).
    assert int(CalibrationResult.OK) in entry["status"]


@pytest.mark.withartiq
def test_kernel_feedback_loop_traces_every_point(fragment_factory, dataset_db):
    """A feedback optimizer streamed into the resident kernel also traces."""
    c = fragment_factory(kernel_calibrations.KernelFeedbackOptimizableCalibration)
    c.fix_own_state()

    entry = _entry(dataset_db, "KernelFeedbackOptimizableCalibration")
    assert len(entry["points"]) > 0
    assert len(entry["points"]) == len(entry["data"]) == len(entry["status"])
