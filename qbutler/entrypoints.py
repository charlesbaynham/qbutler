from time import sleep

from artiq.experiment import EnvExperiment
from artiq.experiment import NumberValue
from artiq.master.scheduler import Scheduler

from qbutler.calibration import Calibration


def build_interface_from_calibration(
    cal: Calibration, name: str = None
) -> EnvExperiment:
    """
    Build an EnvExperiment from a Calibration, allowing you to schedule it in
    the pipeline

    The primary purpose of Calibrations is to be called from an EnvExperiment or
    Fragment to guarantee calibrated performance of your quantum system before
    embarking on an experiment which assumes a well defined state. For that, the
    functions in :mod:`qbutler.entrypoints` exist.

    However, it is sometimes useful to run Calibrations (and their dependency
    chains) separately, either for debugging or to e.g. implement standing lab
    monitors. This function creates a EnvExperiment which will be added to the
    ARTIQ GUI to allow you to launch a standalone Calibration chain with a given
    calibration as the most advanced calibration target.

    For now, this interface just runs :meth:`Calibration.do_check` on all
    Calibration objects in the chain at a regular interval. This is a WIP.

    Args:
        cal (Calibration):  The most advanced calibration target. This will be
                            the final step in the Calibration chain generated.

    Returns:
        EnvExperiment: Standalone ARTIQ experiment
    """

    def build(self: EnvExperiment):
        self.setattr_argument(
            "timeout", NumberValue(default=1.0, unit="s", min=0, ndecimals=1)
        )

        self.setattr_device("scheduler")
        self.scheduler: Scheduler

        self.cal_target: Calibration = cal

    def run(self):
        while True:
            if self.scheduler.check_pause():
                return

            self.cal_target.check_state(continue_on_fail=True)

            sleep(self.timeout)

    if not name:
        name = cal.__class__.__name__ + "Interface"

    return type(name, (EnvExperiment,), {"build": build, "run": run})
