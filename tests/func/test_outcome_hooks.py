"""The ``_on_checked`` / ``_on_recalibrated`` hooks.

qbutler stays agnostic about where calibration outcomes are recorded, and
exposes them as two no-op hooks for a downstream mix-in to implement. These
tests pin down what a downstream implementer is entitled to assume: that every
recorded check is reported with its metric and a context saying what kind of
check it was, and that a recalibration is reported with the metric measured at
the committed optimum.
"""

import pytest

from qbutler.calibration import CHECK_CONTEXT_CHECK
from qbutler.calibration import CHECK_CONTEXT_FIX_FAILED
from qbutler.calibration import CHECK_CONTEXT_SWEEP
from qbutler.calibration import CHECK_CONTEXT_VERIFY
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationError
from qbutler.calibration import CalibrationResult
from qbutler.optimizers import NUM_SCAN_POINT
from tests.func import kernel_calibrations


class RecordingMixin:
    """Capture what the hooks are handed, the way a real logger would."""

    def build_fragment(self, *args, **kwargs):
        super().build_fragment(*args, **kwargs)
        self.checks = []
        self.recalibrations = []

    def _on_checked(self, result, metric, context):
        self.checks.append((result, metric, context))

    def _on_recalibrated(self, committed_params, metric):
        self.recalibrations.append((committed_params, metric))


class RecordedCalibration(RecordingMixin, Calibration):
    """Host check_own_state; metric = 10 * test, optimum (and only OK point)
    at test = 1."""

    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0.0, 1.0, default=0.5)

    def check_own_state(self):
        metric = 10.0 * self.test.get()
        if metric >= 10.0:
            return CalibrationResult.OK, metric
        return CalibrationResult.BAD_DATA, metric


class UnfixableCalibration(RecordingMixin, Calibration):
    """No point ever checks out, so the optimizer gives up."""

    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0.0, 1.0, default=0.5)
        self.set_max_fix_attempts(1)

    def check_own_state(self):
        return CalibrationResult.BAD_DATA, -1.0


class MetriclessCalibration(RecordingMixin, Calibration):
    """A calibration that measures nothing beyond pass/fail."""

    def build_calibration(self):
        pass

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_check_reports_result_metric_and_context(fragment_factory):
    c = fragment_factory(RecordedCalibration)
    c.check_state()

    assert c.checks == [(CalibrationResult.BAD_DATA, 5.0, CHECK_CONTEXT_CHECK)]


def test_failing_check_is_reported_too(fragment_factory):
    """A check is reported whether it passed or failed — the failures are the
    interesting half of a health history."""
    c = fragment_factory(RecordedCalibration)
    c.check_state()
    c.check_state(force=True)

    assert [r for r, _, _ in c.checks] == [CalibrationResult.BAD_DATA] * 2


def test_metricless_calibration_reports_none(fragment_factory):
    c = fragment_factory(MetriclessCalibration)
    c.check_state()

    assert c.checks == [(CalibrationResult.OK, None, CHECK_CONTEXT_CHECK)]


def test_hooks_never_break_a_calibration(fragment_factory):
    """Both hooks are best-effort: a broken implementation is logged and
    ignored, never allowed to fail the run."""

    class Exploding(RecordedCalibration):
        def _on_checked(self, result, metric, context):
            raise RuntimeError("boom")

        def _on_recalibrated(self, committed_params, metric):
            raise RuntimeError("boom")

    c = fragment_factory(Exploding)
    c.fix_state(force=True)

    assert c.check_state(force=True)[0] == CalibrationResult.OK


def test_fix_reports_sweep_verify_and_recalibration(fragment_factory):
    c = fragment_factory(RecordedCalibration)
    c.fix_state(force=True)

    contexts = [context for _, _, context in c.checks]
    # One sweep point per grid point, then the verification of the best point,
    # then the fix walk's own re-check of the fixed node.
    assert contexts == (
        [CHECK_CONTEXT_SWEEP] * NUM_SCAN_POINT
        + [CHECK_CONTEXT_VERIFY, CHECK_CONTEXT_CHECK]
    )

    # The sweep deliberately measures bad points; only the verification and the
    # re-check describe the state the calibration was left in.
    sweep_results = [r for r, _, context in c.checks if context == CHECK_CONTEXT_SWEEP]
    assert CalibrationResult.BAD_DATA in sweep_results
    assert [r for r, _, context in c.checks if context != CHECK_CONTEXT_SWEEP] == [
        CalibrationResult.OK,
        CalibrationResult.OK,
    ]


def test_recalibration_reports_the_metric_at_the_optimum(fragment_factory):
    c = fragment_factory(RecordedCalibration)
    c.fix_state(force=True)

    assert len(c.recalibrations) == 1
    committed_params, metric = c.recalibrations[0]
    assert committed_params["test"] == pytest.approx(1.0)
    # The metric measured with the committed parameters applied, not the
    # default-parameter value the last routine check happened to see.
    assert metric == pytest.approx(10.0)


def test_a_fix_that_gives_up_is_reported_as_such(fragment_factory):
    c = fragment_factory(UnfixableCalibration)
    with pytest.raises(CalibrationError):
        c.fix_state(force=True)

    assert c.checks[-1] == (CalibrationResult.BAD_DATA, None, CHECK_CONTEXT_FIX_FAILED)
    assert c.recalibrations == []


@pytest.mark.withartiq
def test_kernel_fix_reports_verify_and_recalibration(fragment_factory):
    """The resident kernel loop does not record its trial points as checks, so
    a kernel-mode fix reports the verification measurement and nothing else."""

    class RecordedKernelCalibration(
        RecordingMixin, kernel_calibrations.KernelOptimizableCalibration
    ):
        pass

    c = fragment_factory(RecordedKernelCalibration)
    c.fix_own_state()

    assert [context for _, _, context in c.checks] == [CHECK_CONTEXT_VERIFY]

    assert len(c.recalibrations) == 1
    committed_params, metric = c.recalibrations[0]
    # data = 10 - |p - 7|, so the optimum sits at param1 = 7 with metric 10.
    assert committed_params["param1"] == pytest.approx(7.0, abs=1.0)
    assert metric == pytest.approx(10.0 - abs(committed_params["param1"] - 7.0))
