from artiq.experiment import EnvExperiment
from artiq.experiment import kernel
from ndscan.experiment import ExpFragment
from ndscan.experiment.entry_point import make_fragment_scan_exp

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class MinimalKernelCalibration(Calibration):
    def build_calibration(self):
        pass

    @kernel
    def check_own_state(self):
        return CalibrationResult.OK, None


class MinimalKernelCalibrationExperiment(ExpFragment):
    def build_fragment(self, *args, **kwargs) -> None:
        self.setattr_calibration(MinimalKernelCalibration)
        self.MinimalKernelCalibration: MinimalKernelCalibration

        return super().build_fragment(*args, **kwargs)

    def run(self):
        self.MinimalKernelCalibration.check_state()


MinimalKernelCalibrationExperiment = make_fragment_scan_exp(
    MinimalKernelCalibrationExperiment
)
