"""Auto-launch qbutler's live-view dashboard applets via the ARTIQ CCB.

qbutler creates its own applets so a calibration run visualises itself without
the user wiring anything up: a DAG overview, plus one optimizer-trace plot per
calibration class as that class first rescans. Both live under a
``Calibrations/<pipeline>`` group in the dashboard's applet tree, with the
per-class optimizer plots nested under an ``Optimizers`` subgroup below that.

Applet creation is idempotent on the dashboard side, keyed by name + group, so
calling these repeatedly across a run just refreshes the existing applet -- and
so two runs in different pipelines would otherwise fight over one entry. The
pipeline level in the group, and the matching pipeline suffix on the dataset
keys in the command, is what keeps them apart; see :mod:`qbutler.scoping`.

Every entry point here is best-effort and never raises: the ``ccb`` device is
only present inside a real ARTIQ worker, so a missing device (e.g. under unit
tests) or any dashboard hiccup is logged and swallowed rather than being
allowed to break a calibration run.
"""

import logging

from . import scoping

logger = logging.getLogger(__name__)

#: Subgroup (nested below the pipeline) for the per-class optimizer plots.
OPTIMIZER_SUBGROUP = "Optimizers"


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
    """Ensure this pipeline's calibration-DAG overview applet exists.

    One applet per pipeline, plotting that pipeline's own DAG. The status table
    it colours nodes from is shared across pipelines, but only nodes present in
    this pipeline's DAG are drawn.
    """
    from .calibration import STATUS_DATASET
    from .dag import DAG_DATASET

    command = (
        "${python} -m qbutler.applets.dag_applet "
        f"{scoping.scoped_key(DAG_DATASET, env)} {STATUS_DATASET}"
    )
    _create_applet(env, "Calibration DAG", command, scoping.applet_group(env))


def create_optimizer_applet(env, class_name):
    """Ensure this pipeline's optimizer-trace applet for ``class_name`` exists.

    One applet per calibration class per pipeline, so each class's optimizer
    sweep is shown in its own separately-namespaced plot.
    """
    from .calibration import OPTIMIZER_DATASET

    command = (
        "${python} -m qbutler.applets.optimizer_applet "
        f"--calibration {class_name} {scoping.scoped_key(OPTIMIZER_DATASET, env)}"
    )
    _create_applet(
        env,
        f"Optimizer: {class_name}",
        command,
        scoping.applet_group(env, OPTIMIZER_SUBGROUP),
    )
