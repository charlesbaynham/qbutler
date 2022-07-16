from random import random
from qbutler.calibration import Calibration, CalibrationResult


class TestMonitor(Calibration):
    def build_calibration(self):
        self.setattr_param(
            "threshold", "Threshold above which this monitor will report 'BAD'"
        )
        self.set_timeout(1)

    def calibrate(self):
        raise NotImplementedError

    def check_state(self):
        r = random()


class MonitorMaster(Calibration):
    def build_calibration(self):
        self.add_dependency(TestMonitor)

    def check_state(self) -> CalibrationResult:
        return True
