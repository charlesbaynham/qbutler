"""Threading semantics of :class:`qbutler.precompile.PrecompilePool`.

Pure host-side tests with a fake core, so they need neither ARTIQ nor the
emulator. The fake ``precompile`` returns a callable that just invokes the
seeded function, which is enough to exercise block-until-ready, exception
propagation, idempotence and sequential compilation.
"""

import threading
import time

import pytest

from qbutler.precompile import PrecompilePool


class FakeCore:
    """Stand-in core whose ``precompile`` optionally sleeps (to model compile
    latency) and records the order in which kernels were compiled."""

    def __init__(self, compile_delay=0.0, fail=None):
        self.compile_delay = compile_delay
        self.fail = fail or set()
        self.compiled = []
        self._lock = threading.Lock()

    def precompile(self, fn, *args, **kwargs):
        if self.compile_delay:
            time.sleep(self.compile_delay)
        with self._lock:
            self.compiled.append(fn)
        if fn in self.fail:
            raise ValueError("boom compiling")
        return lambda: fn(*args, **kwargs)


def test_get_returns_working_callable():
    core = FakeCore()
    pool = PrecompilePool(core)
    pool.seed("k", lambda: 42)
    assert pool.get("k")() == 42
    pool.shutdown()


def test_get_blocks_until_ready():
    core = FakeCore(compile_delay=0.3)
    pool = PrecompilePool(core)
    pool.seed("k", lambda: 7)

    assert not pool.is_ready("k")
    t0 = time.monotonic()
    result = pool.get("k")()
    elapsed = time.monotonic() - t0

    assert result == 7
    assert elapsed >= 0.25, "get() should have blocked on the compile"
    assert pool.is_ready("k")
    pool.shutdown()


def test_exception_propagates_on_get():
    boom = lambda: None
    core = FakeCore(fail={boom})
    pool = PrecompilePool(core)
    pool.seed("bad", boom)

    with pytest.raises(ValueError, match="boom compiling") as exc_info:
        pool.get("bad")
    assert exc_info.value.__traceback__ is not None
    pool.shutdown()


def test_one_failure_does_not_block_others():
    boom = lambda: None
    good = lambda: "ok"
    core = FakeCore(fail={boom})
    pool = PrecompilePool(core)
    pool.seed("bad", boom)
    pool.seed("good", good)

    with pytest.raises(ValueError):
        pool.get("bad")
    assert pool.get("good")() == "ok"
    pool.shutdown()


def test_idempotent_seed():
    first = lambda: "first"
    second = lambda: "second"
    core = FakeCore()
    pool = PrecompilePool(core)
    pool.seed("k", first)
    pool.seed("k", second)  # ignored: first seed wins
    pool.drain()

    assert pool.get("k")() == "first"
    assert core.compiled == [first]
    pool.shutdown()


def test_get_unknown_key_raises_keyerror():
    pool = PrecompilePool(FakeCore())
    with pytest.raises(KeyError):
        pool.get("never-seeded")
    pool.shutdown()


def test_sequential_single_thread_preserves_order():
    core = FakeCore(compile_delay=0.02)
    pool = PrecompilePool(core)
    fns = [lambda i=i: i for i in range(5)]
    for i, fn in enumerate(fns):
        pool.seed(i, fn)
    pool.drain()

    assert core.compiled == fns, "one worker thread compiles in seed order"
    for i in range(5):
        assert pool.get(i)() == i
    pool.shutdown()


def test_drain_waits_for_all():
    core = FakeCore(compile_delay=0.05)
    pool = PrecompilePool(core)
    for i in range(4):
        pool.seed(i, lambda i=i: i)
    pool.drain()
    for i in range(4):
        assert pool.is_ready(i)
    pool.shutdown()


def test_shutdown_idempotent_and_blocks_seed():
    pool = PrecompilePool(FakeCore())
    pool.seed("k", lambda: 1)
    pool.drain()
    pool.shutdown()
    pool.shutdown()  # no error second time
    with pytest.raises(RuntimeError):
        pool.seed("late", lambda: 2)
