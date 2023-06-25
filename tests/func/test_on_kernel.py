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
