"""The physicist-facing entry point for a calibration-aware experiment.

Subclass :class:`CalibratedExpFragment`, attach the top calibration of your DAG,
and write a ``@kernel run_once``. Somewhere at a scan-point boundary, call
``self.recalibrate_if_needed()``: if a dependency has drifted, the experiment
transparently pauses, recalibrates on the host (running the precompiled per-node
kernels), and re-enters your kernel. Everything else — the background
precompilation, the escape/re-enter loop, teardown — is automatic.

ndscan is a mandatory, load-bearing part of this: the wrapper returned by
:func:`make_calibrated_experiment` is an ndscan ``FragmentScanExperiment``, so
every parameter of the fragment tree is visible and overridable from the
dashboard argument editor, exactly like any other ndscan experiment.

Minimal client::

    from artiq.experiment import kernel
    from qbutler import CalibratedExpFragment, make_calibrated_experiment

    class EnsureBlueMOTFrag(CalibratedExpFragment):
        def build_fragment(self):
            self.setattr_device("core")
            self.setattr_calibration(BlueMOTCalibration)

        @kernel
        def run_once(self):
            self.recalibrate_if_needed()
            # ... science ...

    EnsureBlueMOT = make_calibrated_experiment(EnsureBlueMOTFrag)

The only thing to declare is the calibration. If you attach exactly one it is
found automatically; with several, set ``self.calibration_target`` to the top
one in ``build_fragment``.
"""

import logging
from contextlib import suppress

from artiq.experiment import TBool
from artiq.experiment import TFloat
from artiq.experiment import TList
from artiq.experiment import kernel
from artiq.experiment import rpc
from artiq.language.core import TerminationRequested
from ndscan.experiment import ExpFragment
from ndscan.experiment.entry_point import FragmentScanExperiment
from ndscan.experiment.entry_point import get_class_pretty_name
from ndscan.experiment.parameters import FloatParamStore
from ndscan.experiment.parameters import ParamHandle
from ndscan.experiment.utils import is_kernel

from . import dag
from .calibration import Calibration
from .calibration import CalibrationError
from .calibration import CalibrationEscape
from .calibration import CalibrationResult
from .precompile import PrecompilePool
from .worker_ipc_lock import install_worker_ipc_lock

logger = logging.getLogger(__name__)


def drive_with_recalibration(main, fix, max_recalibrations, describe=""):
    """Call ``main`` (the experiment's execution), and whenever it raises
    :class:`~qbutler.calibration.CalibrationEscape` run ``fix`` and re-enter.

    Pure host-side control flow, factored out so it can be exercised with fake
    callables. ``main`` is retried after each fix; the fix is bounded so a
    non-converging calibration cannot loop forever.

    Args:
        main: zero-arg callable running the experiment (e.g. ndscan's
            ``TopLevelRunner.run``).
        fix: zero-arg callable; runs the DAG fix walk (``target.fix_state``).
        max_recalibrations: max escape→fix cycles before giving up.
        describe: label used in the give-up error message.

    Raises:
        CalibrationError: if it is still escaping after ``max_recalibrations``.
    """
    for _ in range(max_recalibrations + 1):
        try:
            main()
            return
        except CalibrationEscape:
            logger.info("Calibration escape%s: fixing and re-entering", describe)
            fix()
    raise CalibrationError(
        f"{describe or 'Experiment'} escaped for recalibration more than "
        f"{max_recalibrations} times without settling. A calibration is not "
        "converging: check the failing node's optimizer bounds and measurement, "
        "or raise max_recalibrations if this is expected."
    )


class CalibratedExpFragment(ExpFragment):
    """An ``ExpFragment`` that keeps its calibration DAG healthy while it runs.

    Named for what it is: this subclasses an ndscan *Fragment* — it is NOT an
    ARTIQ ``Experiment``, and cannot be scheduled directly. Wrap it with
    :func:`make_calibrated_experiment` (whose output *is* an experiment) or
    drive it from another fragment.

    Override ``build_fragment`` (attach a calibration) and ``run_once`` (your
    science, ideally a ``@kernel``). Call :meth:`recalibrate_if_needed` from the
    kernel wherever a recalibration would be safe.

    **Re-entry contract (guaranteed semantics).** After a
    :class:`~qbutler.calibration.CalibrationEscape`, re-entry restarts the main
    kernel FROM THE TOP: ``device_setup()`` runs again, then ``run_once()``
    (and ``device_cleanup()`` runs on every exit, escape included — ndscan's
    fragment lifecycle guarantees this). Attribute writes a kernel makes
    before escaping are never written back to the host, so a shot-to-shot
    first-run guard (e.g. ``self.first_run = False`` after initialising an
    oracle) RE-TRIGGERS on re-entry. This is deliberate and required: the
    calibration detour may have changed exactly what those persisted variables
    assume, so persisted-state initialisation must re-run after it.

    Class attributes you may override:
        calibration_target: The top :class:`~qbutler.calibration.Calibration` of
            the DAG. Leave ``None`` to auto-discover the single attached
            calibration.
        max_recalibrations: How many escape/recalibrate cycles to allow before
            giving up (guards against a non-converging calibration looping
            forever). Default 20.
    """

    calibration_target: Calibration = None
    max_recalibrations: int = 20

    def host_setup(self):
        super().host_setup()
        self._arm_calibration()

    def host_cleanup(self):
        # The pool deliberately survives host_cleanup: ndscan tears the
        # fragment down and re-sets it up around every pause and every escape
        # re-entry, and the fix walk between those needs the compiled
        # artifacts. Final teardown is _shutdown_calibration(), called by the
        # wrapper when the experiment finishes.
        super().host_cleanup()

    def _shutdown_calibration(self):
        pool = getattr(self, "_cal_pool", None)
        if pool is not None:
            pool.shutdown()

    @kernel
    def recalibrate_if_needed(self):
        """From the kernel: recalibrate the DAG if anything has drifted.

        Cheap when everything is healthy (one RPC that only consults cached
        check state, no hardware). If a dependency looks bad it raises
        :class:`~qbutler.calibration.CalibrationEscape`, unwinding to the host,
        which fixes the DAG and re-enters this kernel. Put it at a point where
        interrupting and restarting the kernel is safe — typically a scan-point
        boundary, not mid-shot.
        """
        if self._needs_recalibration():
            raise CalibrationEscape("a calibration dependency needs recalibrating")

    def _ensure_cal_pool(self) -> PrecompilePool:
        pool = getattr(self, "_cal_pool", None)
        if pool is None:
            pool = PrecompilePool(self.core)
            self._cal_pool = pool
        return pool

    def _arm_calibration(self):
        if getattr(self, "_cal_armed", False):
            return
        target = self._resolve_target()
        self._cal_target = target

        # Each node needs its own host_setup before its kernel can compile (it
        # arms the measurement the kernel reads). The target is attached, so
        # super().host_setup() already set it up; its dependencies are detached,
        # so set them up here.
        deps = dag.get_dependencies(target)
        for node in deps:
            if node is not target:
                node.host_setup()

        target.seed_precompile_pool(self._ensure_cal_pool())
        self._cal_armed = True

    def _resolve_target(self) -> Calibration:
        if self.calibration_target is not None:
            return self.calibration_target

        seen = []
        for value in vars(self).values():
            if isinstance(value, Calibration) and value not in seen:
                seen.append(value)

        if len(seen) == 1:
            return seen[0]
        if not seen:
            raise CalibrationError(
                f"{type(self).__name__} has no calibration attached. Attach one "
                "with self.setattr_calibration(YourCalibration) in "
                "build_fragment(), or set self.calibration_target."
            )
        names = ", ".join(type(c).__name__ for c in seen)
        raise CalibrationError(
            f"{type(self).__name__} has several calibrations ({names}); set "
            "self.calibration_target = self.<top calibration> in build_fragment() "
            "so the client knows which DAG to maintain."
        )

    @rpc
    def _needs_recalibration(self) -> TBool:
        target = getattr(self, "_cal_target", None) or self._resolve_target()
        for node in dag.get_dependencies(target):
            if node._guess_own_state() != CalibrationResult.OK:
                return True
        return False


def _collect_float_stores(fragment) -> list:
    """Every FloatParamStore bound to a handle in the attached fragment tree.

    These are the stores the main kernel can read; the precompiled entry
    refreshes exactly this list on-core at each (re-)entry. Detached
    subfragments (the calibration nodes) are excluded — their kernels are
    separate pool artifacts with their own refresh
    (:meth:`~qbutler.calibration.Calibration._check_with_current_params`).
    Float only: committed calibration values are FloatParams
    (``setattr_param_optimizable`` supports nothing else), and float params
    are also the ones that pick up dataset defaults a fix rewrites.
    """
    stores = []
    seen = set()

    def visit(frag):
        for value in vars(frag).values():
            if isinstance(value, ParamHandle):
                store = getattr(value, "_store", None)
                if isinstance(store, FloatParamStore) and id(store) not in seen:
                    seen.add(id(store))
                    stores.append(store)
        for sub in frag._subfragments:
            if sub in frag._detached_subfragments:
                continue
            visit(sub)

    visit(fragment)
    return stores


class _PrecompiledContinuousEntry:
    """A precompiled stand-in for ndscan's ``TopLevelRunner._run_continuous_kernel``.

    Bound as an instance attribute on the runner, so ``_run_continuous``'s
    ``self._run_continuous_kernel()`` (entry_point.py) deploys a ready
    artifact (~0.24 s) instead of recompiling the kernel entry (~16 s) on
    every escape re-entry.

    Parameter freshness: ndscan already refreshes the *host* stores from
    datasets on every re-entry (``recompute_param_defaults`` at the top of
    ``_run_continuous``), but a precompiled kernel's embedded store copies
    stay at their compile-time values. So the entry kernel first pulls the
    current host values over one RPC and applies them on-core via the stores'
    ``@portable set_value`` — the same mechanism ndscan's kernel scans use to
    apply per-point values (``scan_runner.py`` ``param_store.set_value``) —
    then runs ndscan's own ``_continuous_loop``.

    One deliberate semantic shift versus the recompiling path: the loop's
    transitory-error/underflow counters restart from their compile-time zeros
    on each entry, so the retry budget is per-entry rather than cumulative.
    """

    def __init__(self, tlr, pool):
        self.core = tlr.core
        self.tlr = tlr
        self._pool = pool
        self._float_stores = _collect_float_stores(tlr.fragment)
        self._key = (self, "main")
        # _continuous_loop reads/writes these, but ndscan only creates them
        # when _run_continuous starts — after our background compile may have
        # embedded the runner. Pre-create them so the compile can type them
        # (ndscan re-initialises them itself before every run).
        tlr._point_phase = False
        tlr.num_current_transitory_errors = 0
        tlr.num_current_underflows = 0
        # The ARTIQ compiler cannot type an empty embedded list; skip the
        # refresh machinery entirely for a param-less tree.
        entry = self._entry if self._float_stores else self._entry_no_params
        pool.seed(self._key, entry)

    def __call__(self):
        return self._pool.get(self._key)()

    @rpc
    def _current_values(self) -> TList(TFloat):
        return [float(store.get_value()) for store in self._float_stores]

    @kernel
    def _entry(self):
        self.core.reset()
        values = self._current_values()
        for i in range(len(self._float_stores)):
            self._float_stores[i].set_value(values[i])
        return self.tlr._continuous_loop()

    @kernel
    def _entry_no_params(self):
        self.core.reset()
        return self.tlr._continuous_loop()


def make_calibrated_experiment(
    fragment_class,
    *args,
    max_rtio_underflow_retries: int = 3,
    max_transitory_error_retries: int = 10,
):
    """Wrap a :class:`CalibratedExpFragment` as an ndscan scan experiment.

    The one module-level line that turns a client fragment into something the
    ARTIQ dashboard can schedule — the calibrated analogue of ndscan's
    ``make_fragment_scan_exp``. It returns a ``FragmentScanExperiment``
    subclass, so the wrapped client gets the full ndscan argument UI: the
    PARAMS schema for every parameter in the fragment tree, the dashboard
    override editor, and ndscan's parameter processing. The
    escape/recalibrate/re-enter loop wraps ndscan's execution: on
    :class:`~qbutler.calibration.CalibrationEscape` the host fixes the DAG
    (precompiled node kernels) and re-enters ndscan's run loop from the top::

        EnsureBlueMOT = make_calibrated_experiment(EnsureBlueMOTFrag)

    Submitting with scan axes raises ``NotImplementedError`` for now: the
    escape/re-entry semantics at scan-point boundaries are the F6 follow-up.
    The non-scanned path (single / repeat / time series) is fully supported.

    ndscan needs the fragment tree at ``build()`` time for the argument UI,
    and the ARTIQ master gives the worker's build action an absolute 15 s
    budget (``Worker.build`` ``timeout=15.0``, ``artiq/master/worker.py``; the
    deadline is fixed at action start and is not extended by traffic). A
    client whose tree construction exceeds that budget will therefore be
    killed at submission until fragment building is made lazy (the
    declarative-LMT laziness work, tracked separately).
    """

    class _CalibratedScanShim(FragmentScanExperiment):
        def build(self):
            # Earliest worker entry point: the IPC transaction lock must
            # precede any thread qbutler ever starts.
            install_worker_ipc_lock()
            super().build(
                lambda: fragment_class(self, [], *args),
                max_rtio_underflow_retries=max_rtio_underflow_retries,
                max_transitory_error_retries=max_transitory_error_retries,
            )

        def prepare(self):
            super().prepare()
            fragment = self.fragment
            scanned = self.tlr.spec.axes and not self.tlr._is_time_series
            if is_kernel(fragment.run_once) and not scanned:
                # Seed the main kernel into the pool FIRST (node kernels are
                # seeded later, at host_setup) so the background compile that
                # the first entry blocks on is the main one, and node compiles
                # overlap the first run.
                self.tlr._run_continuous_kernel = _PrecompiledContinuousEntry(
                    self.tlr, fragment._ensure_cal_pool()
                )

        def run(self):
            if self.tlr.spec.axes and not self.tlr._is_time_series:
                raise NotImplementedError(
                    "Scanning a CalibratedExpFragment is not supported yet: "
                    "escape/recalibrate re-entry at scan-point boundaries is "
                    "the F6 follow-up. Submit without scan axes, or scan the "
                    "bare fragment via make_fragment_scan_exp (no mid-run "
                    "recalibration)."
                )
            fragment: CalibratedExpFragment = self.fragment
            target = fragment._resolve_target()
            name = get_class_pretty_name(fragment.__class__)
            self.tlr.create_applet(title=f"{name} ({fragment.fqn})")
            try:
                with suppress(TerminationRequested):
                    drive_with_recalibration(
                        self.tlr.run,
                        target.fix_state,
                        fragment.max_recalibrations,
                        describe=f" from {type(fragment).__name__}",
                    )
            finally:
                fragment._shutdown_calibration()

    _CalibratedScanShim.__name__ = fragment_class.__name__
    _CalibratedScanShim.__doc__ = fragment_class.__doc__
    return _CalibratedScanShim
