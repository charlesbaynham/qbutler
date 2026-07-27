"""Make ARTIQ worker↔master parent transactions atomic across threads.

Every parent action in ``artiq/master/worker_impl.py`` is an unlocked pair on
one shared line-JSON pipe: ``put_object(request)`` then ``reply = get_object()``
(``make_parent_action``). Two threads transacting concurrently interleave their
lines and each reads the other's reply. :class:`~qbutler.precompile.PrecompilePool`
already keeps its compile thread off the pipe entirely (device-db snapshot);
this module is the belt-and-braces layer: one RLock wrapped around every
parent-action *pair*, so any residual or future cross-thread transaction
serialises instead of corrupting the stream.

The choke point: ``make_parent_action`` closures are the only writers of
request lines and the only readers of reply lines (the worker main loop's
fire-and-forget ``put_object`` replies to master-initiated requests run on the
main thread between experiments, never concurrently with a running pool).
Wrapping each stored parent-action function — they all share
``__name__ == "parent_action"`` — makes each put+get pair atomic without
touching the fire-and-forget puts, so nothing can deadlock waiting for a reply
that never comes.

The worker runs ``worker_impl`` as ``__main__`` (spawned with
``-m artiq.master.worker_impl``), so the live module is found in
``sys.modules`` rather than imported — importing it here would execute a
second, dead copy.
"""

import inspect
import logging
import sys
import threading
from functools import wraps

logger = logging.getLogger(__name__)

_ipc_lock = threading.RLock()
_installed = False


def install_worker_ipc_lock() -> None:
    """Serialise all worker→master parent transactions under one RLock.

    Idempotent. A no-op outside an ARTIQ worker (tests, ``artiq_run``, the
    master process itself).
    """
    global _installed
    if _installed:
        return
    module = _find_worker_module()
    if module is not None:
        count = _wrap_parent_actions(module)
        logger.debug("Locked %d worker parent actions", count)
    _installed = True


def _find_worker_module():
    for name in ("__main__", "artiq.master.worker_impl"):
        module = sys.modules.get(name)
        if (
            module is not None
            and hasattr(module, "make_parent_action")
            and hasattr(module, "ipc")
        ):
            return module
    return None


def _locked(fn):
    @wraps(fn)
    def locked_parent_action(*args, **kwargs):
        with _ipc_lock:
            return fn(*args, **kwargs)

    locked_parent_action._qbutler_ipc_locked = True
    return locked_parent_action


def _is_unwrapped_parent_action(fn):
    return (
        callable(fn)
        and getattr(fn, "__name__", "") == "parent_action"
        and not getattr(fn, "_qbutler_ipc_locked", False)
    )


def _wrap_parent_actions(module) -> int:
    count = 0
    for name, value in list(vars(module).items()):
        if _is_unwrapped_parent_action(value):
            setattr(module, name, _locked(value))
            count += 1
        elif inspect.isclass(value):
            for attr, cell in list(vars(value).items()):
                fn = cell.__func__ if isinstance(cell, staticmethod) else cell
                if _is_unwrapped_parent_action(fn):
                    wrapped = _locked(fn)
                    if isinstance(cell, staticmethod):
                        wrapped = staticmethod(wrapped)
                    setattr(value, attr, wrapped)
                    count += 1
    return count
