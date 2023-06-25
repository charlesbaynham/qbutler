from artiq.coredevice.core import Core
from artiq.experiment import EnvExperiment
from artiq.experiment import kernel

from qbutler.calibration import CalibrationResult


class PrintCalibrationResult(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.core: Core

    @kernel
    def run(self):
        # By using an interaction with the real world, I'm checking that the
        # compiler isn't optimizing away my variables
        t = self.core.get_rtio_counter_mu()

        if t % 2 == 0:
            r = CalibrationResult.OK
        else:
            r = CalibrationResult.BAD_DATA

        if r == CalibrationResult.OK:
            print("All good")
        else:
            print("Bad!")

        a, b = self.return_tuple()

        print(a)
        print(b)

    @kernel
    def return_tuple(self):
        t = self.core.get_rtio_counter_mu()

        if t % 2 == 0:
            r = CalibrationResult.OK
        else:
            r = CalibrationResult.BAD_DATA

        return r, 0.0


def test_calibrationresult_on_core(build_and_run_experiment):
    build_and_run_experiment(PrintCalibrationResult)
