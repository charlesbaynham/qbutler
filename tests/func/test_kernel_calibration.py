from artiq.experiment import kernel
from ndscan.experiment import ExpFragment

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class DepCali(Calibration):
    def build_calibration(self):
        pass

    @kernel
    def check_own_state(self):
        return CalibrationResult.OK, None


class GoodCalibration(Calibration):
    def build_calibration(self):
        self.add_dependency(DepCali, "dependent")

    @kernel
    def check_own_state(self):
        return CalibrationResult.OK, None


class GuessState(ExpFragment):
    def build_fragment(self):
        self.setattr_device("core")

        self.setattr_calibration(GoodCalibration, "good_calibration")
        self.good_calibration: Calibration

    @kernel
    def run_once(self):
        self.good_calibration.guess_state()


class CheckState(ExpFragment):
    def build_fragment(self):
        self.setattr_device("core")

        self.setattr_calibration(GoodCalibration, "good_calibration")
        self.good_calibration: Calibration

    @kernel
    def run_once(self):
        self.good_calibration.check_state()
