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
