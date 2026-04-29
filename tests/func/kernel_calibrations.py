from artiq.experiment import kernel
from ndscan.experiment import ExpFragment
from ndscan.experiment.entry_point import make_fragment_scan_exp

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class MinimalKernelCalibration(Calibration):
    def build_calibration(self):
        self.setattr_device("core")

    @kernel
    def check_own_state(self):
        return CalibrationResult.OK, None


class MinimalKernelCalibrationExperiment(ExpFragment):
    def build_fragment(self) -> None:
        self.setattr_calibration(MinimalKernelCalibration)
        self.MinimalKernelCalibration: MinimalKernelCalibration

    def run_once(self):
        state = self.MinimalKernelCalibration.check_state()
        print(f"State : {state}")


MinimalKernelCalibrationExperiment = make_fragment_scan_exp(
    MinimalKernelCalibrationExperiment
)


class KernelOptimizableCalibration(Calibration):
    """A calibration with kernel check_own_state that can be optimized.

    The optimal parameter value is 7.0, where the data peaks.
    """

    def build_calibration(self):
        self.setattr_device("core")
        self.setattr_param_optimizable(
            "param1",
            "Test param",
            min=0.0,
            max=10.0,
            default=5.0,
        )

    @kernel
    def check_own_state(self):
        p = self.param1.get()
        data = 10.0 - abs(p - 7.0)
        if data > 8.0:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class KernelOptimizableCalibrationExperiment(ExpFragment):
    def build_fragment(self) -> None:
        self.setattr_calibration(KernelOptimizableCalibration)
        self.KernelOptimizableCalibration: KernelOptimizableCalibration

    def run_once(self):
        state = self.KernelOptimizableCalibration.check_state()
        print(f"State : {state}")


KernelOptimizableCalibrationExperiment = make_fragment_scan_exp(
    KernelOptimizableCalibrationExperiment
)


class KernelFixOwnStateCalibration(Calibration):
    """A calibration with kernel fix_own_state."""

    def build_calibration(self):
        self.setattr_device("core")
        self.broken = True

    def check_own_state(self):
        if self.broken:
            return CalibrationResult.BAD_DATA, None
        else:
            return CalibrationResult.OK, None

    @kernel
    def fix_own_state(self):
        self.broken = False


class KernelFixOwnStateCalibrationExperiment(ExpFragment):
    def build_fragment(self) -> None:
        self.setattr_calibration(KernelFixOwnStateCalibration)
        self.KernelFixOwnStateCalibration: KernelFixOwnStateCalibration

    def run_once(self):
        state = self.KernelFixOwnStateCalibration.check_state()
        print(f"State : {state}")


KernelFixOwnStateCalibrationExperiment = make_fragment_scan_exp(
    KernelFixOwnStateCalibrationExperiment
)
