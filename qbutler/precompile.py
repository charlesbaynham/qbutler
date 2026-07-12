"""Background precompilation of calibration kernels.

A calibration walk touches several kernels — each node's ``check_own_state``
and, for auto-fixed nodes, its resident optimizer loop — plus the client's main
science kernel. Compiling those on the core device costs ~16 s each, and the
plain host walk recompiles on every touch. :class:`PrecompilePool` compiles them
once, in a background thread, so the walk (and the escape/re-enter loop) deploys
a ready artifact (~0.24 s) instead.

The pool is deliberately dumb: it maps an opaque, hashable *key* to a
precompiled no-argument callable. Callers choose their own keys (qbutler uses
``(node, "check")`` / ``(node, "fix")``); the pool knows nothing about
calibrations. Compiles run sequentially on one worker thread, which is safe
against a concurrently-running kernel *only* for kernels with no subkernels
(``Core.precompile``'s compile path touches no ``comm`` state in that case). Every
calibration measurement kernel here is single-core, so this holds; CI guards it.

Thread-safety against the worker↔master pipe
--------------------------------------------

In an ARTIQ worker, ``ddb.get`` / dataset access are *parent actions*: an
unlocked write-request/read-reply pair on one line-JSON pipe
(``artiq/master/worker_impl.py`` ``put_object``/``get_object`` /
``make_parent_action``). The compiler hits this per quoted function —
``embedding.py`` ``_quote_function`` does ``self.dmgr.get(core_name)``, and
``DeviceManager.get`` re-fetches the description over the pipe on *every* call,
even for an already-active device. A background compile therefore interleaves
its request/reply lines with the main thread's dataset traffic and each thread
reads the other's reply (rig-observed as ``JSONDecodeError`` inside
``pyon.decode`` → ``DeviceError: Failed to get description of device 'core'``,
RID 77452).

The pool removes every pipe touch from the compile thread: at construction (on
the caller's thread) it snapshots the whole device db — one ``get_device_db``
transaction — and rebinds ``core.dmgr.ddb`` to a read-only
:class:`_SnapshotDeviceDB` serving ``get``/``get_desc`` locally (aliases
resolved against the snapshot). Device *instantiation* stays a main-thread
affair: a pool-thread lookup that is not already an active/virtual device
raises instead of creating one. Residual compile-thread pipe touches: none —
the compile path is pure compute plus the (now snapshot-served) desc lookups;
subkernel upload never runs (no-subkernel precondition above); worker log
records, including a multi-KB failure traceback from the pool thread, go to
stderr via the single lock-serialised ``StreamHandler`` installed by sipyco's
``multiline_log_config`` — a different fd from the IPC pipe, with no other
stderr writer to interleave with. Belt-and-braces, constructing a pool inside a
worker also installs :func:`qbutler.worker_ipc_lock.install_worker_ipc_lock`,
which makes every residual parent put+get pair atomic under one RLock.
"""

import logging
import threading
from collections import deque
from typing import Any
from typing import Callable
from typing import Hashable

from .worker_ipc_lock import install_worker_ipc_lock

logger = logging.getLogger(__name__)


class _SnapshotDeviceDB:
    """A read-only device db serving lookups from an in-process snapshot.

    Drop-in for the worker's ``ParentDeviceDB`` (and the master's ``DeviceDB``)
    on the read paths ``DeviceManager`` uses — ``get(key, resolve_alias=...)``,
    ``get_device_db()``, ``get_satellite_cpu_target`` — with no pipe IPC ever.
    """

    def __init__(self, data: dict):
        self._data = data

    def get_device_db(self) -> dict:
        return self._data

    def get(self, key, resolve_alias=False):
        try:
            desc = self._data[key]
        except KeyError:
            raise KeyError(
                f"Device '{key}' is not in the device-db snapshot taken at "
                "PrecompilePool construction; if the device db changed "
                "mid-run, rebuild the pool"
            ) from None
        if resolve_alias:
            while isinstance(desc, str):
                desc = self._data[desc]
        return desc

    def get_satellite_cpu_target(self, destination):
        return self._data["satellite_cpu_targets"][destination]


def _install_pipe_free_desc_lookups(core) -> None:
    """Rebind ``core.dmgr.ddb`` to a snapshot so desc lookups (from any thread)
    stop transacting on the worker↔master pipe.

    Idempotent; call on the thread that owns the pipe (the main thread). Also
    guards against a compile-thread lookup instantiating a device: devices are
    built on the main thread during build(), so a pool-thread ``dmgr.get`` must
    only ever find an existing active/virtual device.
    """
    dmgr = getattr(core, "dmgr", None)
    ddb = getattr(dmgr, "ddb", None)
    if ddb is None or isinstance(ddb, _SnapshotDeviceDB):
        return
    dmgr.ddb = _SnapshotDeviceDB(ddb.get_device_db())

    if getattr(dmgr, "_qbutler_thread_guard", False):
        return
    owner = threading.current_thread()
    orig_get = dmgr.get

    def guarded_get(name, *args, **kwargs):
        if threading.current_thread() is not owner:
            virtual = getattr(dmgr, "virtual_devices", {})
            active = getattr(dmgr, "active_devices", [])
            if name not in virtual:
                desc = dmgr.ddb.get(name, resolve_alias=True)
                if not any(desc == d for d, _ in active):
                    raise RuntimeError(
                        f"Device '{name}' is not active: a compile-thread "
                        "lookup would instantiate it. Devices must be created "
                        "on the main thread during build()"
                    )
        return orig_get(name, *args, **kwargs)

    dmgr.get = guarded_get
    dmgr._qbutler_thread_guard = True


class PrecompilePool:
    """Compile kernels in the background and hand back ready callables.

    Seed the pool with the kernels a submission will need (``seed``), then pull
    each one when you need it (``get``), blocking until its compile finishes.
    Compile errors surface on ``get`` with the original traceback. Seeding is
    idempotent, so re-seeding the same key is a no-op.

    Args:
        core: The ARTIQ ``core`` device (anything with a ``precompile`` method).
    """

    def __init__(self, core: Any):
        # Both must happen on the caller's (pipe-owning) thread, before the
        # worker thread exists: see "Thread-safety" in the module docstring.
        _install_pipe_free_desc_lookups(core)
        install_worker_ipc_lock()

        self._core = core
        self._specs: dict[Hashable, tuple] = {}
        self._results: dict[Hashable, Callable] = {}
        self._errors: dict[Hashable, BaseException] = {}
        self._queue: deque = deque()
        self._cv = threading.Condition()
        self._thread: threading.Thread = None
        self._shutdown = False

    def seed(self, key: Hashable, kernel_fn: Callable, *args, **kwargs) -> None:
        """Enqueue ``kernel_fn`` for background precompilation under ``key``.

        Returns immediately; the compile happens on the worker thread. Seeding a
        key that is already known does nothing (the first seed wins), so this is
        safe to call from an idempotent setup path.
        """
        with self._cv:
            if self._shutdown:
                raise RuntimeError("Cannot seed a PrecompilePool after shutdown()")
            if key in self._specs:
                return
            self._specs[key] = (kernel_fn, args, kwargs)
            self._queue.append(key)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._worker, name="qbutler-precompile", daemon=True
                )
                self._thread.start()
            self._cv.notify_all()

    def get(self, key: Hashable) -> Callable:
        """Return the precompiled callable for ``key``, blocking until ready.

        Raises:
            KeyError: if ``key`` was never seeded.
            Exception: whatever the compile raised, re-raised with its original
                traceback.
        """
        with self._cv:
            if key not in self._specs:
                raise KeyError(f"PrecompilePool has no seeded kernel for {key!r}")
            while key not in self._results and key not in self._errors:
                self._cv.wait()
            if key in self._errors:
                exc = self._errors[key]
                raise exc.with_traceback(exc.__traceback__)
            return self._results[key]

    def is_ready(self, key: Hashable) -> bool:
        """True if ``key``'s compile has finished (successfully or not)."""
        with self._cv:
            return key in self._results or key in self._errors

    def drain(self) -> None:
        """Block until every seeded kernel has finished compiling."""
        with self._cv:
            while len(self._results) + len(self._errors) < len(self._specs):
                self._cv.wait()

    def shutdown(self, wait: bool = True) -> None:
        """Stop the worker thread. Idempotent; call at teardown.

        Args:
            wait: join the worker (letting the in-flight compile finish) before
                returning.
        """
        with self._cv:
            if self._shutdown:
                return
            self._shutdown = True
            self._cv.notify_all()
            thread = self._thread
        if wait and thread is not None:
            thread.join()

    def _worker(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._shutdown:
                    self._cv.wait()
                if not self._queue:
                    return
                key = self._queue.popleft()
                kernel_fn, args, kwargs = self._specs[key]

            try:
                result = self._core.precompile(kernel_fn, *args, **kwargs)
                error = None
            except BaseException as exc:  # noqa: BLE001 - surfaced on get()
                logger.warning("Precompile of %r failed", key, exc_info=True)
                result, error = None, exc

            with self._cv:
                if error is None:
                    self._results[key] = result
                else:
                    self._errors[key] = error
                self._cv.notify_all()
