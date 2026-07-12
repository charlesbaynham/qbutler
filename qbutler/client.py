"""The physicist-facing entry point for a calibration-aware experiment.

Subclass :class:`CalibratedExpFragment`, attach the top calibration of your DAG,
and write a ``@kernel run_once``. Somewhere at a scan-point boundary, call
``self.recalibrate_if_needed()``: if a dependency has drifted, the experiment
transparently pauses, recalibrates on the host (running the precompiled per-node
kernels), and re-enters your kernel where it left off. Everything else — the
background precompilation, the escape/re-enter loop, teardown — is automatic.

Minimal client::

    from artiq.experiment import kernel
    from qbutler import CalibratedExpFragment

    class EnsureBlueMOT(CalibratedExpFragment):
        def build_fragment(self):
            self.setattr_device("core")
            self.setattr_calibration(BlueMOTCalibration)

        @kernel
        def run_once(self):
            self.recalibrate_if_needed()
            # ... science ...

The only thing to declare is the calibration. If you attach exactly one it is
found automatically; with several, set ``self.calibration_target`` to the top
one in ``build_fragment``.
"""

import logging

from artiq.experiment import TBool
from artiq.experiment import kernel
from artiq.experiment import rpc
from ndscan.experiment import ExpFragment
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
    """Call ``main`` (the precompiled science kernel), and whenever it raises
    :class:`~qbutler.calibration.CalibrationEscape` run ``fix`` and re-enter.

    Pure host-side control flow, factored out so it can be exercised with fake
    callables. ``main`` is retried after each fix; the fix is bounded so a
    non-converging calibration cannot loop forever.

    Args:
        main: zero-arg callable; the precompiled main kernel.
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
    (and ``device_cleanup()`` runs on every exit, escape included). Precompiled
    kernels also start every entry from their compile-time-baked attribute
    values (``attribute_writeback=False``), so a shot-to-shot first-run guard
    (e.g. ``self.first_run = False`` after initialising an oracle) RE-TRIGGERS
    on every kernel entry. This is deliberate and required: the calibration
    detour may have changed exactly what those persisted variables assume, so
    persisted-state initialisation must re-run after it.

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
        pool = getattr(self, "_cal_pool", None)
        if pool is not None:
            pool.shutdown()
        super().host_cleanup()

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

    @kernel
    def _calibrated_main(self):
        """The precompiled main kernel: the fragment's full lifecycle, so every
        (re-)entry restarts from the top — device_setup runs again after a
        calibration detour (the re-entry contract), and device_cleanup runs on
        every exit, escape included. Mirrors ndscan's _FragmentRunner._run."""
        self.device_setup()
        try:
            self.run_once()
        finally:
            self.device_cleanup()

    def run_calibrated(self):
        """Host driver: run the precompiled main kernel, recalibrating and
        re-entering whenever it escapes.

        This is the seam an experiment runner calls: the standalone entry point
        (:func:`make_calibrated_experiment`) and the ndscan scan wrap both drive
        the fragment through here. A physicist does not call it directly.
        """
        self._arm_calibration()
        main = self._precompiled_main()
        target = self._cal_target
        drive_with_recalibration(
            main,
            target.fix_state,
            self.max_recalibrations,
            describe=f" from {type(self).__name__}",
        )

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

        pool = PrecompilePool(self.core)
        self._cal_pool = pool
        # Seed the main kernel first — it runs before any escape, so it is the
        # first artifact needed.
        if is_kernel(self.run_once):
            pool.seed((self, "main"), self._calibrated_main)
        target.seed_precompile_pool(pool)
        self._cal_armed = True

    def _precompiled_main(self):
        if not is_kernel(self.run_once):
            raise TypeError(
                f"{type(self).__name__}.run_once must be a @kernel to be driven by "
                "run_calibrated(). Make it a @kernel, or run this fragment as a "
                "plain host experiment (the calibration walk still uses the "
                "precompiled node kernels)."
            )
        return self._cal_pool.get((self, "main"))

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


def make_calibrated_experiment(fragment_class):
    """Wrap a :class:`CalibratedExpFragment` as a runnable standalone experiment.

    The one module-level line that turns a client fragment into something the
    ARTIQ dashboard can schedule — the analogue of ndscan's
    ``make_fragment_scan_exp`` for the "ensure calibrated, then run once"
    case. It drives the fragment through :meth:`CalibratedExpFragment.run_calibrated`,
    so the escape/recalibrate/re-enter loop is applied automatically::

        EnsureBlueMOT = make_calibrated_experiment(EnsureBlueMOTFrag)

    The fragment tree is constructed in ``prepare()``, not ``build()``: the
    master gives the worker's build action an absolute 15 s budget
    (``Worker.build`` ``timeout=15.0``; the deadline is fixed when the action
    starts and worker↔master traffic does not extend it — rig RIDs
    77458/77459), while prepare has no deadline at all. A client whose
    construction is expensive — e.g. two calibration targets each building a
    full measurement chain — must therefore not build during the build action.
    """
    from artiq.experiment import EnvExperiment

    class _CalibratedExpFragmentRunner(EnvExperiment):
        def build(self):
            # Deliberately near-empty: see the 15 s build budget above. The
            # IPC transaction lock is installed here, the earliest worker
            # entry point, so it precedes any thread qbutler ever starts.
            install_worker_ipc_lock()

        def prepare(self):
            self.frag: CalibratedExpFragment = fragment_class(self, [])
            self.frag.init_params()
            self.frag.prepare()

        def run(self):
            try:
                self.frag.host_setup()
                self.frag.run_calibrated()
            finally:
                self.frag.host_cleanup()

    _CalibratedExpFragmentRunner.__name__ = fragment_class.__name__ + "Experiment"
    _CalibratedExpFragmentRunner.__qualname__ = _CalibratedExpFragmentRunner.__name__
    return _CalibratedExpFragmentRunner
