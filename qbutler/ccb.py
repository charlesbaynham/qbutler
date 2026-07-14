"""Auto-launch qbutler's live-view dashboard applets via the ARTIQ CCB.

qbutler creates its own applets so a calibration run visualises itself without
the user wiring anything up: a single DAG overview, plus one optimizer-trace
plot per calibration class as that class first rescans. Both live under a
``Calibrations`` group in the dashboard's applet tree, with the per-class
optimizer plots nested under an ``Optimizers`` subgroup.

Every entry point here is best-effort and never raises: the ``ccb`` device is
only present inside a real ARTIQ worker, so a missing device (e.g. under unit
tests) or any dashboard hiccup is logged and swallowed rather than being
allowed to break a calibration run. Applet creation is idempotent on the
dashboard side (keyed by name + group), so calling these repeatedly across a
run just refreshes the existing applet.
"""

import logging

logger = logging.getLogger(__name__)

#: Top-level group the applets live under in the dashboard's applet tree.
APPLET_GROUP = "Calibrations"

#: Subgroup (nested under :data:`APPLET_GROUP`) for the per-class optimizer plots.
OPTIMIZER_GROUP = [APPLET_GROUP, "Optimizers"]


def _create_applet(env, name, command, group):
    """Issue a best-effort ``create_applet`` CCB request from ``env``.

    ``env`` is any ARTIQ ``HasEnvironment`` (a :class:`Calibration` is one) used
    only to reach the ``ccb`` virtual device. Never raises.
    """
    try:
        ccb = env.get_device("ccb")
    except Exception:
        logger.debug(
            "No ccb device available; not creating applet %r", name, exc_info=True
        )
        return
    try:
        ccb.issue("create_applet", name, command, group=group)
        logger.debug("Requested applet %r (group %s)", name, group)
    except Exception:
        logger.warning("Could not create applet %r", name, exc_info=True)


def create_dag_applet(env):
    """Ensure the calibration-DAG overview applet exists."""
    from .calibration import STATUS_DATASET
    from .dag import DAG_DATASET

    command = (
        "${python} -m qbutler.applets.dag_applet " f"{DAG_DATASET} {STATUS_DATASET}"
    )
    _create_applet(env, "Calibration DAG", command, APPLET_GROUP)


def create_optimizer_applet(env, class_name):
    """Ensure the optimizer-trace applet for ``class_name`` exists.

    One applet per calibration class, so each class's optimizer sweep is shown
    in its own separately-namespaced plot.
    """
    from .calibration import OPTIMIZER_DATASET

    command = (
        "${python} -m qbutler.applets.optimizer_applet "
        f"--calibration {class_name} {OPTIMIZER_DATASET}"
    )
    _create_applet(env, f"Optimizer: {class_name}", command, OPTIMIZER_GROUP)
