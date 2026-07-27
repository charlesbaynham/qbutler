"""The belt-and-braces IPC lock: parent-action put+get pairs become atomic."""

import threading
import types

from qbutler import worker_ipc_lock
from qbutler.worker_ipc_lock import _wrap_parent_actions


def _make_parent_action_like(pair_log, name_tag):
    def parent_action(*args, **kwargs):
        # Mimic the non-atomic put/get pair: record entry and exit separately
        # so an interleaving from another thread is visible between them.
        pair_log.append(("put", name_tag))
        threading.Event().wait(0.001)
        pair_log.append(("get", name_tag))
        return name_tag

    return parent_action


def _make_fake_worker_module(pair_log):
    module = types.ModuleType("fake_worker")
    module.make_parent_action = lambda action: None
    module.ipc = object()
    module.register_experiment = _make_parent_action_like(pair_log, "register")

    class ParentDeviceDB:
        get = _make_parent_action_like(pair_log, "get_device")
        get_device_db = _make_parent_action_like(pair_log, "get_device_db")

    class Scheduler:
        pause_noexc = staticmethod(_make_parent_action_like(pair_log, "pause"))

    module.ParentDeviceDB = ParentDeviceDB
    module.Scheduler = Scheduler
    return module


def test_wrap_covers_module_class_and_staticmethod_actions():
    log = []
    module = _make_fake_worker_module(log)
    assert _wrap_parent_actions(module) == 4
    # Idempotent: nothing left to wrap.
    assert _wrap_parent_actions(module) == 0

    # Wrapped callables still work through every access path.
    assert module.register_experiment() == "register"
    assert module.ParentDeviceDB.get() == "get_device"
    assert module.Scheduler.pause_noexc() == "pause"


def test_wrapped_pairs_are_atomic_across_threads():
    log = []
    module = _make_fake_worker_module(log)
    _wrap_parent_actions(module)

    start = threading.Event()

    def hammer(fn, n):
        start.wait()
        for _ in range(n):
            fn()

    threads = [
        threading.Thread(target=hammer, args=(module.ParentDeviceDB.get, 50)),
        threading.Thread(target=hammer, args=(module.register_experiment, 50)),
    ]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    # Under the RLock, every put is immediately followed by its own get.
    assert len(log) == 200
    for put, get in zip(log[::2], log[1::2]):
        assert put[0] == "put" and get[0] == "get"
        assert put[1] == get[1], "a foreign transaction interleaved a pair"


def test_install_is_noop_outside_a_worker():
    # pytest's __main__ is not worker_impl; must not raise and must latch.
    worker_ipc_lock.install_worker_ipc_lock()
    assert worker_ipc_lock._installed
    worker_ipc_lock.install_worker_ipc_lock()
