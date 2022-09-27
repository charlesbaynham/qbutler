import logging
from enum import auto
from enum import Flag
from time import time
from typing import Type

import numpy as np
from ndscan.experiment import ExpFragment
from ndscan.experiment import FloatChannel
from ndscan.experiment import OpaqueChannel
from ndscan.experiment import run_fragment_once
from ndscan.experiment.parameters import FloatParam
from ndscan.experiment.parameters import FloatParamHandle
from ndscan.experiment.parameters import FloatParamStore
from ndscan.experiment.parameters import ParamHandle
from ndscan.experiment.parameters import StringParam

from . import dag
from . import patch_ndscan  # noqa

logger = logging.getLogger(__name__)


NUM_SCAN_POINT = 10


class CalibrationError(RuntimeError):
    pass


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
    2. Its status can be checked by running :meth:`run_once` (you must implement
       this method)
    3. It can depend on other Calibrations (add them using
       :meth:`Calibration.add_dependency` in :meth:`.build_calibration`)
    4. It can be repaired / optimized by running :meth:`Calibration.fix_state`
    5. It is also a valid ndscan :class:`~ndscan.experiment.fragment.Fragment`,
       and so can be scanned using the usual ndscan interface

    To write a new Calibration object, you must implement the :meth:`run_once`
    method. If you need custom logic for checking state or calibrating, you can
    also override :meth:`check_own_state` or :meth:`fix_own_state`. See the
    documentation for each method for details of the interface.

    As a fully qualified :class:`~ndscan.experiment.fragment.Fragment`, you are
    also entitled to implement the ndscan methods such as
    :meth:`~ndscan.experiment.fragment.Fragment.device_setup`,
    :meth:`~ndscan.experiment.fragment.Fragment.host_setup`, etc.
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

    def run_once(self) -> None:
        """
        Measure the status of this :class:`.Calibration`

        You must override this method to implement the logic that allows this Calibration
        to

        a) check if it is "OK"

        b)  *(optional)* measure a number that can be used to quantify this
            Calibraiton's status

        This method must measure the state of the system somehow, then output a
        :class:`.CalibrationResult` to the :class:`.ResultsChannel` "status". It
        should also, optionally, output a float to the :class:`.ResultsChannel`
        "data" which could be used to optimize the :class:`.Calibration`.

        This method has access to the usual :mod:`ndscan` preparations such as
        :meth:`~ndscan.experiment.fragment.Fragment.device_setup`,
        :meth:`~ndscan.experiment.fragment.Fragment.host_setup` etc, and may be
        a kernel. See the documentation for
        :class:`~ndscan.experiment.fragment.Fragment` for details.

        TODO: Confirm that CalibrationResult types work on kernels
        """
        raise NotImplementedError

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
        self.__most_recent_check_timestamp = None
        self.__most_recent_check_result = None
        self.__optimization_type = "max"

        # Add results channels for measurements of the Calibration's state
        self.setattr_result("status", OpaqueChannel)
        self.setattr_result("data")
        self.status: OpaqueChannel
        self.data: FloatChannel

        self.__in_build_calibration = True
        self.build_calibration()
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
        Calibration, based on the "data" result output from :meth:`.run_once`.

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
        self, dep_calibration_class: Type["Calibration"], name: str = None
    ) -> None:
        """
        Add a dependency of this Calibration

        This method can only be called during the build() phase.

        Adds another Calibration as a dependency of this one. This method can be
        called multiple times to add multiple dependencies.

        Note that this method should be passed the dependency's *class*, not an
        instantiated object.

        Args:
            dep_calibration (Type["Calibration"]): The Calibration class to add as a dependency
        """
        if not self.__in_build_calibration:
            raise TypeError("This method must only be called in build_calibration()")

        if name is None:
            name = dep_calibration_class.__name__

        cal_from_cache = dag.get_calibration_from_type(dep_calibration_class)

        if cal_from_cache is not None:
            # If this Calibration has been already created elsewhere, don't make a
            # new copy. Instead, add the existing version as an attribute
            setattr(self, name, cal_from_cache)
        else:
            # Otherwise, initialize it as a new subfragment and add it to our
            # DAG machinery
            self.setattr_fragment(name, dep_calibration_class)

            dep_calibration_object = getattr(self, name)
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
            return TypeError("This method must only be called in build_calibration()")

        self.__timeout = timeout

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

    def check_state(self, force=False, continue_on_fail=False) -> CalibrationResult:
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
                Result of the checks. If continue_on_fail was
                passed, this is the bitwise combination of all results;
                otherwise it is the first bad result, or OK.
        """
        # Iterate over the dependencies, starting with the ones furthest away,
        # and check their states, ending with this object
        r = CalibrationResult.OK

        deps = dag.get_dependencies(self)
        for dep in deps:
            current_state = dep._guess_own_state()
            if force or current_state != CalibrationResult.OK:
                r |= dep._do_check_own_state()
                if r != CalibrationResult.OK and not continue_on_fail:
                    return r

        return r

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
                current_state = dep.check_own_state()
                logger.debug(
                    f"Checked state of {dep.__class__.__name__} = {current_state}"
                )

            if current_state != CalibrationResult.OK or force:
                logger.debug(f"Attempting fix of {dep.__class__.__name__}")

                dep.fix_own_state()
                current_state = dep.check_own_state()

                logger.debug(
                    "Result of fix of %s = %s", dep.__class__.__name__, current_state
                )

                if current_state != CalibrationResult.OK:
                    self.__most_recent_check_result = CalibrationResult.BAD_DEPS
                    self.__most_recent_check_timestamp = time()

                    raise CalibrationError(
                        f"Calibration of {dep.__class__.__name__} failed"
                    )

    def _do_check_own_state(self) -> CalibrationResult:
        self.__most_recent_check_result = self.check_own_state()
        self.__most_recent_check_timestamp = time()

        logger.debug(
            f"Checked own state of {self.__class__}: result {self.__most_recent_check_result} at time {self.__most_recent_check_timestamp}"
        )

        return self.__most_recent_check_result

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

    def check_own_state(self) -> CalibrationResult:
        """
        Check the state of this Calibration

        This default implementation checks the state of this Calibration by
        running the :meth:`~.run_once` method and collecting the results from
        the two :class:`~ndscan.experiment.result_channels.ResultChannel`
        channels: "data" and "status".

        If you prefer, you can override this method to implement your own check
        logic.

        Returns:
            CalibrationResult:  Result of the check
        """
        results = run_fragment_once(self)
        status = results[self.status]

        if status is None:
            raise NotImplementedError(
                "run_once methods must push a CalibrationResult state to the 'status' OutputChannel"
            )

        return status

    def fix_own_state(self) -> None:
        """
        Attempt to fix this Calibration

        Attempt to optimize the output of this Calibration by calling
        :meth:`.run_once` and inspecting the "data" :class:`.ResultsChannel`
        while varying the optimizable parameters (see
        :meth:`.setattr_param_optimizable`). How this optimization occurs is an
        implementation detail.

        By the end of the optimization, the output of :meth:`run_once` should be
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
        elif len(self.__optimizable_params) > 1:
            raise NotImplementedError(
                f"Calibration {self.__class__} cannot be optimized because optimizations of >1 params have not yet been implemented"
            )

        p_min, p_max, p_handle = self.__optimizable_params[0]
        p_handle: FloatParamHandle

        points = np.linspace(p_min, p_max, NUM_SCAN_POINT).tolist()

        # Override the parameter we're scanning to a new ParamStore
        _, p_store = self.override_param(p_handle.name, p_min)
        p_store: FloatParamStore

        output_status = []
        output_data = []

        for point in points:
            p_store.set_value(point)
            results = run_fragment_once(self)
            output_status.append(results[self.status])
            output_data.append(results[self.data])

        strategy = self.optimization_type.get()

        if strategy != "max":
            # TODO: implement "min" and "zero" strategies
            raise NotImplementedError("Not done yet")

        # Set the best param
        k_max = np.argmax(output_data)
        best_val = output_data[k_max]

        self.set_dataset(
            self._param_dataset_key_from_name(p_handle.name),
            best_val,
            broadcast=True,
            persist=True,
        )

        if output_status[k_max] != CalibrationResult.OK:
            raise CalibrationError("The best parameters found did not pass the check")

        self.reset_param(p_handle.name)
        self.recompute_param_defaults()

    def _get_dag(self):
        return dag.get_graph_containing_calibration(self)
