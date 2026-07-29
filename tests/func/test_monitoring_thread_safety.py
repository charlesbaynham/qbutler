"""Regression tests for qbutler#24: the worker IPC lock must be installed on
the module the worker *actually runs*.

The ARTIQ worker is spawned as ``python -m artiq.master.worker_impl``
(``artiq/master/worker.py``), so the live module object is
``sys.modules["__main__"]``. Any ``import artiq.master.worker_impl`` inside the
worker builds a *second, dead copy* with its own globals; the ``parent_action``
closures that really run resolve ``put_object``/``get_object`` out of
``__main__``'s globals and never see a patch applied to the imported copy.

The previous implementation (``qbutler.monitoring.patch_artiq_threading``,
removed with these tests) patched the imported copy, so it was a no-op on the
rig while its tests — which imported the module and drove that same object —
passed. Every test here therefore builds the real two-module topology first.
"""

import threading
import time
import types

import pytest
from sipyco import pyon

import qbutler.worker_ipc_lock as worker_ipc_lock
from qbutler.worker_ipc_lock import install_worker_ipc_lock


class _RecordingPipe:
    """Stand-in for ``sipyco.pipe_ipc.ChildComm``.

    Answers every request with a well-formed reply, and records the order of
    the two halves of each round trip. Each half sleeps, so if two threads are
    ever inside ``parent_action`` at once their put/get events *will* interleave
    — which on a real OS pipe is what splices request lines and hands a thread
    a fragment of someone else's reply (the ``JSONDecodeError`` storms seen on
    MonitorMaster). Asserting on the interleaving tests that invariant directly
    instead of relying on a race to corrupt bytes.
    """

    def __init__(self):
        self.events = []
        self._lock = threading.Lock()
        self._replies = {}

    def write(self, data: bytes) -> None:
        tid = threading.get_ident()
        request = pyon.decode(data.decode())
        with self._lock:
            self.events.append(("put", tid))
        time.sleep(0.002)
        reply = pyon.encode({"status": "ok", "data": request["args"]}) + "\n"
        with self._lock:
            self._replies[tid] = reply

    def readline(self) -> bytes:
        tid = threading.get_ident()
        time.sleep(0.002)
        with self._lock:
            self.events.append(("get", tid))
            reply = self._replies.pop(tid)
        return reply.encode()

    def assert_round_trips_were_atomic(self):
        assert len(self.events) % 2 == 0, f"unpaired IPC halves: {self.events!r}"
        for first, second in zip(self.events[::2], self.events[1::2]):
            assert first[0] == "put" and second[0] == "get" and first[1] == second[1], (
                "interleaved round trip - a thread's put/get pair was split by "
                f"another thread: {self.events!r}"
            )


@pytest.fixture
def worker(worker_main):
    """The shared ``worker_main`` topology fixture, wired to a recording pipe."""
    main, imported = worker_main
    main.ipc = _RecordingPipe()
    return main, imported


def _hammer(parent_action, thread_count=8, per_thread=15):
    errors = []

    def run(thread_id):
        try:
            for i in range(per_thread):
                payload = (thread_id, i, "x" * 200)
                assert tuple(parent_action(*payload)) == payload
        except Exception as e:  # noqa: BLE001 - collected and reported below
            errors.append(e)

    threads = [threading.Thread(target=run, args=(t,)) for t in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return errors


def test_lock_is_installed_on_the_module_the_worker_runs(worker):
    """The whole bug in one assertion: the wrapping must land on ``__main__``."""
    main, imported = worker
    assert main is not imported, "fixture failed to build two distinct modules"

    assert worker_ipc_lock._find_worker_module() is main

    install_worker_ipc_lock()

    assert main.ParentDatasetDB.get._qbutler_ipc_locked
    assert main.ParentDatasetDB.update._qbutler_ipc_locked
    assert main.CCB.issue._qbutler_ipc_locked
    assert main.Scheduler._check_pause._qbutler_ipc_locked


def test_patching_the_imported_copy_would_not_reach_the_worker(worker):
    """Guard against a return to the old approach.

    ``import artiq.master.worker_impl`` inside a worker yields a module whose
    globals nothing executing ever reads, so mutating it cannot serialise
    anything. If this ever fails, the two-module split has gone away and the
    lock could be installed the cheap way again.
    """
    main, imported = worker
    sentinel = object()
    imported.put_object = sentinel
    imported.get_object = sentinel

    assert main.put_object is not sentinel
    assert main.get_object is not sentinel
    assert main.__dict__ is not imported.__dict__


def test_concurrent_round_trips_are_atomic_once_locked(worker):
    """Threads hammering the pipe - MonitorMaster's executor threads running
    check_state while the main thread polls scheduler.check_pause() - must
    never have their put/get pairs split."""
    main, _ = worker
    install_worker_ipc_lock()

    errors = _hammer(main.ParentDatasetDB.get)

    assert not errors, f"thread(s) hit failed round trips: {errors!r}"
    main.ipc.assert_round_trips_were_atomic()


def test_unlocked_round_trips_do_interleave(worker):
    """Sanity check that the harness can actually detect the failure, so
    test_concurrent_round_trips_are_atomic_once_locked is not vacuous."""
    main, _ = worker

    _hammer(main.ParentDatasetDB.get)

    with pytest.raises(AssertionError, match="interleaved round trip"):
        main.ipc.assert_round_trips_were_atomic()


def test_monitor_controller_installs_the_lock_when_built(worker, build_experiment):
    """The failure that reached the rig (RIDs 79295/79297, 2026-07-29):
    MonitorMaster ran with no IPC lock at all, because make_monitor_controller
    only ever patched the imported copy of worker_impl. Building the controller
    must leave the live ``__main__`` module wrapped."""
    import example.example_monitor
    from example.example_monitor import MyMonitorMaster

    main, _ = worker
    assert not getattr(
        main.ParentDatasetDB.get, "_qbutler_ipc_locked", False
    ), "fixture invalid: the lock was already installed before build"

    build_experiment(MyMonitorMaster, experiment_file=example.example_monitor.__file__)

    assert main.ParentDatasetDB.get._qbutler_ipc_locked
    assert main.CCB.issue._qbutler_ipc_locked
    assert main.Scheduler._check_pause._qbutler_ipc_locked


def _make_env(dataset_mgr):
    env = types.SimpleNamespace()
    setattr(env, "_HasEnvironment__dataset_mgr", dataset_mgr)
    return env


def test_dataset_broadcaster_alias_is_locked(worker):
    """``DatasetManager.__init__`` captures ``ParentDatasetDB.update`` into
    ``_broadcaster.publish`` at worker startup, before any experiment code
    runs, so rebinding the class attribute cannot reach it. This alias is the
    ``set_dataset(broadcast=True)`` path that failed live as 'Could not publish
    calibration DAG'."""
    main, _ = worker
    dataset_mgr = main.DatasetManager(main.ParentDatasetDB)

    assert not getattr(
        dataset_mgr._broadcaster.publish, "_qbutler_ipc_locked", False
    ), "fixture invalid: the alias was already wrapped"

    install_worker_ipc_lock(_make_env(dataset_mgr))

    assert dataset_mgr._broadcaster.publish._qbutler_ipc_locked


def test_broadcast_set_dataset_serialises_against_other_parent_actions(worker):
    """The live failure shape: a monitor thread broadcasting a dataset while
    the main thread runs an unrelated parent action."""
    main, _ = worker
    dataset_mgr = main.DatasetManager(main.ParentDatasetDB)
    install_worker_ipc_lock(_make_env(dataset_mgr))

    errors = []

    def broadcast():
        try:
            for i in range(15):
                dataset_mgr.set("calibrations.dag", {"i": i}, {}, True, False, False)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=broadcast) for _ in range(4)]
    threads += [
        threading.Thread(target=lambda: _hammer(main.Scheduler._check_pause, 2, 15))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"broadcast thread(s) failed: {errors!r}"
    main.ipc.assert_round_trips_were_atomic()
