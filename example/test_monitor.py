from random import random
from qbutler.calibration import Calibration, CalibrationResult

from ndscan.experiment.parameters import FloatParamHandle


class TestMonitor(Calibration):
    def build_calibration(self):
        self.setattr_param(
            "threshold", "Threshold above which this monitor will report 'BAD'"
        )
        self.threshold: FloatParamHandle
        self.set_timeout(1)

    def calibrate_self(self):
        raise NotImplementedError

    def check_own_state(self):
        r = random()
        if r > self.threshold.get():
            result = CalibrationResult.BAD_DATA
        else:
            result = CalibrationResult.OK

        return result


class MonitorMaster(Calibration):
    def build_calibration(self):
        self.add_dependency(TestMonitor)

    def check_state(self) -> CalibrationResult:
        return True
