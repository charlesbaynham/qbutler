"""Per-pipeline namespacing for qbutler's live-view datasets and applets.

Two calibration runs in different ARTIQ pipelines are two independent walks over
two independently-instantiated DAGs. Without namespacing they publish over each
other: the dashboard keys an applet on (name, group) and replaces the spec of an
existing entry, so both pipelines end up sharing one applet that flickers
between their two graphs, and both write the whole DAG / optimizer table to one
dataset key.

Everything here therefore suffixes a dataset key, or inserts a group level, with
the running pipeline's name. Where no pipeline can be determined -- ``artiq_run``,
a bare unit test, any environment without a scheduler -- the unscoped key and
group are used, so single-pipeline and host-only behaviour is unchanged.

Note that :data:`~qbutler.calibration.STATUS_DATASET` is deliberately *not*
scoped: see the note there.

This module imports nothing from qbutler, so it can be used from any of them.
"""

import logging
import re

logger = logging.getLogger(__name__)

#: Top-level group the applets live under in the dashboard's applet tree.
APPLET_GROUP = "Calibrations"

#: Characters not allowed in the pipeline component of a dataset key. A dot is
#: the dashboard's dataset-tree separator, so it must not survive into the slug.
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def pipeline_name(env):
    """The name of the pipeline ``env`` is running in, or None if unknown.

    ``env`` is any ARTIQ ``HasEnvironment`` (a :class:`~qbutler.calibration.Calibration`
    is one). A ``Calibration`` has already resolved and cached its scheduler by
    the time anything here is called, so the common path is an attribute read;
    other environments fall back to a device lookup. Never raises.
    """
    scheduler = getattr(env, "_scheduler", None)
    if scheduler is None:
        try:
            scheduler = env.get_device("scheduler")
        except Exception:
            logger.debug("No scheduler available; not scoping to a pipeline")
            return None

    name = getattr(scheduler, "pipeline_name", None)
    if not isinstance(name, str) or not name:
        return None
    return name


def _slug(env):
    """The sanitised pipeline component for ``env``, or None if unknown."""
    name = pipeline_name(env)
    if name is None:
        return None
    return _UNSAFE.sub("_", name)


def scoped_key(base, env):
    """``base`` suffixed with ``env``'s pipeline name, e.g. ``calibrations.dag.main``.

    Returns ``base`` unchanged where the pipeline cannot be determined.
    """
    slug = _slug(env)
    return base if slug is None else f"{base}.{slug}"


def applet_group(env, *subgroups):
    """The dashboard applet-tree path for ``env``, e.g. ``["Calibrations", "main"]``.

    Any ``subgroups`` are appended below the pipeline level. Where the pipeline
    cannot be determined the pipeline level is simply omitted, and a path with no
    subgroups collapses to the bare :data:`APPLET_GROUP` string that ARTIQ
    accepts for a top-level group.
    """
    slug = _slug(env)
    path = [APPLET_GROUP]
    if slug is not None:
        path.append(slug)
    path.extend(subgroups)
    return path[0] if len(path) == 1 else path
