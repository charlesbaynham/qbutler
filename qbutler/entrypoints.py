"""
*Entrypoints* are the interface from regular ARTIQ / ndscan to qbutler,
analagous to entrypoint in ndscan. There are two options:

1. Standalone Calibrations. Use :meth:`build_interface_from_calibration` to
   launch a Calibration as a standalone EnvExperiment to check and maintain a
   tree of Calibrations indefinitely. See the docs for
   :meth:`build_interface_from_calibration` for more details.

2. Calibrations in a Fragment. This is the main use-case of qbutler. Use
   :meth:`setattr_calibration`.

Expose the final Calibration in a DAG as an attribute of the ARTIQ
HasEnvironment object, ready for its methods to be called (see the documentation
for :class:`~qbutler.calibration.Calibration` for details).
"""

from typing import Type

from artiq.experiment import EnvExperiment
from ndscan.experiment import Fragment

from .calibration import Calibration


def setattr_calibration(
    self: Fragment,
    calibration_class: Type["Calibration"],
    name: str = None,
    *args,
    **kwargs
) -> "Fragment":
    """
    Create a Calibration and set it as an attribute of this Fragment.

    This method is added to the Fragment class when qbutler is imported: it can
    therefore be called from any Fragment. This method should be used to add the
    final Calibration in a DAG to a Fragment that consumes it, i.e. the Fragment
    that requires this Calibraiton and all its dependents to have been set up
    successfully.

    Any additional arguments are passed to setattr_fragment.
    """
    if name is None:
        name = calibration_class.__name__

    self.setattr_fragment(name, calibration_class, *args, **kwargs)


setattr(Fragment, "setattr_calibration", setattr_calibration)


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

    return NotImplementedError()

    # def build(self: EnvExperiment):
    #     self.setattr_argument(
    #         "timeout", NumberValue(default=1.0, unit="s", min=0, ndecimals=1)
    #     )

    #     self.setattr_device("scheduler")
    #     self.scheduler: Scheduler

    #     self.cal_target: Calibration = cal

    # def run(self):
    #     while True:
    #         if self.scheduler.check_pause():
    #             return

    #         self.cal_target.check_state(continue_on_fail=True)

    #         sleep(self.timeout)

    # if not name:
    #     name = cal.__class__.__name__ + "Interface"

    # return type(name, (EnvExperiment,), {"build": build, "run": run})
