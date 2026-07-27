"""Regression for the worker↔master pipe race (rig RID 77452).

In an ARTIQ worker, parent transactions are unlocked put/get pairs on one
line-JSON pipe; the compile path does a pipe-backed desc lookup per quoted
function, so a background compile interleaved with main-thread dataset traffic
made each thread read the other's reply (JSONDecodeError → DeviceError).

These tests rebuild that plumbing in miniature: an unlocked request/reply line
channel exactly like worker_impl's, a pipe-backed device db, and a fake core
whose ``precompile`` does the compiler's ``dmgr.get("core")``. The control test
shows the harness detects the race (compile thread transacting directly on the
pipe → cross-talk); the fix test shows a PrecompilePool-armed core keeps the
compile thread entirely off the pipe while the main thread hammers it.
"""

import json
import queue
import random
import threading
import time

import pytest

from qbutler.precompile import PrecompilePool
from qbutler.precompile import _SnapshotDeviceDB

DEVICE_DB = {
    "core": {"class": "Core", "arguments": {"host": None}},
    "core_alias": "core",
    "alias_chain": "core_alias",
}


class UnlockedLinePipe:
    """worker_impl's put_object/get_object in miniature: one request line
    channel, one reply line channel, a server thread answering in order, and —
    deliberately — no locking around the put+get pair, so two client threads
    with outstanding requests can steal each other's replies."""

    def __init__(self):
        self._requests = queue.Queue()
        self._replies = queue.Queue()
        self.transactions = []  # (thread_name, request_id, reply_id)
        self._server = threading.Thread(target=self._serve, daemon=True)
        self._server.start()

    def _serve(self):
        while True:
            line = self._requests.get()
            if line is None:
                return
            request = json.loads(line)
            time.sleep(0.0005)  # widen the race window
            self._replies.put(json.dumps({"id": request["id"], "status": "ok"}))

    def transact(self, request_id):
        self._requests.put(json.dumps({"id": request_id}))  # put_object
        # The real put_object/get_object pair is not atomic: another thread can
        # transact in the gap and read this thread's reply first.
        time.sleep(random.random() * 0.002)
        reply = json.loads(self._replies.get())  # get_object
        self.transactions.append(
            (threading.current_thread().name, request_id, reply["id"])
        )
        return reply

    def stop(self):
        self._requests.put(None)

    def mismatches(self):
        return [t for t in self.transactions if t[1] != t[2]]

    def threads_seen(self):
        return {t[0] for t in self.transactions}


class PipeBackedDDB:
    """ParentDeviceDB in miniature: every get/get_device_db is a pipe pair."""

    def __init__(self, pipe):
        self._pipe = pipe
        self._n = 0

    def _transact(self):
        self._n += 1
        self._pipe.transact(f"{threading.current_thread().name}-ddb-{self._n}")

    def get_device_db(self):
        self._transact()
        return dict(DEVICE_DB)

    def get(self, key, resolve_alias=False):
        self._transact()
        desc = DEVICE_DB[key]
        if resolve_alias:
            while isinstance(desc, str):
                desc = DEVICE_DB[desc]
        return desc


class FakeDmgr:
    """DeviceManager.get in miniature: desc over the ddb on every call."""

    def __init__(self, ddb):
        self.ddb = ddb
        self.virtual_devices = {}
        self.active_devices = []

    def get_desc(self, name):
        return self.ddb.get(name, resolve_alias=True)

    def get(self, name):
        desc = self.get_desc(name)
        for existing_desc, existing_dev in self.active_devices:
            if desc == existing_desc:
                return existing_dev
        dev = object()
        self.active_devices.append((desc, dev))
        return dev


class FakeCore:
    """precompile does what the Stitcher does: a dmgr.get per quoted core."""

    def __init__(self, dmgr):
        self.dmgr = dmgr

    def precompile(self, fn, *args, **kwargs):
        self.dmgr.get("core")
        return lambda: fn(*args, **kwargs)


def _hammer_datasets(pipe, n, start: threading.Event):
    start.wait()
    for i in range(n):
        pipe.transact(f"{threading.current_thread().name}-dataset-{i}")


def test_control_unlocked_pipe_cross_talks():
    """The harness detects the bug: a compile-style thread transacting on the
    pipe concurrently with main-thread dataset traffic reads wrong replies."""
    pipe = UnlockedLinePipe()
    ddb = PipeBackedDDB(pipe)
    dmgr = FakeDmgr(ddb)
    start = threading.Event()

    def compile_thread():
        start.wait()
        for _ in range(300):
            try:
                dmgr.get("core")  # pipe-backed desc lookup, like the old pool
            except Exception:
                pass  # decode-equivalent breakage counts as cross-talk too

    worker = threading.Thread(target=compile_thread, name="pool-compile")
    main = threading.Thread(
        target=_hammer_datasets, args=(pipe, 300, start), name="main-datasets"
    )
    worker.start()
    main.start()
    start.set()
    worker.join()
    main.join()
    pipe.stop()

    assert pipe.mismatches(), (
        "expected the unlocked pipe to cross-talk under concurrent "
        "transactions; the harness would not detect a regression"
    )


def test_pool_keeps_compile_thread_off_the_pipe():
    """With the fix: pool construction snapshots the device db (one main-thread
    transaction); compiles then run desc lookups from the snapshot, so the
    compile thread never touches the pipe and the main thread's concurrent
    dataset traffic sees zero cross-talk."""
    pipe = UnlockedLinePipe()
    core = FakeCore(FakeDmgr(PipeBackedDDB(pipe)))
    start = threading.Event()

    pool = PrecompilePool(core)  # snapshots on this (main) thread
    # Activate 'core' on the main thread, as build() does in a real fragment
    # (desc now comes from the snapshot, so this is pipe-free too).
    core.dmgr.get("core")

    main = threading.Thread(
        target=_hammer_datasets, args=(pipe, 300, start), name="main-datasets"
    )
    main.start()
    start.set()
    for i in range(100):
        pool.seed(i, lambda i=i: i)
    pool.drain()
    main.join()
    pool.shutdown()
    pipe.stop()

    for i in range(100):
        assert pool.get(i)() == i
    assert not pipe.mismatches(), "cross-talk despite the snapshot fix"
    # Every pipe transaction came from the main thread; the single ddb one is
    # the snapshot taken at pool construction.
    assert pipe.threads_seen() <= {"MainThread", "main-datasets"}
    ddb_transactions = [t for t in pipe.transactions if "-ddb-" in t[1]]
    assert len(ddb_transactions) == 1
    assert ddb_transactions[0][0] == "MainThread"


def test_snapshot_resolves_aliases_locally():
    snap = _SnapshotDeviceDB(dict(DEVICE_DB))
    assert snap.get("alias_chain", resolve_alias=True) == DEVICE_DB["core"]
    assert snap.get("core_alias") == "core"
    with pytest.raises(KeyError, match="snapshot"):
        snap.get("nonexistent")


def test_compile_thread_cannot_instantiate_devices():
    """A pool-thread lookup of a never-activated device raises instead of
    creating it (device instantiation is main-thread-only)."""
    pipe = UnlockedLinePipe()
    dmgr = FakeDmgr(PipeBackedDDB(pipe))
    core = FakeCore(dmgr)
    PrecompilePool(core).shutdown()  # arms snapshot + guard

    errors = []

    def lookup():
        try:
            dmgr.get("core")
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=lookup)
    thread.start()
    thread.join()
    pipe.stop()

    assert len(errors) == 1
    assert "would instantiate" in str(errors[0])
    # The main thread may still instantiate it, and afterwards the pool thread
    # may look it up.
    dmgr.get("core")
    thread2_result = []
    thread2 = threading.Thread(target=lambda: thread2_result.append(dmgr.get("core")))
    thread2.start()
    thread2.join()
    assert thread2_result
