import itertools
import logging
import math
import warnings
from dataclasses import dataclass
from enum import Enum
from enum import Flag
from enum import auto
from time import time
from typing import Any
from typing import Callable
from typing import Generator
from typing import Optional
from typing import Tuple
from typing import Type

import numpy as np
from ndscan.experiment import ExpFragment
from ndscan.experiment import OpaqueChannel
from ndscan.experiment.parameters import FloatParam
from ndscan.experiment.parameters import FloatParamHandle
from ndscan.experiment.parameters import FloatParamStore
from ndscan.experiment.parameters import ParamHandle
from ndscan.experiment.parameters import StringParam

from . import dag
from . import patch_ndscan  # noqa

logger = logging.getLogger(__name__)


NUM_SCAN_POINT = 11


class CalibrationError(RuntimeError):
    pass


@dataclass
class ParamSpec:
    name: str
    min: float
    max: float
    handle: FloatParamHandle


class OptimizationStrategy(Enum):
    MAXIMIZE = auto()
    MINIMIZE = auto()
    ZERO = auto()


class CalibrationResult(int, Flag):
    OK = 0

    BAD_EXPIRED = auto()
    BAD_DEPS = auto()
    BAD_DATA = auto()
    BAD = BAD_EXPIRED | BAD_DEPS | BAD_DATA

    INVALID_DATA = auto()


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

    def __repr__(self) -> str:
        return self.__class__.__name__

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
        self.__optimizable_params = []
        self.__optimizer_func = None
        self.__most_recent_check_timestamp = None
        self.__most_recent_check_result = None
        self.__most_recent_check_data = None
        self.__optimization_type = "max"

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
            return TypeError("This method must only be called in build_calibration()")

        dataset_key = self._param_dataset_key_from_name(name)

        p = self.setattr_param(
            name,
            FloatParam,
            description,
            default=f'dataset("{dataset_key}", default={default})',
            *args,
            **kwargs,
        )
        self.__optimizable_params.append((min, max, p))
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
            setattr(self, name, cals_from_cache[0])

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
            return TypeError("This method must only be called in build_calibration()")

        self.__timeout = timeout

    def get_timeout(self) -> float:
        """
        Gets the timeout set by :meth:`set_timeout`

        Returns:
            float: The timeout in seconds
        """
        return self.__timeout

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
        # Iterate over the dependencies, starting with the ones furthest away,
        # and check their states, ending with this object
        r = CalibrationResult.OK
        data = None

        deps = dag.get_dependencies(self)
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

    def fix_state(self, force=False):
        """
        Fix the state of this Calibration and dependents

        This method will perform quick measurements where necessairy to update
        any expired / bad / invalid Calibrations. If force==True, this step is
        skipped.

        For any Calibrations that fail the check, or if force==True,
        :meth:`fix_own_state` will be called on each, starting from the most
        basic. After this, :meth`check_own_state` will be called again and the
        algorithm will either exist with an error or continue on success.

        Args:
            force (bool, optional): Check all dependents, even if they should be
                                    fine. Defaults to False.

        Returns:
            CalibrationResult:
                Result of the checks. If continue_on_fail was
                passed, this is the bitwise combination of all
                results; otherwise it is the first bad result,
                or OK.
        """
        # Iterate over the dependencies, starting with the ones furthest away,
        # and check their states, ending with this object
        deps = dag.get_dependencies(self)
        logger.debug(f"Fixing all dependencies of {self.__class__.__name__}")
        for dep in deps:
            current_state = dep._guess_own_state()
            logger.debug(f"Guessed state of {dep.__class__.__name__} = {current_state}")

            if current_state & CalibrationResult.BAD_EXPIRED and not force:
                current_state, current_data = dep._do_check_own_state()

            if current_state != CalibrationResult.OK or force:
                logger.debug(f"Attempting fix of {dep.__class__.__name__}")

                dep.fix_own_state()
                current_state, current_data = dep._do_check_own_state()

                logger.debug(
                    "Result of fix of %s = %s", dep.__class__.__name__, current_state
                )

                if current_state != CalibrationResult.OK:
                    self.__most_recent_check_result = CalibrationResult.BAD_DEPS
                    self.__most_recent_check_timestamp = time()
                    self.__most_recent_check_data = current_data

                    raise CalibrationError(
                        f"Calibration of {dep.__class__.__name__} failed"
                    )

    def _do_check_own_state(self) -> Tuple[CalibrationResult, Any]:
        (
            self.__most_recent_check_result,
            self.__most_recent_check_data,
        ) = self.check_own_state()
        self.__most_recent_check_timestamp = time()

        logger.debug(
            "Checked own state of %s: result %s/%s at time %s",
            self.__class__.__name__,
            self.__most_recent_check_result,
            self.__most_recent_check_data,
            self.__most_recent_check_timestamp,
        )

        return self.__most_recent_check_result, self.__most_recent_check_data

    def _guess_own_state(self) -> CalibrationResult:
        if (
            self.__most_recent_check_result is None
            or self.__most_recent_check_timestamp is None
        ):
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
                Raised if the algorithm fails to fix this Calibration.
        """

        if len(self.__optimizable_params) == 0:
            raise ValueError(
                f"Calibration {self.__class__} cannot be optimized because it has no optimizable params"
            )

        if self.__optimizer_func is not None:
            return self._run_custom_optimizer()
        else:
            self._run_grid_search()

    def _run_grid_search(self, num_points: int = NUM_SCAN_POINT) -> None:
        """Built-in N-dimensional grid search using itertools.product."""
        param_specs = self.__optimizable_params
        n_params = len(param_specs)

        n_points = num_points**n_params
        if n_points > 500:
            warnings.warn(
                f"Grid search will evaluate {n_points} points ({n_params}D × {num_points} points). "
                "Consider using a custom optimizer for high-dimensional spaces.",
                UserWarning,
            )

        # Build axes: list of linspaces for each parameter
        axes = [
            np.linspace(min_v, max_v, num_points) for min_v, max_v, _ in param_specs
        ]
        names = [handle.name for _, _, handle in param_specs]

        logger.debug(
            "Running grid search over %s parameters: %s",
            n_params,
            names,
        )

        # Override all params
        stores = {}
        for _, _, handle in param_specs:
            _, store = self.override_param(handle.name, handle.get())
            stores[handle.name] = store

        try:
            best_data = None
            best_params = None
            best_status = None
            best_non_ok_data = None
            best_non_ok_params = None

            # Iterate through cartesian product of all parameter axes
            for point in itertools.product(*axes):
                params = dict(zip(names, point))

                # Apply parameters
                for name, value in params.items():
                    stores[name].set_value(value)

                # Measure
                result, data = self._do_check_own_state()

                logger.debug(
                    "Grid search point %s: result=%s, data=%s",
                    params,
                    result,
                    data,
                )

                # Track best (only if OK)
                if result == CalibrationResult.OK:
                    if self._is_better(data, best_data):
                        best_data = data
                        best_params = params.copy()
                        best_status = result
                else:
                    if best_non_ok_data is None or self._is_better(
                        data, best_non_ok_data
                    ):
                        best_non_ok_data = data
                        best_non_ok_params = params.copy()

            if best_params is None:
                msg = "No valid parameters found in grid search"
                if best_non_ok_params is not None:
                    msg += f". Best non-OK params: {best_non_ok_params}"
                raise CalibrationError(msg)

            # Save best params to datasets
            for name, value in best_params.items():
                self.set_dataset(
                    self._param_dataset_key_from_name(name),
                    value,
                    broadcast=True,
                    persist=True,
                )

        finally:
            # Reset params
            for _, _, handle in param_specs:
                self.reset_param(handle.name)
            self.recompute_param_defaults()

        # Verify: explicitly apply best params before checking
        if best_params is not None:
            for name, value in best_params.items():
                stores[name].set_value(value)

        result, data = self._do_check_own_state()
        if result != CalibrationResult.OK:
            raise CalibrationError("Best parameters did not pass check")

    def _run_custom_optimizer(self) -> None:
        """Run a user-provided optimizer generator."""
        param_specs = [
            ParamSpec(name=handle.name, min=min_v, max=max_v, handle=handle)
            for min_v, max_v, handle in self.__optimizable_params
        ]

        # Override all params
        stores = {}
        for spec in param_specs:
            _, store = self.override_param(spec.name, spec.handle.get())
            stores[spec.name] = store

        best_data = None
        best_params = None

        try:
            # Instantiate generator
            optimizer = self.__optimizer_func(param_specs)

            # Prime generator
            try:
                param_dict = next(optimizer)
            except StopIteration as e:
                if e.value is not None:
                    best_params = e.value
                param_dict = None

            # Main loop
            while param_dict is not None:
                # Validate
                self._validate_param_dict(param_dict, param_specs)

                # Apply
                for name, value in param_dict.items():
                    stores[name].set_value(value)

                # Measure
                result, data = self._do_check_own_state()

                # Track best
                if result == CalibrationResult.OK and self._is_better(data, best_data):
                    best_data = data
                    best_params = param_dict.copy()

                # Send result, get next params
                try:
                    param_dict = optimizer.send((result, data))
                except StopIteration as e:
                    if e.value is not None:
                        best_params = e.value
                    param_dict = None

        except CalibrationError:
            raise
        except Exception as e:
            logger.exception("Optimizer failed")
            raise CalibrationError(f"Optimizer failed: {e}") from e

        finally:
            # Always cleanup
            for spec in param_specs:
                self.reset_param(spec.name)
            self.recompute_param_defaults()

        # Apply best
        if best_params is None:
            raise CalibrationError("No valid parameters found")

        for name, value in best_params.items():
            self.set_dataset(
                self._param_dataset_key_from_name(name),
                value,
                broadcast=True,
                persist=True,
            )

        # Verify
        if best_params is not None:
            for name, value in best_params.items():
                stores[name].set_value(value)

        result, data = self._do_check_own_state()
        if result != CalibrationResult.OK:
            raise CalibrationError("Best parameters did not pass check")

    def _validate_param_dict(
        self, param_dict: dict, param_specs: list[ParamSpec]
    ) -> None:
        """Validate that yielded parameter dict is well-formed."""
        expected_names = {spec.name for spec in param_specs}
        actual_names = set(param_dict.keys())

        if actual_names != expected_names:
            missing = expected_names - actual_names
            extra = actual_names - expected_names
            raise ValueError(
                f"Invalid parameter dict: missing={missing}, extra={extra}"
            )

        for spec in param_specs:
            value = param_dict[spec.name]
            if not isinstance(value, (int, float)):
                raise TypeError(
                    f"Parameter {spec.name} must be numeric, got {type(value)}"
                )
            if not (spec.min <= value <= spec.max):
                raise ValueError(
                    f"Parameter {spec.name}={value} out of bounds [{spec.min}, {spec.max}]"
                )

    def _is_better(self, data: Any, best_data: Optional[Any]) -> bool:
        """Compare data value against current best based on optimization strategy."""
        if best_data is None:
            return True

        if not isinstance(data, (int, float)):
            return False

        strategy = self._get_optimization_strategy()

        if strategy == OptimizationStrategy.MAXIMIZE:
            return data > best_data
        elif strategy == OptimizationStrategy.MINIMIZE:
            return data < best_data
        elif strategy == OptimizationStrategy.ZERO:
            return abs(data) < abs(best_data)
        else:
            raise ValueError(f"Unknown optimization strategy: {strategy}")

    def _get_optimization_strategy(self) -> OptimizationStrategy:
        """Map legacy string optimization_type to OptimizationStrategy enum."""
        strategy_str = self.optimization_type.get()

        if strategy_str == "max":
            return OptimizationStrategy.MAXIMIZE
        elif strategy_str == "min":
            return OptimizationStrategy.MINIMIZE
        elif strategy_str == "zero":
            return OptimizationStrategy.ZERO
        else:
            raise ValueError(f"Unknown optimization_type: {strategy_str}")

    def set_optimizer(
        self,
        optimizer_func: Callable[
            [list[ParamSpec]],
            Generator[dict, tuple[CalibrationResult, Any], Optional[dict]],
        ],
    ) -> None:
        """
        Set a custom optimizer for this Calibration.

        Can only be called during build_calibration().

        Args:
            optimizer_func: A generator function that yields param dicts and receives (result, data)
        """
        if not self.__in_build_calibration:
            raise TypeError("This method must only be called in build_calibration()")

        self.__optimizer_func = optimizer_func
