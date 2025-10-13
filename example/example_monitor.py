import logging
from random import random

from ndscan.experiment.parameters import FloatParam

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.monitoring import make_monitor_controller

logger = logging.getLogger(__name__)


class SimpleMonitor(Calibration):
    def build_calibration(self):
        self.set_timeout(1.0)  # 1s timeout

    def check_own_state(self):
        return CalibrationResult.OK, 123.0


class RandomMonitor(Calibration):
    def build_calibration(self):
        self.setattr_param(
            "threshold",
            FloatParam,
            "Threshold above which this monitor will report 'BAD'",
            default=0.5,
        )
        self.threshold: FloatParam
        self.set_timeout(1.1)  # 1.1 seconds, just to be different

    def check_own_state(self):
        logger.debug("Monitor check_own_state ran once")
        r = random()
        if r > self.threshold.get():
            result = CalibrationResult.BAD_DATA
        else:
            result = CalibrationResult.OK

        return result, r


MyMonitorMaster = make_monitor_controller(
    "MyMonitorMaster", monitors={"simple": SimpleMonitor, "random": RandomMonitor}
)
