"""Regression tests for qbutler#24: MonitorMaster's ``patch_artiq_threading``
must serialise the *whole* put_object-then-get_object round trip of a
``parent_action`` call (see ``artiq.master.worker_impl.make_parent_action``),
not just one half of it.
"""

import threading
import time

import artiq.master.worker_impl as worker_impl

from qbutler.monitoring import patch_artiq_threading


class _RacyPipe:
    """Stand-in for ``sipyco.pipe_ipc.ChildComm``: a single shared byte stream
    where ``readline()`` returns whatever comes next up to the first newline,
    just like a real OS pipe. Every ``write()`` is split into two chunks with
    a sleep in between, widening the interleaving window the way a large
    pyon-encoded line (e.g. a ``calibrations.status``/``calibrations.dag``
    publish) does on a real pipe, so an unsynchronised concurrent writer's
    bytes can land in the middle of ours.
    """

    def __init__(self):
        self._buf = bytearray()
        self._cond = threading.Condition()

    def write(self, data: bytes) -> None:
        mid = len(data) // 2
        with self._cond:
            self._buf.extend(data[:mid])
        time.sleep(0.005)
        with self._cond:
            self._buf.extend(data[mid:])
            self._cond.notify_all()

    def readline(self) -> bytes:
        with self._cond:
            while b"\n" not in self._buf:
                self._cond.wait(timeout=2)
            idx = self._buf.index(b"\n") + 1
            line = bytes(self._buf[:idx])
            del self._buf[:idx]
            return line


def test_patch_artiq_threading_single_round_trip_does_not_raise(monkeypatch):
    """A lone put_object-then-get_object round trip (the shape every
    ``parent_action`` call takes) must not raise. This is the shape of the
    very first IPC call a worker process makes after the monkeypatch is
    installed, so it must work with no prior lock state."""
    monkeypatch.setattr(worker_impl, "ipc", _RacyPipe())
    patch_artiq_threading()

    request = {"action": "noop", "args": [], "kwargs": {}}
    worker_impl.put_object(request)
    reply = worker_impl.get_object()
    assert reply == request


def test_patch_artiq_threading_serialises_concurrent_round_trips(monkeypatch):
    """Many threads hammering put_object/get_object concurrently (as
    MonitorMaster's executor-thread-per-monitor check_state calls do) must
    never see a corrupted or mismatched round trip.

    Fails against the pre-fix implementation (lock acquired in get_object,
    released in put_object) because that leaves the actual write-then-read
    gap used by every real caller completely unlocked.
    """
    monkeypatch.setattr(worker_impl, "ipc", _RacyPipe())
    patch_artiq_threading()

    errors = []

    def hammer(thread_id):
        try:
            for i in range(20):
                request = {
                    "action": "noop",
                    "args": [thread_id, i, "x" * 200],
                    "kwargs": {},
                }
                worker_impl.put_object(request)
                reply = worker_impl.get_object()
                assert (
                    reply == request
                ), f"corrupted round trip: got {reply!r}, expected {request!r}"
        except Exception as e:  # noqa: BLE001 - collected and reported below
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"thread(s) hit corrupted/failed round trips: {errors!r}"
