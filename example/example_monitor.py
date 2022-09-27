"""
(Ab)use the Calibration framework to create a network of monitors, each of which
regularly checks itself, reports its state to the database and concludes whether
it is "OK" or "BAD".

Requirements:
Each monitor has a configurable timeout
Each monitor writes to the database (using the ARTIQ device as an interface)
Each monitor can be configured with arbitary parameters
All monitors get probed regardless of the state of an individual one

Stretch goals:
A DAG is drawn in an applet which shows the Good / Bad state of each Monitor

Design plan:
`run_once` samples the Calibration once
    It returns a value and a conclusion to two `ResultsChannel`s
`check_own_state` runs `run_once` once and reads the results
`calibrate` would (by default) run `run_once` in a ndscan style scan, then draw some conclusion from it
Monitors differ from normal Calibrations only in that
    a) they have no optimizable parameters (and therefore can't be calibrated)
    b) they have separate code which logs the results to the database and call check with force=true
"""
from random import random

from ndscan.experiment.parameters import FloatParam

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class MonitorTest(Calibration):
    def build_calibration(self):
        self.setattr_param(
            "threshold",
            FloatParam,
            "Threshold above which this monitor will report 'BAD'",
            default=0.5,
        )
        self.threshold: FloatParam
        self.set_timeout(0)

    def run_once(self) -> None:
        r = random()
        if r > self.threshold.get():
            result = CalibrationResult.BAD_DATA
        else:
            result = CalibrationResult.OK

        self.status.push(result)
        self.data.push(r)


class MonitorMaster(Calibration):
    def build_calibration(self):
        self.add_dependency(MonitorTest)

    def run_once(self) -> None:
        self.status.push(CalibrationResult.OK)
