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

    def run_once(self):
        self.status.push(CalibrationResult.OK)
        self.data.push(123.0)


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

    def run_once(self) -> None:
        logger.debug("Monitor run_once ran once")
        r = random()
        if r > self.threshold.get():
            result = CalibrationResult.BAD_DATA
        else:
            result = CalibrationResult.OK

        self.status.push(result)
        self.data.push(r)


# def my_db_logger(self, name, state, data):
#     self.my_db_driver.write(name, data)


MyMonitorMaster = make_monitor_controller(
    "MyMonitorMaster", monitors={"simple": SimpleMonitor, "random": RandomMonitor}
)
