"""Tests for kernel-based calibration optimization.

These tests verify that:
1. The default optimizer works when check_own_state is a kernel
2. The optimization loop triggers only a single kernel call
3. Kernel fix_own_state works correctly
"""
from tests.func import kernel_calibrations

from qbutler.calibration import CalibrationResult


def test_kernel_optimizer_uses_single_kernel_call(fragment_factory, mock_core):
    """Verify that fix_own_state triggers exactly one kernel call for optimization."""
    c = fragment_factory(kernel_calibrations.KernelOptimizableCalibration)

    initial_calls = mock_core.call_count

    c.fix_own_state()

    optimization_calls = mock_core.call_count - initial_calls
    assert optimization_calls == 1, (
        f"Expected exactly 1 kernel call for optimization, got {optimization_calls}"
    )

    # Verify the calibration is now OK
    result, data = c.check_state()
    assert result == CalibrationResult.OK


def test_kernel_optimizer_finds_optimum(fragment_factory):
    """Verify that the optimizer finds a parameter value near the optimum."""
    c = fragment_factory(kernel_calibrations.KernelOptimizableCalibration)

    c.fix_own_state()

    # The optimum is at 7.0; check_state should now pass
    result, data = c.check_state()
    assert result == CalibrationResult.OK


def test_kernel_fix_own_state(fragment_factory):
    """Verify that a kernel fix_own_state correctly fixes the calibration."""
    c = fragment_factory(kernel_calibrations.KernelFixOwnStateCalibration)

    assert c.check_state()[0] == CalibrationResult.BAD_DATA

    c.fix_state()

    assert c.check_state()[0] == CalibrationResult.OK


def test_kernel_fix_own_state_experiment(build_and_run_experiment):
    """Verify that a kernel fix_own_state experiment builds and runs."""
    build_and_run_experiment(
        kernel_calibrations.KernelFixOwnStateCalibrationExperiment,
        kernel_calibrations.__file__,
    )
