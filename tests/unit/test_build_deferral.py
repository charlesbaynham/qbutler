"""The entry-point runner must not construct the fragment during build.

The master gives the worker's build action an absolute 15 s budget
(``Worker.build`` ``timeout=15.0``, ``artiq/master/worker.py``; the deadline is
set once when the action starts and is NOT extended by worker↔master traffic,
so no keepalive can feed it), while prepare has no deadline. Rig RIDs
77458/77459: a two-target client whose fragment construction took >15 s was
killed ~15.0 s into build. The fix: ``make_calibrated_experiment``'s runner
builds the fragment tree in ``prepare()``.
"""

from qbutler import worker_ipc_lock
from qbutler.client import make_calibrated_experiment


class _RecordingFrag:
    """Stands in for an expensive CalibratedExpFragment; records lifecycle."""

    instances: list = []

    def __init__(self, env, path):
        type(self).instances.append(self)
        self.inited = False
        self.prepared = False

    def init_params(self):
        self.inited = True

    def prepare(self):
        self.prepared = True


def test_construction_deferred_to_prepare(experiment_factory):
    _RecordingFrag.instances = []
    Experiment = make_calibrated_experiment(_RecordingFrag)

    exp = experiment_factory(Experiment)  # runs build()
    assert _RecordingFrag.instances == [], (
        "fragment constructed during build — the master kills builds after an "
        "absolute 15 s; construction belongs in prepare"
    )
    assert not hasattr(exp, "frag")

    exp.prepare()
    assert len(_RecordingFrag.instances) == 1
    assert exp.frag.inited and exp.frag.prepared


def test_build_installs_ipc_lock_first(experiment_factory):
    """The transaction lock is armed at the earliest worker entry point,
    before any qbutler thread can exist."""
    worker_ipc_lock._installed = False
    try:
        Experiment = make_calibrated_experiment(_RecordingFrag)
        experiment_factory(Experiment)  # build()
        assert worker_ipc_lock._installed
    finally:
        worker_ipc_lock._installed = True
