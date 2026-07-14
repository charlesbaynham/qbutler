import logging
from typing import Any
from typing import Tuple

from ndscan.experiment import Fragment
from ndscan.experiment.parameters import ParamStore
from ndscan.experiment.scan_generator import generate_points
from ndscan.experiment.scan_runner import ScanRunner

logger = logging.getLogger(__name__)


def reset_param(self: Fragment, param_name: str) -> Tuple[Any, ParamStore]:
    """Reset the parameter with the given name to its default value

    Undoes the work of  :meth:`override_param`, which overrides the parameter to
    a given value.

    Note that the default value are not recalculated: use
    :meth:`recompute_param_defaults` for that.

    :param param_name: The name of the parameter.

    :return: A tuple ``(param, store)`` of the parameter metadata and the
        original and now rebound :class:`.ParamStore` instance that the
        parameter handles are now bound to.
    """
    assert (
        self._free_params.get(param_name, None) is None
    ), "Already a free parameter: '{}'".format(param_name)

    fqn = self.fqn + "." + param_name

    param: Any = None
    store: ParamStore = None
    for this_param, this_store in self._default_params:
        if this_param.fqn == fqn:
            param = this_param
            store = this_store

    if param is None:
        raise KeyError("Parameter {} not found".format(param_name))

    for handle in self._get_all_handles_for_param(param_name):
        handle.set_store(store)

    self._free_params[param_name] = param

    return param, store


setattr(Fragment, "reset_param", reset_param)


def _scan_runner_run_with_recalibration(self, fragment, spec, axis_sinks):
    """``ScanRunner.run`` with a calibration-escape/re-enter detour woven in.

    Structurally identical to ndscan's own ``ScanRunner.run`` — the same
    ``setup``/``set_points`` then ``while True:`` loop of
    ``recompute_param_defaults`` → ``host_setup`` → ``acquire`` → ``host_cleanup``
    → ``scheduler.pause``. The only addition: if ``acquire()`` unwinds with a
    :class:`~qbutler.calibration.CalibrationEscape` (raised from the science
    kernel at a scan-point boundary), run the fragment's DAG fix on the host and
    loop back into ``acquire()``. Because ndscan's scan runners only mark a point
    complete after it finishes (``KernelScanRunner`` keeps the in-flight point in
    ``_current_chunk``; ``_point_completed`` pops it), re-entering ``acquire()``
    resumes at the interrupted point — it re-runs, already-completed points do
    not, so every point lands exactly once.

    Only :class:`~qbutler.client.CalibratedExpFragment` exposes ``_recalibrate``;
    for any other fragment ``fix`` is ``None``, no escape is ever raised, and
    this is byte-for-byte the original loop.
    """
    # Imported here (not at module top) to avoid an import cycle: calibration.py
    # imports this module.
    from .calibration import CalibrationError
    from .calibration import CalibrationEscape

    self.setup(fragment, spec.axes, axis_sinks)
    self.set_points(generate_points(spec.generators, spec.options))

    fix = getattr(fragment, "_recalibrate", None)
    max_recalibrations = getattr(fragment, "max_recalibrations", 0)
    recalibrations = 0

    while True:
        # After every pause(), pull in dataset changes (immediately as well to
        # catch changes between the time the experiment is prepared and when it
        # is run, to keep the semantics uniform). A fix also rewrites committed
        # calibration params to datasets, so this pulls those in on re-entry.
        fragment.recompute_param_defaults()
        escaped = False
        try:
            fragment.host_setup()
            # For on-core-device scans, we'll spawn a kernel here.
            if self.acquire():
                return
        except CalibrationEscape:
            if fix is None:
                raise
            escaped = True
        finally:
            fragment.host_cleanup()
            # For host-only scans, self.core might be artiq.sim.devices.Core or
            # similar without a close() method.
            if hasattr(self.core, "close"):
                self.core.close()

        if escaped:
            recalibrations += 1
            if recalibrations > max_recalibrations:
                raise CalibrationError(
                    f"Scan of {type(fragment).__name__} escaped for recalibration "
                    f"more than {max_recalibrations} times without settling. A "
                    "calibration is not converging: check the failing node's "
                    "optimizer bounds and measurement, or raise "
                    "max_recalibrations if this is expected."
                )
            logger.info(
                "Calibration escape mid-scan (%d/%d): fixing DAG and resuming",
                recalibrations,
                max_recalibrations,
            )
            fix()
            continue
        self.scheduler.pause()


setattr(ScanRunner, "run", _scan_runner_run_with_recalibration)
