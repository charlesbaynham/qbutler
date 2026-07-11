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
"""

import logging
import threading
from collections import deque
from typing import Any
from typing import Callable
from typing import Hashable

logger = logging.getLogger(__name__)


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
