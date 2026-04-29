"""Basic checks for kernel functionality

The unit checker cannot run kernel code without a core, so we're just
checking that experiments compile - better than nothing.
"""
from tests.func import kernel_calibrations


def test_minimal_kernel_calibration(build_and_run_experiment):
    build_and_run_experiment(
        kernel_calibrations.MinimalKernelCalibrationExperiment,
        kernel_calibrations.__file__,
    )


def test_kernel_optimizable_calibration(build_and_run_experiment):
    build_and_run_experiment(
        kernel_calibrations.KernelOptimizableCalibrationExperiment,
        kernel_calibrations.__file__,
    )


def test_kernel_fix_own_state_calibration(build_and_run_experiment):
    build_and_run_experiment(
        kernel_calibrations.KernelFixOwnStateCalibrationExperiment,
        kernel_calibrations.__file__,
    )
