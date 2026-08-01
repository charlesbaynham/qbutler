import logging
import threading
from contextlib import contextmanager
from enum import Flag
from enum import auto
from time import time
from typing import Any
from typing import Callable
from typing import Generator
from typing import Optional
from typing import Tuple
from typing import Type

from artiq.experiment import TFloat
from artiq.experiment import TInt32
from artiq.experiment import TList
from artiq.experiment import kernel
from artiq.experiment import rpc
from ndscan.experiment import ExpFragment
from ndscan.experiment import OpaqueChannel
from ndscan.experiment.fragment import RestartKernelTransitoryError
from ndscan.experiment.fragment import TransitoryError
from ndscan.experiment.parameters import FloatParam
from ndscan.experiment.parameters import ParamHandle
from ndscan.experiment.parameters import StringParam
from ndscan.experiment.utils import is_kernel

from . import ccb
from . import dag
from . import patch_ndscan  # noqa
from . import scoping
from .optimizers import ParamSpec
from .optimizers import grid_search_optimizer

logger = logging.getLogger(__name__)

#: Broadcast dataset holding a {class_name: {status, last_check, timeout, data}}
#: table, published on every check so applets and later worker processes can
#: see calibration state.
#:
#: Deliberately *not* scoped per pipeline, unlike :data:`OPTIMIZER_DATASET` and
#: :data:`~qbutler.dag.DAG_DATASET`: when a calibration was last checked, and
#: what it found, is a property of the apparatus rather than of the pipeline
#: that happened to measure it, and :meth:`Calibration._recall_status` relies on
#: a walk in one pipeline seeing a check another pipeline already did.
STATUS_DATASET = "calibrations.status"

#: Base of the broadcast dataset holding a {class_name: {param_names, points,
#: data, status, started}} table, appended to as an optimizer walks so an applet
#: can watch the scan happen live (see
#: :meth:`Calibration._publish_optimizer_point`). Suffixed with the running
#: pipeline's name: each point is a read-modify-write of the whole table, so
#: simultaneous fixes in different pipelines would otherwise drop each other's
#: points (see :mod:`qbutler.scoping`).
OPTIMIZER_DATASET = "calibrations.optimizer"

#: Minimum wall-clock interval, in seconds, between ``scheduler.check_pause()``
#: calls made while a calibration is being fixed. Each check is a round trip to
#: the ARTIQ master, so a calibration whose shots take microseconds must not
#: make one per shot; in between checks the answer is taken to be "no pause".
PAUSE_CHECK_INTERVAL = 1.0

#: How many times a fix walk will try to bring a single calibration good before
#: giving up and raising :class:`CalibrationError`. ``None`` — the default —
#: means "never give up": a calibration that does not come good is simply fixed
#: again, indefinitely, since a drifting physics system usually needs another
#: attempt rather than an aborted experiment. Set this module attribute
#: (``qbutler.calibration.DEFAULT_MAX_FIX_ATTEMPTS = 1`` restores the old
#: fail-fast behaviour) to bound every calibration in the process, or call
#: :meth:`Calibration.set_max_fix_attempts` to bound one of them. It is read
#: when a fix runs, so it may be changed after the calibrations are built.
DEFAULT_MAX_FIX_ATTEMPTS = None

#: Marks a calibration that has not called :meth:`Calibration.set_max_fix_attempts`,
#: so that its budget is read from :data:`DEFAULT_MAX_FIX_ATTEMPTS` when a fix
#: walk actually runs (rather than being frozen at build time).
_MAX_FIX_ATTEMPTS_UNSET = object()

#: A check of the calibration's real state: a check walk, or the re-check a fix
#: walk makes after fixing a node. This is the check a monitor cares about.
CHECK_CONTEXT_CHECK = "check"

#: A trial measurement made by an optimizer while sweeping for a better point.
#: Its result describes the point being tried, not the state the calibration was
#: left in, so a health monitor should filter these out.
CHECK_CONTEXT_SWEEP = "sweep"

#: The confirmation measurement a fix makes with the best parameters applied,
#: i.e. the metric "at peak" that is about to be committed.
CHECK_CONTEXT_VERIFY = "verify"

#: Not a measurement at all: the synthetic BAD_DATA recorded when a fix gave up
#: with a :class:`CalibrationError` before it could measure anything.
CHECK_CONTEXT_FIX_FAILED = "fix_failed"

#: How many ndscan :class:`~ndscan.experiment.fragment.TransitoryError`\ s a
#: single measurement may raise before the walk gives up on it. A transitory
#: error means the apparatus is fine and the shot just needs taking again, so
#: the measurement is simply repeated; see
#: :meth:`Calibration._measure_with_transitory_retries`.
DEFAULT_MAX_TRANSITORY_ERROR_RETRIES = 10


class _PauseCheckGate:
    """Wall-clock gate in front of every ``scheduler.check_pause()`` qbutler makes.

    One gate for the process rather than one per calibration, so the
    once-a-second budget covers the DAG walk as a whole: a fix touching twenty
    nodes must not ask the master twenty times a second.
    """

    def __init__(self):
        self._last_check = None

    def due(self) -> bool:
        """True if enough time has passed to ask the scheduler again.

        Consumes the budget: two calls in quick succession return True, then
        False.
        """
        now = time()
        if (
            self._last_check is not None
            and now - self._last_check < PAUSE_CHECK_INTERVAL
        ):
            return False
        self._last_check = now
        return True

    def reset(self) -> None:
        """Forget the last check, so the next one happens immediately."""
        self._last_check = None


#: The process-wide gate (see :class:`_PauseCheckGate`).
pause_check_gate = _PauseCheckGate()


#: The calibration node whose measurement is currently set up on the hardware
#: (``host_setup`` run, ``host_cleanup`` pending), or None. One slot for the
#: whole process: calibration measurements share physical hardware (e.g. one
#: camera), so at most one may be "active" at a time — see
#: :meth:`Calibration._activate`.
_active_node: Optional["Calibration"] = None

#: Guards :data:`_active_node`. Walks run on the main thread, but monitor
#: checks run on executor threads; the lock keeps the slot coherent anywhere.
_active_node_lock = threading.RLock()


def deactivate_active_calibration() -> None:
    """``host_cleanup`` the currently-active calibration node, if any.

    Called at the end of every walk (:func:`fix_targets`, :func:`check_targets`,
    :meth:`Calibration.fix_state`, :meth:`Calibration.check_state`) and at
    client shutdown, so a walk leaves the hardware the way it found it and the
    main experiment fragment's next ``host_setup`` starts from a clean slate.
    """
    global _active_node
    with _active_node_lock:
        node, _active_node = _active_node, None
        if node is not None:
            node.host_cleanup()


class CalibrationError(RuntimeError):
    pass


class CalibrationEscape(Exception):
    """Raised inside a science kernel to unwind to the host for a recalibration.

    A calibration client's main ``@kernel`` checks, at a scan-point boundary,
    whether the calibration DAG still looks healthy; if not it raises this so the
    host can run the fix walk and re-enter the (precompiled) kernel. The class is
    module-level on purpose: a custom exception crosses the kernel→host boundary
    by *class identity*, so ``except CalibrationEscape`` only catches it if both
    sides import this same class. Do not shadow or re-define it downstream.
    """


class _SuspectDependencies(Exception):
    """Control flow only, never user-facing: a fix attempt failed and cast
    suspicion on dependencies that still looked good, so the walk should
    restart from the deepest dependency and re-examine them before retrying
    this node. Carries the node whose fix failed. Deliberately not a
    :class:`CalibrationError` subclass, so nothing that handles calibration
    failures can swallow it by accident.
    """

    def __init__(self, node: "Calibration"):
        super().__init__(f"Dependencies of {node.__class__.__name__} are now suspect")
        self.node = node


class CalibrationResult(int, Flag):
    OK = 0

    BAD_EXPIRED = auto()
    BAD_DEPS = auto()
    BAD_DATA = auto()
    BAD = BAD_EXPIRED | BAD_DEPS | BAD_DATA

    INVALID_DATA = auto()


def fix_targets(targets, force=False) -> None:
    """Fix the union of several calibration targets' DAGs.

    The multi-target analogue of :meth:`Calibration.fix_state`. It walks
    :func:`dag.get_union_dependencies` — the merged, furthest-dependency-first
    order across every target — so a node shared by several targets is fixed
    exactly once, before any node that depends on it, and every target's own
    leaf is fixed. Node ordering, the per-node guess/check/fix/re-check and the
    pooled-kernel dispatch are identical to the single-target walk.

    If a node's fix attempt fails while dependencies of it still look good,
    those dependencies become suspect and the walk backtracks: it restarts
    from the deepest dependency, re-measures the suspects despite their
    timeouts, fixes any found genuinely bad, and then retries the failing
    node — once per node per walk (see
    :meth:`Calibration._fix_own_state_until_ok`).

    Args:
        targets: the leaf calibrations to maintain (a client's
            ``calibration_targets``). Empty is a no-op.
        force: re-check and re-fix every node even if it looks fine.

    Raises:
        CalibrationError: if a node will not come good within its attempt
            budget (see :meth:`Calibration.set_max_fix_attempts`; by default
            there is no budget and a node is retried indefinitely).
        TerminationRequested: if the run is terminated while the walk pauses
            between nodes or between fix attempts (see
            :meth:`Calibration._pause_if_requested`).
    """
    targets = list(targets)
    if not targets:
        return
    for target in targets:
        dag.publish_dag(target)

    try:
        backtracked = set()
        while True:
            try:
                for dep in dag.get_union_dependencies(targets):
                    dep._pause_if_requested()
                    current_state = dep._guess_own_state()
                    if current_state & CalibrationResult.BAD_EXPIRED and not force:
                        current_state, _ = dep._do_check_own_state()

                    if current_state != CalibrationResult.OK or force:
                        dep._fix_own_state_until_ok(
                            allow_backtrack=not force and dep not in backtracked
                        )
                break
            except _SuspectDependencies as e:
                # A node's fix failed and its dependencies looked good, so they
                # are now suspect. Restart the walk from the deepest dependency:
                # suspect nodes no longer guess OK, so they get re-measured (and
                # fixed, if the measurement says so) before this node is retried.
                # Each node gets one such backtrack per walk; after that it is
                # retried on its own budget as usual.
                backtracked.add(e.node)
    finally:
        deactivate_active_calibration()


def check_targets(
    targets, force=False, continue_on_fail=False
) -> Tuple["CalibrationResult", Any]:
    """Check the union of several calibration targets' DAGs.

    The multi-target analogue of :meth:`Calibration.check_state`; returns as soon
    as a node is found bad (unless ``continue_on_fail``). See :func:`fix_targets`
    for the ordering guarantee.
    """
    targets = list(targets)
    if not targets:
        return CalibrationResult.OK, None
    for target in targets:
        dag.publish_dag(target)

    r = CalibrationResult.OK
    data = None
    deps = dag.get_union_dependencies(targets)
    try:
        for dep in deps:
            current_state = dep._guess_own_state()
            if force or current_state != CalibrationResult.OK:
                state, data = dep._do_check_own_state()
                r |= state
                if r != CalibrationResult.OK and not continue_on_fail:
                    if dep is deps[-1]:
                        return r, data
                    return CalibrationResult.BAD_DEPS, None
        return r, data
    finally:
        deactivate_active_calibration()


def _suspect_dependencies_of(failed: "Calibration") -> bool:
    """Cast suspicion on the dependencies of a calibration whose fix failed.

    If a node cannot be brought good, the likeliest physical explanation is
    often a dependency that still *looks* good — its last check passed and its
    timeout has not expired — but has actually drifted. Every such dependency
    is marked suspect (see :meth:`Calibration._add_suspicion`), which makes it
    a candidate for re-measurement despite its timeout.

    Dependencies that already guess bad (expired, failed, or already suspect)
    are left alone: the ordinary walk logic re-examines those anyway.

    Best-effort: a DAG hiccup must not be able to break a fix walk.

    Returns:
        True if at least one dependency was newly marked suspect.
    """
    name = failed.__class__.__name__
    try:
        deps = dag.get_dependencies(failed)
    except Exception:
        logger.warning(
            "Could not walk dependencies of %s to mark suspects", name, exc_info=True
        )
        return False

    newly_marked = False
    for dep in deps:
        if dep is failed:
            continue
        # Only a dependency that currently guesses OK can be *newly* suspect —
        # an already-suspect one guesses BAD_EXPIRED and is skipped here.
        if dep._guess_own_state() == CalibrationResult.OK:
            dep._add_suspicion(name)
            newly_marked = True
    return newly_marked


class Calibration(ExpFragment):
    """
    Represent a step in a calibration chain

    Calibrations represent a system with a desired outcome than can be checked
    and affected by changing certain parameters. A Calibration has the following
    features:

    1. Its status is either "OK" or "BAD"
    2. Its status and that of its dependents can be checked by running
       :meth:`check_state`.
    3. It can depend on other Calibrations (add them using
       :meth:`Calibration.add_dependency` in :meth:`.build_calibration`)
    4. It can be repaired / optimized by running :meth:`Calibration.fix_state`
    5. It is also a valid ndscan
       :class:`~ndscan.experiment.fragment.ExpFragment`, and so can be scanned
       using the usual ndscan interface

    To write a new Calibration object, you must implement the
    :meth:`check_own_state` method. If you need custom logic for checking state
    or calibrating, you can also override :meth:`fix_own_state`. See the
    documentation for each method for details of the interface.

    As a fully qualified :class:`~ndscan.experiment.fragment.Fragment`, you are
    also entitled to implement the ndscan methods such as
    :meth:`~ndscan.experiment.fragment.Fragment.device_setup`,
    :meth:`~ndscan.experiment.fragment.Fragment.host_setup`, etc.

    Do not implement :meth:`~ndscan.experiment.fragment.Fragment.run_once`! This
    is implemented automatically so that Calibrations can be scanned over their
    parameters as ndscan ExpFragments.

    """

    #: What the check currently in flight is for (see :meth:`_on_checked`). A
    #: class attribute so it reads correctly before (and without) a build.
    _check_context = CHECK_CONTEXT_CHECK

    def build_calibration(self):
        """
        Set parameters / options / results channels for the calibration

        As with ndscan's build_fragment() or ARTIQ's build(), this method set up
        the Calibration by defining things like:

        * What parameters it needs
        * Which parameters can be optimised
        * What timeout applies
        * What other Calibrations this one depends on

        Apart from Calibration methods, you can also call any :mod:`ndscan`
        methods available for build_fragment() in this method.

        Raises:
            NotImplementedError: Raised if the user did not override this method
                                 - you must write this method for your own
                                   classes.
        """
        raise NotImplementedError

    def check_own_state(self) -> Tuple[CalibrationResult, Any]:
        """
        Measure the status of this :class:`.Calibration`

        You must override this method to implement the logic that allows this
        Calibration to

        a) check if it is "OK"

        b)  *(optional)* measure a quantity that can be used to quantify this
            Calibration's status

        This method must measure the state of the system somehow, then return a
        "status" of type :class:`.CalibrationResult`. It must also output a
        "data" value which could be used to optimize the :class:`.Calibration`.
        This can be `None` if not desired. :meth:`fix_own_state` can handle
        basic algorithms for optimizing as long as the "data" output is a float.
        To handle other output formats, you can override :meth:`fix_own_state`:
        see the docs for :meth:`fix_own_state`.

        This method has access to the usual :mod:`ndscan` preparations such as
        :meth:`~ndscan.experiment.fragment.Fragment.device_setup`,
        :meth:`~ndscan.experiment.fragment.Fragment.host_setup` etc, and may be
        a kernel. See the documentation for
        :class:`~ndscan.experiment.fragment.Fragment` for details.

        TODO: Confirm that CalibrationResult types work on kernels
        """
        raise NotImplementedError(
            "You should override this method with your own code: see the docs"
        )

    def build_fragment(self, *args, **kwargs) -> None:
        """
        Set up the calibration

        Initialize this Calibration, setting up the "data" and "status"
        :class:`ndscan.experiment.result_channels.ResultChannel` objects which
        receive results from a single check of the Calibration.

        Do not call this method yourself: it will be called by the ndscan
        machinery.
        """
        self.__timeout = 0
        self.__max_fix_attempts = _MAX_FIX_ATTEMPTS_UNSET
        self.__optimizable_params = []
        self.__optimizer_func = grid_search_optimizer  # Default optimizer, can be overridden by set_optimizer()
        self.__most_recent_check_timestamp = None
        self.__most_recent_check_result = None
        self.__most_recent_check_data = None
        self.__optimization_type = "max"
        self.max_transitory_error_retries = DEFAULT_MAX_TRANSITORY_ERROR_RETRIES
        # Class names of dependents whose fix conclusively failed while this
        # node looked good. Non-empty means SUSPECT: the cached OK is not
        # trusted and this node is re-measured despite its timeout. This is
        # trust metadata, deliberately kept out of CalibrationResult — a check
        # can never *measure* "suspect".
        self.__suspected_by = set()

        # Resolved once, here, rather than per shot: the scheduler is a virtual
        # device, but the fix loops consult it in their innermost loop. None
        # where there is no scheduler at all (a bare unit test), in which case
        # pause checking is simply skipped.
        try:
            self._scheduler = self.get_device("scheduler")
        except Exception:
            logger.debug(
                "No scheduler device available: %s cannot be paused mid-fix",
                self.__class__.__name__,
                exc_info=True,
            )
            self._scheduler = None

        # Set by attach_precompile_pool when a client arms a PrecompilePool;
        # while None the walk measures / fixes directly (plain host-mode
        # qbutler). Kernels compile lazily, on first activation (_activate).
        self._precompile_pool = None
        self._precompile_seeded = False
        self._precompile_check_key = None
        self._precompile_fix_key = None
        self._precompile_fix_own_key = None

        # True once host_setup has ever run (see _activate)
        self._lifecycle_setup_ran = False

        # Add results channels for measurements of the Calibration's state
        self.setattr_result("status", OpaqueChannel)
        self.setattr_result("data", OpaqueChannel)
        self.status: OpaqueChannel
        self.data: OpaqueChannel

        self.__in_build_calibration = True
        self.build_calibration(*args, **kwargs)
        self.__in_build_calibration = False

        # Add a parameter controlling whether this calibration's data is
        # maximized, minimized or set to zero. This is a parameter rather than
        # an attribute so that ndscan users can override it when debugging.
        self.setattr_param(
            "optimization_type",
            StringParam,
            description="How should this Calibration be optimized?",
            default=f"'{self.__optimization_type}'",
        )

        # Register this Calibration as having been built
        dag.add_to_dependency_map(self, None)

    def run_once(self) -> None:
        """
        Run the checks of this Calibration once and push the results into the
        :any:.`ResultsChannel`s "data" and "status" so that Calibrations can
        also be scanned as ExpFragments.
        """
        status, data = self._do_check_own_state()
        self.status.push(status)
        self.data.push(data)

    def _param_dataset_key_from_name(self, name: str) -> str:
        return self.__class__.__name__ + "." + name

    def setattr_param_optimizable(
        self,
        name: str,
        description: str,
        min: float,
        max: float,
        default: float,
        *args,
        **kwargs,
    ) -> ParamHandle:
        """Create an ndscan parameter that's available for optimization by the
        calibrator

        This method can only be called during the build() phase.

        The syntax for this method is exactly the same as for
        :meth:`ndscan.experiment.fragment.Fragment.setattr_param`, but also
        requires minimum and maximum bounds for the optimizer. Note that these
        may differ from the min/max bounds specified by the param_class
        instance.

        For now, only :class:`ndscan.experiment.parameters.FloatParam` objects
        are supported.

        Parameters created via this method will behave exactly the same as
        normal ndscan parameters, except

        a) They'll also be optimized during :meth:`.calibrate` routines.
        b) They'll automatically have their default values set to load from a
           persistent dataset with a name generated via
           :meth:`_param_dataset_key_from_name` (as if you'd set e.g
           ``default = 'dataset("somedataset", 123)'`` in your
           :class:`FloatParam` setup).

        Args:
            name (str): The parameter name, to be part of its FQN. Must be a
            valid Python
                        identifier; the parameter handle will be accessible as
                        ``self.<name>``.
            description (str): The human-readable parameter name. min (float):
            Minimum value for the optimizer to try max (float): Maximum value
            for the optimizer to try args: Any extra arguments to pass to the
            ``param_class`` constructor. kwargs: Any extra keyword arguments to
            pass to the the ``param_class``
                    constructor.

        Returns:
            ParamHandle: The newly created parameter handle.
        """
        if not self.__in_build_calibration:
            raise TypeError("This method must only be called in build_calibration()")

        dataset_key = self._param_dataset_key_from_name(name)

        p = self.setattr_param(
            name,
            FloatParam,
            description,
            default=f'dataset("{dataset_key}", default={default})',
            *args,
            **kwargs,
        )
        self.__optimizable_params.append(
            ParamSpec(name=name, min=min, max=max, handle=p)
        )
        return p

    def set_optimization_type(self, optimization_type: str) -> None:
        """
        Configure how this Calibration is optimized

        Control how the default fix_state algorithm will optimize this
        Calibration, based on the "data" result output from :meth:`.check_own_state`.

        Options are:

        "max": Attempt to maximise the result
        "min": Attempt to minimixe
        "zero": Attempt to set the result to zero

        The default is "max".

        Arguments:
            type (str): One of "max", "min", "zero".
        """
        optimization_type = optimization_type.lower()

        if optimization_type not in ["max", "min", "zero"]:
            raise ValueError('type must be one of "max", "min" or "zero"')

        self.__optimization_type = optimization_type

    def add_dependency(
        self,
        dep_calibration_class: Type["Calibration"],
        name: str = None,
        create_duplicates=False,
    ) -> None:
        """
        Add a dependency of this Calibration

        This method can only be called during the build() phase.

        Adds another Calibration as a dependency of this one. This method can be
        called multiple times to add multiple dependencies.

        Note that this method should be passed the dependency's *class*, not an
        instantiated object.

        If the dep_calibration_class Calibration has already been created, the
        existing instance will be added as a dependency instead of a new one.
        This prevents the creation of multiple Calibrations all checking the
        same thing. If you'd like to force the creation of a duplicate, use
        `create_duplicates`.

        Args:
            dep_calibration_class (Type["Calibration"]):
                The Calibration class to add as a dependency

            name (str):
                The name to use for this calibration. Default to the name of the
                class.

            create_duplicates (bool):
                If True, create new objects even if a Calibration of this type
                already exists.
        """
        if not self.__in_build_calibration:
            raise TypeError("This method must only be called in build_calibration()")

        if name is None:
            name = dep_calibration_class.__name__

        cals_from_cache = dag.get_calibrations_of_type(dep_calibration_class)

        if create_duplicates or not cals_from_cache:
            # If the Calibration does not already exist, initialize it as a new
            # subfragment and add it to our DAG machinery
            self.setattr_calibration(dep_calibration_class, name=name)

            dep_calibration_object = getattr(self, name)
            # Dependencies are driven by check_state/fix_state walks, never by
            # the enclosing ndscan scan: detach so the scan machinery neither
            # collects their (unpushed) status/data channels nor runs their
            # setup/cleanup
            self.detach_fragment(dep_calibration_object)
            dag.add_to_dependency_map(self, dep_calibration_object)
        else:
            # If this Calibration has been already created elsewhere, don't make a
            # new copy. Instead, add the first existing version as an attribute
            if len(cals_from_cache) > 1:
                logger.warning(
                    "Multiple instances of %s exist - using the first one (%s)",
                    dep_calibration_class,
                    cals_from_cache[0],
                )
            dep_calibration_object = cals_from_cache[0]
            setattr(self, name, dep_calibration_object)
            # The dependency edge must be recorded even though the node is
            # shared: dropping it left the dependent disconnected from (part
            # of) its chain, so walks could order it before its own
            # dependencies (issue #31).
            dag.add_to_dependency_map(self, dep_calibration_object)

    def _get_dependencies(self):
        return dag.get_dependencies(self)

    def set_timeout(self, timeout: float):
        """
        Set the timeout after which previously performed calibration checks
        become invalid.

        This method can only be called during the build() phase.

        After this timeout has elapsed, future calls to
        :meth:`Calibration.guess_state` will return a :class:`CalibrationResult`
        of type :any:`CalibrationResult.BAD_EXPIRED`. This will trigger a check
        of the process via a call to :meth:`Calibration.check_state` on the next
        request.

        If this method is not called, timeout defaults to 0 seconds.

        Args:
            timeout (float): _description_
        """
        if not self.__in_build_calibration:
            raise TypeError("This method must only be called in build_calibration()")

        self.__timeout = timeout

    def get_timeout(self) -> float:
        """
        Gets the timeout set by :meth:`set_timeout`

        Returns:
            float: The timeout in seconds
        """
        return self.__timeout

    def set_max_fix_attempts(self, attempts: Optional[int]) -> None:
        """
        Limit how many times a fix walk will try to bring this Calibration good

        This method can only be called during the build() phase.

        A fix walk fixes a Calibration and then re-checks it. If the re-check is
        still not :any:`CalibrationResult.OK` — either because the measurement
        says so or because the optimizer gave up — qbutler assumes the system has
        simply drifted somewhere the last attempt could not reach, and fixes it
        again. By default it will do so indefinitely, so a stubborn calibration
        stalls the experiment rather than crashing it. Terminating the run still
        stops it promptly: the retry loop yields to the ARTIQ scheduler between
        attempts.

        Call this to bound that instead, e.g. ``set_max_fix_attempts(3)`` to get
        three tries and then a :class:`CalibrationError`, or
        ``set_max_fix_attempts(1)`` for the fail-fast behaviour qbutler used to
        have. Pass ``None`` to retry forever (the default).

        One nuance: a failed attempt that makes dependencies suspect triggers
        a backtrack — the walk re-verifies those dependencies and then retries
        this node with a fresh budget — before the budget can abort anything.
        That happens at most once per node per walk, so a budget of ``n``
        allows up to roughly ``2n`` attempts in total, and even
        ``set_max_fix_attempts(1)`` gets its one chance to blame a dependency
        before failing.

        Args:
            attempts (Optional[int]): The maximum number of fix attempts, or
                ``None`` for no limit. Must be at least 1 if given.
        """
        if not self.__in_build_calibration:
            raise TypeError("This method must only be called in build_calibration()")

        if attempts is not None:
            attempts = int(attempts)
            if attempts < 1:
                raise ValueError("max_fix_attempts must be at least 1, or None")

        self.__max_fix_attempts = attempts

    def get_max_fix_attempts(self) -> Optional[int]:
        """
        Gets the fix-attempt budget set by :meth:`set_max_fix_attempts`

        Returns:
            Optional[int]: The maximum number of fix attempts, or ``None`` if
            this Calibration is retried until it succeeds. Falls back to
            :data:`DEFAULT_MAX_FIX_ATTEMPTS` if it was never set.
        """
        if self.__max_fix_attempts is _MAX_FIX_ATTEMPTS_UNSET:
            return DEFAULT_MAX_FIX_ATTEMPTS
        return self.__max_fix_attempts

    def guess_state(self) -> CalibrationResult:
        """
        Guess the status of this Calibration based on past measurements

        This method guesses the current status of this Calibration based on:

        * The most recent CalibrationResult
        * The time since the most recent data & the timeout
        * The status of all dependent Calibrations

        This method does not interact with any devices, and is therefore
        computationally cheap.

        Returns:
            CalibrationResult: The guessed status of this Calibration.
        """
        # Iterate over the dependencies, starting with the ones furthest away, and check their states
        for dep in dag.get_dependencies(self):
            state = dep._guess_own_state()
            if not (state & CalibrationResult.OK):
                return state

        return self._guess_own_state()

    def check_state(
        self, force=False, continue_on_fail=False
    ) -> Tuple[CalibrationResult, Any]:
        """
        Check the state of this Calibration and dependents

        This method will perform quick measurements where necessairy to update
        any expired / bad / invalid Calibrations. If a dependent Calibration is
        still within its timeout, it won't be checked unless `force==True`.

        Note that this method will return as soon as a problem is found, so it
        is not guaranteed that all dependents were checked unless the result is
        OK or continue_on_fail was passed.

        Args:
            force (bool, optional): Check all dependents, even if they should be
                                    fine. Defaults to False.
            continue_on_fail (bool, optional):
                                    Continue checking all the dependents even if
                                    we encounter a failure.

        Returns:
            CalibrationResult:
                Result of the checks. If continue_on_fail was passed, this is
                the bitwise combination of all results; otherwise it is the
                first bad result, or OK.

            Any:
                Data from the final layer of the check, i.e. from this
                Calibration, or None if the check failed before the final
                layer was run.
        """
        dag.publish_dag(self)

        # Iterate over the dependencies, starting with the ones furthest away,
        # and check their states, ending with this object
        r = CalibrationResult.OK
        data = None

        deps = dag.get_dependencies(self)
        try:
            for dep in deps:
                current_state = dep._guess_own_state()
                if force or current_state != CalibrationResult.OK:
                    state, data = dep._do_check_own_state()
                    r |= state
                    if r != CalibrationResult.OK and not continue_on_fail:
                        if dep == deps[-1]:
                            return r, data
                        else:
                            return CalibrationResult.BAD_DEPS, None

            return r, data
        finally:
            deactivate_active_calibration()

    def fix_state(self, force=False):
        """
        Fix the state of this Calibration and dependents

        This method will perform quick measurements where necessairy to update
        any expired / bad / invalid Calibrations. If force==True, this step is
        skipped.

        For any Calibrations that fail the check, or if force==True,
        :meth:`fix_own_state` will be called on each, starting from the most
        basic. After this, :meth`check_own_state` will be called again; if the
        Calibration is still not OK it is simply fixed again, and again, until
        it comes good (see :meth:`_fix_own_state_until_ok`). Bound that with
        :meth:`set_max_fix_attempts` if you would rather the walk gave up.

        A failed fix attempt also casts suspicion on the failing node's
        dependencies that still look good: the walk backtracks (once per node
        per walk), re-measuring the suspects despite their timeouts and fixing
        any found genuinely bad, before the failing node is retried.

        Args:
            force (bool, optional): Check all dependents, even if they should be
                                    fine. Defaults to False.

        Returns:
            CalibrationResult:
                Result of the checks. If continue_on_fail was
                passed, this is the bitwise combination of all
                results; otherwise it is the first bad result,
                or OK.

        Raises:
            CalibrationError: if a node will not come good within its attempt
                budget — only possible once one has been set, with
                :meth:`set_max_fix_attempts` or
                :data:`DEFAULT_MAX_FIX_ATTEMPTS`.
            TerminationRequested: if the run is terminated while the walk
                pauses between nodes or between fix attempts (see
                :meth:`_pause_if_requested`).
        """
        dag.publish_dag(self)

        logger.debug(f"Fixing all dependencies of {self.__class__.__name__}")
        try:
            backtracked = set()
            while True:
                try:
                    # Iterate over the dependencies, starting with the ones
                    # furthest away, and check their states, ending with this
                    # object
                    for dep in dag.get_dependencies(self):
                        dep._pause_if_requested()
                        current_state = dep._guess_own_state()
                        logger.debug(
                            f"Guessed state of {dep.__class__.__name__} = {current_state}"
                        )

                        if current_state & CalibrationResult.BAD_EXPIRED and not force:
                            current_state, _ = dep._do_check_own_state()

                        if current_state != CalibrationResult.OK or force:
                            try:
                                current_state, _ = dep._fix_own_state_until_ok(
                                    allow_backtrack=not force and dep not in backtracked
                                )
                            except CalibrationError:
                                # Only reachable when the node has a finite
                                # attempt budget: record ourselves as
                                # broken-by-dependency before the error unwinds,
                                # so a later guess does not trust our stale
                                # check.
                                self.__most_recent_check_result = (
                                    CalibrationResult.BAD_DEPS
                                )
                                self.__most_recent_check_timestamp = time()
                                self.__most_recent_check_data = None
                                raise

                    return current_state
                except _SuspectDependencies as e:
                    # A node's fix failed and its dependencies looked good, so
                    # they are now suspect. Restart the walk from the deepest
                    # dependency: suspect nodes no longer guess OK, so they get
                    # re-measured (and fixed, if the measurement says so) before
                    # this node is retried. Each node gets one such backtrack
                    # per walk; after that it is retried on its own budget as
                    # usual.
                    backtracked.add(e.node)
        finally:
            deactivate_active_calibration()

    def host_setup(self):
        # Record that this node has been set up at least once, so _activate can
        # tell an ndscan-managed node (e.g. a monitor, set up at experiment
        # start) from a detached dependency that has never been prepared.
        self._lifecycle_setup_ran = True
        super().host_setup()

    def _activate(self) -> None:
        """Make this node's measurement the active one, honouring the ndscan
        fragment contract: its ``host_setup`` has run, and still holds, when its
        kernels execute.

        Calibration measurements share physical hardware (one camera, one set
        of beams), so their ``host_setup``\\ s must not coexist: whichever ran
        last owns the hardware. This is the serialisation point — at most one
        node is set up at a time (:data:`_active_node`), and every switch is a
        full handover: ``host_cleanup`` of the previously-active node, then
        ``host_setup`` of this one. Called from :meth:`_do_check_own_state` /
        :meth:`_do_fix_own_state`, i.e. on the host, immediately before a
        node's kernels are dispatched.

        The slot is claimed *before* ``host_setup`` runs, mirroring ndscan's
        ``try: host_setup ... finally: host_cleanup``: if setup fails halfway,
        the node still gets its cleanup on the next handover (fragments must
        tolerate cleanup after a failed setup, as in plain ndscan).

        The first activation also compiles this node's kernels into the
        precompile pool (lazy): compilation embeds attributes ``host_setup``
        creates, so it can only happen once the node has been set up — which
        is exactly now.

        Host-mode nodes (host ``check_own_state``) do not join the handover:
        monitors are checked concurrently from several threads and hold no
        rig hardware. A host-mode node that has never been set up (a detached
        dependency in a client walk) gets a one-time ``host_setup`` here;
        one already set up by ndscan (an attached monitor) is left alone.
        """
        global _active_node
        if not (is_kernel(self.check_own_state) or is_kernel(self.fix_own_state)):
            if not getattr(self, "_lifecycle_setup_ran", False):
                self.host_setup()
            return

        with _active_node_lock:
            if _active_node is self:
                return
            if _active_node is not None:
                node, _active_node = _active_node, None
                node.host_cleanup()
            _active_node = self
            self.host_setup()

        pool = getattr(self, "_precompile_pool", None)
        if pool is not None and not self._precompile_seeded:
            self._seed_own_precompile(pool)
            self._precompile_seeded = True

    def attach_precompile_pool(self, pool) -> None:
        """Point every node in this calibration's DAG at ``pool`` for kernel
        precompilation.

        Call once on the host — the calibration client base class does this at
        ``host_setup`` from ``dag.get_dependencies``. Idempotent. Attaching
        does NOT compile anything: a node's kernels are compiled lazily, on its
        first activation (see :meth:`_activate`), because compilation needs the
        node's ``host_setup`` to have run and setups are serialised. Host-mode
        calibrations (host ``check_own_state``) are left alone and keep
        measuring directly, so plain qbutler use is unchanged.

        A node whose ``check_own_state`` drives a kernel measurement on the host
        (rather than being a kernel itself) should override
        :meth:`_seed_own_precompile` to seed that measurement kernel and route
        its host loop through the pool.
        """
        for node in dag.get_dependencies(self):
            node._precompile_pool = pool

    def _seed_own_precompile(self, pool) -> None:
        self._precompile_check_key = None
        self._precompile_fix_key = None
        self._precompile_fix_own_key = None

        is_kernel_check = is_kernel(self.check_own_state)

        if is_kernel(self.fix_own_state):
            # Node with its own @kernel fix: precompile it directly.
            self._precompile_fix_own_key = (self, "fix_own")
            pool.seed(self._precompile_fix_own_key, self.fix_own_state)
        elif is_kernel_check and len(self.__optimizable_params) > 0:
            # Default optimizer fix over a kernel check: precompile the resident
            # optimizer loop. Bind the (persistent) stores first, so both it and
            # the check kernel below embed the same live stores.
            self._kopt_bind_stores()
            self._precompile_fix_key = (self, "fix")
            pool.seed(self._precompile_fix_key, self._optimizer_kernel_loop)

        if is_kernel_check:
            self._precompile_check_key = (self, "check")
            if self._precompile_fix_key is not None:
                # Optimized node: its committed param must reach the precompiled
                # check at runtime (see _check_with_current_params).
                pool.seed(self._precompile_check_key, self._check_with_current_params)
            else:
                pool.seed(self._precompile_check_key, self.check_own_state)

    def _kopt_bind_stores(self) -> None:
        """Override the optimizable params and bind their stores for a
        kernel-driven fix. Idempotent; must run before kernel compilation."""
        if getattr(self, "_kopt_stores", None) is not None:
            return
        param_specs = self.__optimizable_params
        stores = {}
        for spec in param_specs:
            _, store = self.override_param(spec.name, spec.handle.get())
            stores[spec.name] = store
        self._kopt_specs = param_specs
        self._kopt_stores = [stores[spec.name] for spec in param_specs]

    def _do_check_own_state(self) -> Tuple[CalibrationResult, Any]:
        self._activate()
        pool = getattr(self, "_precompile_pool", None)
        check_key = getattr(self, "_precompile_check_key", None)
        num_transitory = 0
        while True:
            try:
                if pool is not None and check_key is not None:
                    result, data = pool.get(check_key)()
                else:
                    result, data = self.check_own_state()
                break
            except RestartKernelTransitoryError:
                raise
            except TransitoryError:
                if num_transitory >= self.max_transitory_error_retries:
                    raise
                num_transitory += 1
                self._report_transitory_retry(num_transitory)
        self._record_own_check(result, data)
        return result, data

    @rpc(flags={"async"})
    def _report_transitory_retry(self, attempt: TInt32) -> None:
        """Report a repeated measurement. Async so the kernel-side retry loop
        pays no slack for it."""
        logger.warning(
            "%s: transitory error while measuring, repeating the shot (%d/%d)",
            self.__class__.__name__,
            attempt,
            self.max_transitory_error_retries,
        )

    @kernel
    def _measure_with_transitory_retries(self):
        """One :meth:`check_own_state`, repeated on transitory errors only.

        A :class:`~ndscan.experiment.fragment.TransitoryError` means the
        apparatus is fine and this particular shot just needs taking again, so
        the measurement is simply repeated (up to
        :attr:`max_transitory_error_retries`).

        ``RTIOUnderflow`` is deliberately NOT caught here. An underflow is a
        real timing failure and must surface: only the fragment that raised it
        is in a position to judge it benign, and the way to say so is to
        convert it to a ``TransitoryError`` at that point.
        ``RestartKernelTransitoryError`` is also not repeated — nothing inside
        a resident kernel loop can restart the kernel — so it propagates to the
        client.
        """
        num_transitory = 0
        result = CalibrationResult.OK
        data = 0.0
        while True:
            try:
                result, data = self.check_own_state()
                break
            except RestartKernelTransitoryError:
                raise
            except TransitoryError:
                if num_transitory >= self.max_transitory_error_retries:
                    raise
                num_transitory += 1
                self._report_transitory_retry(num_transitory)
        return result, data

    def _do_fix_own_state(self) -> None:
        """Fix this node, using a precompiled kernel if the pool has one.

        The default (host) path just calls :meth:`fix_own_state`; a node with a
        ``@kernel`` ``fix_own_state`` runs its precompiled artifact instead. A
        default-optimizer node's fix is pooled one level down, inside
        :meth:`_run_optimizer`.
        """
        self._activate()
        pool = getattr(self, "_precompile_pool", None)
        fix_own_key = getattr(self, "_precompile_fix_own_key", None)
        if pool is not None and fix_own_key is not None:
            pool.get(fix_own_key)()
        else:
            self.fix_own_state()

    def _fix_own_state_until_ok(
        self, allow_backtrack: bool = True
    ) -> Tuple[CalibrationResult, Any]:
        """Fix this node and re-check it, retrying until the check passes.

        One fix attempt failing is not evidence that the calibration is
        impossible: the system may have drifted while the optimizer swept, the
        measurement may have been noisy, or a dependency may have settled only
        just now. Crashing the whole experiment on the first such failure
        surprises users, so the default is to try again — indefinitely, unless
        :meth:`set_max_fix_attempts` (or :data:`DEFAULT_MAX_FIX_ATTEMPTS`) sets a
        budget.

        A failed attempt also casts suspicion upstream: every dependency that
        still looks good is marked suspect (see
        :func:`_suspect_dependencies_of`), because a fix that will not take is
        often the fault of an input that has silently drifted inside its
        timeout. If ``allow_backtrack`` is True and anything was newly marked,
        :class:`_SuspectDependencies` is raised instead of retrying here, so
        the calling walk can re-examine those dependencies first and only then
        retry this node. With ``allow_backtrack=False`` (the walk has already
        backtracked for this node, or is running with ``force``) the marks are
        still made — monitors and later walks see them — but the retry loop
        carries on locally.

        A retry is a full fix: the optimizer sweeps again from scratch, so a
        recovering system is picked up on the next pass. Between attempts we
        yield to the ARTIQ scheduler, so a run stuck retrying a hopeless
        calibration can still be preempted or terminated by the user.

        Errors other than :class:`CalibrationError` are *not* retried: a
        ``NotImplementedError`` from an unwritten ``check_own_state``, or a
        ``ValueError`` from a calibration with nothing to optimize, is a bug in
        the calibration rather than a drifting system, and retrying it would only
        hide it.

        Returns:
            The ``(result, data)`` of the check that finally passed.

        Raises:
            CalibrationError: if a finite attempt budget is exhausted.
            _SuspectDependencies: if this attempt made dependencies suspect and
                ``allow_backtrack`` is True.
            TerminationRequested: if the run is terminated while waiting to
                retry (see :meth:`_pause_if_requested`).
        """
        name = self.__class__.__name__
        max_attempts = self.get_max_fix_attempts()
        attempt = 0

        while True:
            attempt += 1
            logger.debug("Attempting fix of %s (attempt %d)", name, attempt)

            try:
                self._do_fix_own_state()
                result, data = self._do_check_own_state()
            except CalibrationError as e:
                # The fix gave up on its own (e.g. the optimizer found no point
                # that checked out). Same failure as a bad re-check, so it gets
                # the same treatment: record the node as broken and try again.
                reason = str(e)
                result, data = CalibrationResult.BAD_DATA, None
                with self._checking_for(CHECK_CONTEXT_FIX_FAILED):
                    self._record_own_check(result, data)
            else:
                logger.debug("Result of fix of %s = %s", name, result)
                if result == CalibrationResult.OK:
                    return result, data
                reason = f"check returned {result!s}"

            # This attempt failed, which casts doubt on every dependency that
            # still looks good: one of them may be the real problem. Mark them
            # before deciding what to do next, so the suspicion is published
            # even if we give up below.
            newly_suspect = _suspect_dependencies_of(self)

            # Backtracking wins over giving up: even a fail-fast calibration
            # (max_fix_attempts=1) deserves its one chance to blame a
            # dependency. Only when the walk has already backtracked for this
            # node does an exhausted budget abort.
            if allow_backtrack and newly_suspect:
                logger.warning(
                    "Calibration of %s did not succeed (%s); its dependencies "
                    "are now suspect and will be re-examined before it is "
                    "retried",
                    name,
                    reason,
                )
                raise _SuspectDependencies(self)

            if max_attempts is not None and attempt >= max_attempts:
                raise CalibrationError(
                    f"Calibration of {name} failed after "
                    f"{attempt} attempt{'' if attempt == 1 else 's'}: {reason}"
                )

            logger.warning(
                "Calibration of %s did not succeed (%s); retrying (attempt %d%s)",
                name,
                reason,
                attempt + 1,
                f" of {max_attempts}" if max_attempts is not None else "",
            )
            # Yield before the next sweep, so an experiment stuck on a
            # calibration that never comes good can still be interrupted.
            self._pause_if_requested()

    def _pause_if_requested(self) -> None:
        """Yield to the ARTIQ scheduler between shots, if it wants us to.

        Without this a calibration walk is uninterruptible: the DAG is fixed
        node by node with no return to the scheduler, so a user asking to
        terminate the run waits until every calibration has finished. Called
        between nodes of a fix walk and between the shots of a fix, so a
        request is honoured within roughly one measurement.

        Call from the **host** only, never from inside a kernel:
        ``scheduler.pause()`` hands the core device to the run we are yielding
        to, so our kernel must have returned first — which is exactly why the
        resident optimizer loop bails out to the host instead of pausing in
        place (see :meth:`_kopt_next_point`).

        Cheap enough for a tight loop: the scheduler is asked at most once
        every :data:`PAUSE_CHECK_INTERVAL` seconds, so a calibration whose
        shots take microseconds is not slowed down by round trips to the
        master.

        Fixes only. Checks are also driven from :mod:`qbutler.monitoring`, one
        thread per monitor, and pausing off the main thread is not safe; the
        monitor controller watches for termination itself.

        Raises:
            TerminationRequested: if the run is being terminated rather than
                merely preempted. Letting it propagate is the point — it is how
                a calibration walk stops when the user asks it to.
        """
        if self._scheduler_wants_pause():
            self._pause_now()

    def _scheduler_wants_pause(self) -> bool:
        """Rate-limited ``scheduler.check_pause()``: True if pausing now would
        do something (termination requested, or a higher-priority run waiting).

        Never raises — a scheduler hiccup must not break a calibration — and
        returns False both when there is no scheduler and when the gate says we
        asked too recently.
        """
        scheduler = getattr(self, "_scheduler", None)
        if scheduler is None or not pause_check_gate.due():
            return False
        try:
            return bool(scheduler.check_pause())
        except Exception:
            logger.warning("Could not check for a scheduler pause", exc_info=True)
            return False

    def _pause_now(self) -> None:
        """Hand back the core device and pause until the scheduler resumes us."""
        logger.info(
            "Pausing calibration of %s at the scheduler's request",
            self.__class__.__name__,
        )
        core = getattr(self, "core", None)
        if core is not None:
            # Release the core device so the run we are yielding to can use it;
            # the next kernel call re-opens the connection and re-uploads.
            try:
                core.close()
            except Exception:
                logger.debug("Could not close the core device", exc_info=True)
        self._scheduler.pause()

    def _record_own_check(self, result: CalibrationResult, data) -> None:
        """Record + publish a check outcome, whether measured on the host or
        reported back from a kernel."""
        # A fresh measurement supersedes any suspicion cast on this node: if it
        # measured OK the doubt is answered, and if it measured bad the node is
        # genuinely broken and the ordinary fix path takes over.
        self.__suspected_by.clear()

        self.__most_recent_check_result = result
        self.__most_recent_check_data = data
        self.__most_recent_check_timestamp = time()

        if result == CalibrationResult.OK:
            # This node coming good also answers the suspicion *it* cast on its
            # own dependencies: whatever was wrong evidently was not them.
            # Their still-in-timeout OKs become trusted again, unmeasured.
            name = self.__class__.__name__
            try:
                for dep in dag.get_dependencies(self):
                    if dep is not self:
                        dep._retract_suspicion(name)
            except Exception:
                # Not in any DAG (e.g. a bare fragment in a unit test), or a
                # graph hiccup — the dataset prune in _publish_status still
                # clears the suspicion for anyone reading it.
                logger.debug("Could not retract suspicions", exc_info=True)

        logger.debug(
            "Checked own state of %s: result %s/%s at time %s",
            self.__class__.__name__,
            result,
            data,
            self.__most_recent_check_timestamp,
        )

        self._publish_status()
        self._fire_checked(result, data)

    def _publish_status(self) -> None:
        """Best-effort mirror of check state to :data:`STATUS_DATASET`.

        Never raises: dataset plumbing must not be able to break a
        calibration run.
        """
        try:
            # archive=False: archiving would pull the dict into the run's
            # HDF5 results, which cannot represent it (and kills the worker
            # at write_results)
            table = self.get_dataset(STATUS_DATASET, default={}, archive=False)
            if not isinstance(table, dict):
                table = {}
            data = self.__most_recent_check_data
            name = self.__class__.__name__
            table[name] = {
                "status": int(self.__most_recent_check_result),
                "last_check": self.__most_recent_check_timestamp,
                "timeout": self.__timeout,
                "data": float(data) if isinstance(data, (int, float)) else None,
                "suspected_by": sorted(self.__suspected_by),
            }
            if self.__most_recent_check_result == CalibrationResult.OK:
                # This node coming good retracts the suspicion it cast, even on
                # nodes not instantiated in this process: prune our name from
                # every other entry before the single write.
                for other, entry in table.items():
                    if (
                        other != name
                        and isinstance(entry, dict)
                        and name in entry.get("suspected_by", [])
                    ):
                        entry["suspected_by"] = [
                            s for s in entry["suspected_by"] if s != name
                        ]
            self.set_dataset(
                STATUS_DATASET, table, broadcast=True, persist=True, archive=False
            )
        except Exception:
            logger.warning("Could not publish calibration status", exc_info=True)

    def _optimizer_dataset_key(self) -> str:
        """This pipeline's :data:`OPTIMIZER_DATASET` key."""
        return scoping.scoped_key(OPTIMIZER_DATASET, self)

    def _reset_optimizer_trace(self, param_names) -> None:
        """Start a fresh live trace for this optimizer run.

        Called at the top of every fix so an applet plots one sweep at a time.
        Also ensures this calibration's own optimizer applet exists, so the
        sweep is shown in a plot namespaced for this class (and this pipeline)
        as soon as it starts.
        Never raises: visualisation must not be able to break a calibration.
        """
        ccb.create_optimizer_applet(self, self.__class__.__name__)
        try:
            key = self._optimizer_dataset_key()
            table = self.get_dataset(key, default={}, archive=False)
            if not isinstance(table, dict):
                table = {}
            table[self.__class__.__name__] = {
                "param_names": list(param_names),
                "points": [],
                "data": [],
                "status": [],
                "started": time(),
            }
            self.set_dataset(key, table, broadcast=True, persist=True, archive=False)
        except Exception:
            logger.warning("Could not reset optimizer trace", exc_info=True)

    def _publish_optimizer_point(self, values, result, data) -> None:
        """Append one just-measured optimizer point to the live trace.

        ``values`` is the parameter tuple that produced ``(result, data)``.
        Broadcast so an applet can watch the scan as it happens. Best-effort:
        both the host loop and the resident kernel loop call this per point,
        and neither may be broken by a dataset hiccup.
        """
        try:
            key = self._optimizer_dataset_key()
            table = self.get_dataset(key, default={}, archive=False)
            if not isinstance(table, dict):
                return
            entry = table.get(self.__class__.__name__)
            if not isinstance(entry, dict):
                return
            entry["points"].append([float(v) for v in values])
            entry["data"].append(
                float(data) if isinstance(data, (int, float)) else None
            )
            entry["status"].append(int(result))
            self.set_dataset(key, table, broadcast=True, persist=True, archive=False)
        except Exception:
            logger.warning("Could not publish optimizer point", exc_info=True)

    def _on_recalibrated(self, committed_params: dict, metric) -> None:
        """Hook fired after a fix commits new optimal parameter values.

        ``committed_params`` maps each optimizable-parameter name to the value
        just persisted, and ``metric`` is the "data" the verification check
        measured with those values applied — the quantity at its optimum, which
        is what says whether the committed parameters are any good. It is None
        if the calibration measures no metric.

        The base implementation does nothing: qbutler stays agnostic about where
        recalibrations are recorded. Override this (e.g. via a downstream
        mix-in) to log them somewhere such as a database.

        Runs at the end of a successful fix; the caller swallows any exception,
        so an override need not be defensive, but must not have side effects
        that a calibration run depends on.
        """

    def _fire_recalibrated(self, committed_params: dict, metric) -> None:
        """Best-effort dispatch to :meth:`_on_recalibrated`; never raises."""
        try:
            self._on_recalibrated(dict(committed_params), metric)
        except Exception:
            logger.warning("_on_recalibrated hook failed", exc_info=True)

    def _on_checked(self, result: "CalibrationResult", metric, context: str) -> None:
        """Hook fired every time a check outcome is recorded.

        The counterpart of :meth:`_on_recalibrated` for checks: it sees every
        measurement qbutler records, passing or failing, so a downstream
        override can keep a history of how the apparatus is actually behaving
        rather than only of the moments it was recalibrated.

        Args:
            result: what the check concluded.
            metric: the "data" :meth:`check_own_state` returned alongside it
                (None if the calibration measures no metric).
            context: which kind of check this was — one of
                :data:`CHECK_CONTEXT_CHECK`, :data:`CHECK_CONTEXT_SWEEP`,
                :data:`CHECK_CONTEXT_VERIFY` or
                :data:`CHECK_CONTEXT_FIX_FAILED`. Worth honouring: an
                optimizer deliberately sweeps through bad points, so treating a
                ``sweep`` result as a statement about the apparatus's health
                would make a healthy system look broken every time it was
                fixed.

        Note that ``sweep`` points are only reported by the host optimizer
        loop. The resident kernel loop reports its trial points over its own
        per-point RPC (:meth:`_publish_optimizer_point`) without recording them
        as checks, so a kernel-mode fix fires this hook for its verification
        measurement only.

        The base implementation does nothing. The caller swallows any
        exception, so an override need not be defensive, but must not have side
        effects that a calibration run depends on — and must be cheap, since
        a host-mode fix fires it once per optimizer point.
        """

    def _fire_checked(self, result: "CalibrationResult", metric) -> None:
        """Best-effort dispatch to :meth:`_on_checked`; never raises."""
        try:
            self._on_checked(result, metric, self._check_context)
        except Exception:
            logger.warning("_on_checked hook failed", exc_info=True)

    @contextmanager
    def _checking_for(self, context: str):
        """Label the checks recorded inside this block (see :meth:`_on_checked`).

        Nests, and always restores the previous label, so a fix that raises
        mid-sweep does not leave later checks mislabelled as sweep points.
        """
        previous = self._check_context
        self._check_context = context
        try:
            yield
        finally:
            self._check_context = previous

    def _add_suspicion(self, suspector: str) -> None:
        """Mark this node suspect: ``suspector``'s fix failed while this node
        looked good, so the cached OK is no longer trusted and the next walk or
        monitor pass re-measures this node despite its timeout. Idempotent."""
        if suspector in self.__suspected_by:
            return
        self.__suspected_by.add(suspector)
        logger.warning(
            "%s is now SUSPECT: %s failed to calibrate while it looked good",
            self.__class__.__name__,
            suspector,
        )
        self._publish_status()

    def _retract_suspicion(self, suspector: str) -> None:
        """Withdraw ``suspector``'s suspicion of this node (it came good, so
        this node evidently was not its problem). The still-in-timeout cached
        OK becomes trusted again once no suspicions remain. No dataset write:
        the suspector's own :meth:`_publish_status` prunes the table."""
        if suspector not in self.__suspected_by:
            return
        self.__suspected_by.discard(suspector)
        logger.info(
            "%s is no longer suspected by %s%s",
            self.__class__.__name__,
            suspector,
            (
                ""
                if not self.__suspected_by
                else f" (still suspected by {sorted(self.__suspected_by)})"
            ),
        )

    def is_suspect(self) -> bool:
        """Whether this Calibration is currently SUSPECT: a dependent's fix
        conclusively failed while this node's cached check looked good, so the
        cached OK is not trusted until this node is re-measured or the
        dependent comes good."""
        return bool(self.__suspected_by)

    def _recall_status(self) -> bool:
        """Hydrate check state from :data:`STATUS_DATASET` (a previous worker
        process may have checked this calibration). Returns True on success."""
        try:
            entry = self.get_dataset(STATUS_DATASET, default={}, archive=False).get(
                self.__class__.__name__
            )
            if not entry or entry.get("last_check") is None:
                return False
            # pyon round-trips CalibrationResult as a string of its int value
            self.__most_recent_check_result = CalibrationResult(int(entry["status"]))
            self.__most_recent_check_timestamp = float(entry["last_check"])
            # Entries written before suspicion existed have no key: not suspect
            self.__suspected_by = set(entry.get("suspected_by", []))
            return True
        except Exception:
            logger.debug("Could not recall calibration status", exc_info=True)
            return False

    def _guess_own_state(self) -> CalibrationResult:
        if (
            self.__most_recent_check_result is None
            or self.__most_recent_check_timestamp is None
        ) and not self._recall_status():
            logger.debug(
                f"Guess own state of {self.__class__} failed: no checks have ever been done"
            )
            return CalibrationResult.BAD_EXPIRED

        time_now = time()
        expires_at = self.__most_recent_check_timestamp + self.__timeout
        if time_now > expires_at:
            logger.debug(
                "Guess own state of %s failed: data is stale (time = %s, expired at %s, timeout = %s)",
                self.__class__,
                time_now,
                expires_at,
                self.__timeout,
            )
            self.__most_recent_check_result = CalibrationResult.BAD_EXPIRED

        if (
            self.__most_recent_check_result == CalibrationResult.OK
            and self.__suspected_by
        ):
            # The cached OK is within its timeout but a dependent's fix failed,
            # so it is no longer trusted. Return-only — the cached result is
            # left intact, so if the suspicion is retracted without a
            # measurement (the dependent came good) the OK is trusted again.
            logger.debug(
                "Guess own state of %s: cached OK not trusted, suspected by %s",
                self.__class__.__name__,
                sorted(self.__suspected_by),
            )
            return CalibrationResult.BAD_EXPIRED

        return self.__most_recent_check_result

    def fix_own_state(self) -> None:
        """
        Attempt to fix this Calibration

        Attempt to optimize the output of this Calibration by calling
        :meth:`.check_own_state` and inspecting the "data" output
        while varying the optimizable parameters (see
        :meth:`.setattr_param_optimizable`). How this optimization occurs is an
        implementation detail.

        By the end of the optimization, the output of :meth:`check_own_state` should be
        :any:`CalibrationResult.OK`.

        Override this method to implement your own algorithm to make this
        Calibration "OK".

        Raises:
            CalibrationError:
                Raised if the algorithm fails to fix this Calibration. A fix
                walk treats this as "not fixed yet" and calls this method again
                (see :meth:`_fix_own_state_until_ok`), so raising it is not
                fatal unless the Calibration has a finite attempt budget.
        """

        if len(self.__optimizable_params) == 0:
            raise ValueError(
                f"Calibration {self.__class__} cannot be optimized because it has no optimizable params"
            )

        self._run_optimizer(self.__optimizer_func)

    def _run_optimizer(self, optimizer_func) -> None:
        """Run an optimizer generator against this Calibration's parameters."""
        pool = getattr(self, "_precompile_pool", None)
        if pool is not None and getattr(self, "_precompile_fix_key", None) is not None:
            # Pooled kernel fix: the resident optimizer loop was precompiled
            # once (at seed time) against persistent stores bound by
            # _kopt_bind_stores. Reuse that artifact — priming only resets the
            # per-fix state, so the embedded stores stay valid across fixes;
            # never re-override the params here (that would orphan the stores
            # the compiled kernel holds).
            self._kopt_prime(optimizer_func)
            self._drive_optimizer_kernel_loop(pool.get(self._precompile_fix_key))
            self._kopt_commit()
            return

        param_specs = self.__optimizable_params

        stores = {}
        for spec in param_specs:
            _, store = self.override_param(spec.name, spec.handle.get())
            stores[spec.name] = store

        if is_kernel(self.check_own_state):
            self._run_optimizer_kernel(optimizer_func, param_specs, stores)
        else:
            self._run_optimizer_host(optimizer_func, param_specs, stores)

    def _run_optimizer_host(self, optimizer_func, param_specs, stores) -> None:
        best_data = None
        best_params = None

        try:
            self._reset_optimizer_trace([spec.name for spec in param_specs])
            optimizer = optimizer_func(param_specs)

            try:
                param_dict = next(optimizer)
            except StopIteration as e:
                if e.value is not None:
                    best_params = e.value
                param_dict = None

            while param_dict is not None:
                self._pause_if_requested()

                for name, value in param_dict.items():
                    stores[name].set_value(value)

                with self._checking_for(CHECK_CONTEXT_SWEEP):
                    result, data = self._do_check_own_state()

                logger.debug(
                    "Optimizer point %s: result=%s, data=%s", param_dict, result, data
                )
                self._publish_optimizer_point(
                    [param_dict[spec.name] for spec in param_specs], result, data
                )

                if result == CalibrationResult.OK and self._is_better(data, best_data):
                    best_data = data
                    best_params = param_dict.copy()

                try:
                    param_dict = optimizer.send((result, data))
                except StopIteration as e:
                    if e.value is not None:
                        best_params = e.value
                    param_dict = None

            if best_params is None:
                raise CalibrationError("No valid parameters found")

            for name, value in best_params.items():
                self.set_dataset(
                    self._param_dataset_key_from_name(name),
                    value,
                    broadcast=True,
                    persist=True,
                )

            # Verify with best params still applied
            for name, value in best_params.items():
                stores[name].set_value(value)

            with self._checking_for(CHECK_CONTEXT_VERIFY):
                result, data = self._do_check_own_state()
            if result != CalibrationResult.OK:
                raise CalibrationError("Best parameters did not pass check")

            self._fire_recalibrated(best_params, data)

        finally:
            for spec in param_specs:
                self.reset_param(spec.name)
            self.recompute_param_defaults()

    def _run_optimizer_kernel(self, optimizer_func, param_specs, stores) -> None:
        """Run the optimizer when :meth:`check_own_state` is a kernel.

        The whole optimization runs as a *resident kernel loop* — a single
        kernel call, i.e. one compile + one upload, never a recompile between
        points. The kernel repeatedly asks the host for the next point
        (:meth:`_kopt_next_point`), applies it *on the core device* via the
        stores' ``@portable`` ``set_value()`` (so the update is seen by
        ``check_own_state()``; mutating the host-side store from an RPC does
        not work — the running kernel holds its own copy of the store),
        measures in-kernel and reports the result back. The optimizer
        strategy runs entirely on the host, so any generator works, including
        feedback optimizers that need each result to choose the next point.

        The loop ends with an in-kernel verification pass of the best
        parameters, which is free (same kernel, no extra compile).

        In kernel mode the "data" output of :meth:`check_own_state` must be a
        float (the optimization metric).
        """
        self._kopt_specs = param_specs
        self._kopt_stores = [stores[spec.name] for spec in param_specs]
        self._kopt_prime(optimizer_func)
        try:
            self._drive_optimizer_kernel_loop(self._optimizer_kernel_loop)
            self._kopt_commit()
        finally:
            self._kopt_cleanup()

    def _drive_optimizer_kernel_loop(self, run_loop: Callable[[], None]) -> None:
        """Run the resident optimizer loop, pausing between shots when asked.

        A kernel cannot pause itself — ``scheduler.pause()`` has to hand the
        core device over, so no kernel may be running — so instead the resident
        loop returns early when a between-shots check finds a pause pending
        (:meth:`_kopt_next_point`). We then pause here, with the core free, and
        re-enter: the loop resumes at the point it had not yet measured
        (:meth:`_kopt_entry_point`). A pause therefore costs one kernel
        re-upload and repeats no measurements.

        The loop always measures at least one point before it can bail out
        again, so the sweep still finishes even if the scheduler asks for a
        pause every single time.

        Args:
            run_loop: zero-arg callable running :meth:`_optimizer_kernel_loop`,
                either directly or as its precompiled artifact.

        Raises:
            TerminationRequested: if the run is terminated rather than
                preempted.
        """
        while True:
            run_loop()
            if not self._kopt_pause_pending:
                return
            self._pause_now()

    def _kopt_prime(self, optimizer_func) -> None:
        """Prime the host side of the resident kernel loop.

        Requires ``_kopt_specs`` / ``_kopt_stores`` to be bound already; only
        resets the per-fix state, so the store objects embedded in a compiled
        kernel stay valid across fixes.
        """
        self._kopt_generator = optimizer_func(self._kopt_specs)
        self._kopt_current_values = []
        self._kopt_best_values = None
        self._kopt_best_data = None
        self._kopt_verify_result = None
        self._kopt_verify_data = None
        self._kopt_pause_pending = False
        self._reset_optimizer_trace([spec.name for spec in self._kopt_specs])

    def _kopt_prime_default(self) -> None:
        """Prime the resident loop for this node's configured optimizer (used
        by the kernel-driven DAG fix)."""
        self._kopt_prime(self.__optimizer_func)

    def _kopt_commit(self) -> None:
        """Persist the best point found by the resident loop; raise if the
        optimization found nothing or the verification failed."""
        if self._kopt_best_values is None:
            raise CalibrationError("No valid parameters found")
        if self._kopt_verify_result != CalibrationResult.OK:
            raise CalibrationError("Best parameters did not pass check")
        best_params = {}
        for spec, store, value in zip(
            self._kopt_specs, self._kopt_stores, self._kopt_best_values
        ):
            best_params[spec.name] = value
            # The kernel applied the best values to its own copy of the
            # store; update the host copy to match.
            store.set_value(value)
            self.set_dataset(
                self._param_dataset_key_from_name(spec.name),
                value,
                broadcast=True,
                persist=True,
            )
        self._fire_recalibrated(best_params, self._kopt_verify_data)

    def _kopt_cleanup(self) -> None:
        param_specs = self._kopt_specs
        for attr in (
            "_kopt_specs",
            "_kopt_stores",
            "_kopt_generator",
            "_kopt_current_values",
            "_kopt_best_values",
            "_kopt_best_data",
            "_kopt_verify_result",
            "_kopt_verify_data",
            "_kopt_pause_pending",
        ):
            if hasattr(self, attr):
                delattr(self, attr)
        for spec in param_specs:
            self.reset_param(spec.name)
        self.recompute_param_defaults()

    @rpc
    def _kopt_load_current(self) -> TList(TFloat):
        """RPC: the optimizable params' current host values (the committed
        optimum after a fix, or the default before one)."""
        return [float(store.get_value()) for store in self._kopt_stores]

    @kernel
    def _check_with_current_params(self):
        """Pooled check for an optimized node.

        A precompiled kernel embeds object attribute values as of compile time
        and never writes them back, so a bare precompiled ``check_own_state``
        would keep measuring at the param value baked in when it was compiled —
        blind to any optimum a later fix commits. So pull the current param
        values over RPC and apply them on-core (the same ``@portable``
        ``set_value`` the optimizer loop uses) before measuring.
        """
        values = self._kopt_load_current()
        for i in range(len(self._kopt_stores)):
            self._kopt_stores[i].set_value(values[i])
        return self.check_own_state()

    @rpc
    def _kopt_entry_point(self) -> TList(TFloat):
        """RPC: the point the resident loop should measure first.

        Normally the optimizer's first point; after a scheduler pause, the
        point the loop bailed out on before measuring it, so that a pause
        neither skips a point nor measures one twice.
        """
        if self._kopt_pause_pending:
            self._kopt_pause_pending = False
            return list(self._kopt_current_values)
        return self._kopt_first_point()

    def _kopt_first_point(self) -> list:
        """The optimizer's first point, or [] if it has none."""
        try:
            param_dict = next(self._kopt_generator)
        except StopIteration as e:
            self._kopt_generator_returned(e)
            return []
        return self._kopt_set_current(param_dict)

    @rpc
    def _kopt_next_point(self, result, data) -> TList(TFloat):
        """RPC: record the point just measured, advance the host optimizer
        and return the next point ([] = stop looping).

        This is also where the scheduler is consulted, once per shot but
        rate-limited (:meth:`_scheduler_wants_pause`) — the loop runs on the
        core device with no other opportunity to yield. A pending pause stops
        the loop the same way a finished sweep does, by returning []; the host
        tells the two apart by ``_kopt_pause_pending`` and re-enters the loop
        after pausing.
        """
        result = CalibrationResult(int(result))
        logger.debug(
            "Optimizer point %s: result=%s, data=%s",
            self._kopt_current_values,
            result,
            data,
        )
        self._publish_optimizer_point(self._kopt_current_values, result, data)
        if result == CalibrationResult.OK and self._is_better(
            data, self._kopt_best_data
        ):
            self._kopt_best_data = data
            self._kopt_best_values = list(self._kopt_current_values)
        try:
            param_dict = self._kopt_generator.send((result, data))
        except StopIteration as e:
            self._kopt_generator_returned(e)
            return []
        values = self._kopt_set_current(param_dict)

        if self._scheduler_wants_pause():
            # Stop the kernel so the host can pause with the core device free.
            # `values` stays in _kopt_current_values as the point to resume on.
            self._kopt_pause_pending = True
            return []
        return values

    def _kopt_set_current(self, param_dict) -> list:
        self._kopt_current_values = [
            float(param_dict[spec.name]) for spec in self._kopt_specs
        ]
        return self._kopt_current_values

    def _kopt_generator_returned(self, stop: StopIteration) -> None:
        # Parity with the host loop: a generator may return an explicit best
        # point instead of relying on the best-seen tracking.
        if stop.value is not None:
            self._kopt_best_values = [
                float(stop.value[spec.name]) for spec in self._kopt_specs
            ]

    @rpc
    def _kopt_get_best(self) -> TList(TFloat):
        """RPC: the best values found, [] if none (verification is skipped).

        Also [] when the loop is unwinding for a scheduler pause: the sweep is
        not finished, so there is nothing to verify yet — the loop is re-entered
        after the pause and verifies then.
        """
        if self._kopt_pause_pending or self._kopt_best_values is None:
            return []
        return list(self._kopt_best_values)

    @rpc
    def _kopt_record_verify(self, result, data) -> None:
        """RPC: record the in-kernel verification measurement."""
        result = CalibrationResult(int(result))
        self._kopt_verify_result = result
        self._kopt_verify_data = data
        with self._checking_for(CHECK_CONTEXT_VERIFY):
            self._record_own_check(result, data)

    @kernel
    def _optimizer_kernel_loop(self):
        """Resident kernel loop: pull points from the host, apply them
        device-side, measure, report back; verify the best point before
        returning. One kernel call per fix, plus one more per scheduler pause
        (:meth:`_drive_optimizer_kernel_loop`)."""
        values = self._kopt_entry_point()
        while len(values) > 0:
            for i in range(len(self._kopt_stores)):
                self._kopt_stores[i].set_value(values[i])
            result, data = self._measure_with_transitory_retries()
            values = self._kopt_next_point(result, data)

        values = self._kopt_get_best()
        if len(values) > 0:
            for i in range(len(self._kopt_stores)):
                self._kopt_stores[i].set_value(values[i])
            result, data = self._measure_with_transitory_retries()
            self._kopt_record_verify(result, data)

    def _is_better(self, data: Any, best_data: Optional[Any]) -> bool:
        """Compare data value against current best based on optimization strategy."""
        if best_data is None:
            return True

        if not isinstance(data, (int, float)):
            return False

        strategy = self.optimization_type.get()
        if strategy == "max":
            return data > best_data
        elif strategy == "min":
            return data < best_data
        elif strategy == "zero":
            return abs(data) < abs(best_data)
        else:
            raise ValueError(f"Unknown optimization_type: {strategy}")

    def set_optimizer(
        self,
        optimizer_func: Callable[
            [list[ParamSpec]],
            Generator[dict, tuple[CalibrationResult, Any], Optional[dict]],
        ],
    ) -> None:
        """
        Set a custom optimizer for this Calibration.

        Can only be called during build_calibration(). The optimizer func will
        be called on the host, not the core, so can use fancy python features.

        Args:
            optimizer_func: A generator function that yields param dicts and
            receives (result, data). It always runs on the host — kernel-mode
            calibrations stream its points into the resident kernel over RPC —
            so feedback strategies (Bayesian optimization etc.) work the same
            as simple sweeps.
        """
        if not self.__in_build_calibration:
            raise TypeError("This method must only be called in build_calibration()")

        self.__optimizer_func = optimizer_func
