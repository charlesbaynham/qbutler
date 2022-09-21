from enum import auto
from enum import Flag
from time import time
from typing import List
from typing import Type

import networkx as nx
from ndscan.experiment import Fragment
from ndscan.experiment.parameters import FloatParam
from ndscan.experiment.parameters import ParamHandle

from .dag import add_to_dependency_map
from .dag import graph_containing_calibration


class CalibrationResult(Flag):
    OK = auto()

    BAD_EXPIRED = auto()
    BAD_DEPS = auto()
    BAD_DATA = auto()
    BAD = BAD_EXPIRED | BAD_DEPS | BAD_DATA

    INVALID_DATA = auto()


class Calibration(Fragment):
    def build_calibration(self):
        """
        Set parameters / options / results channels for the calibration

        As with ndscan's build_fragment() or ARTIQ's build(), this method set up
        the Calibration by defining things like:

        * What parameters it needs
        * Which parameters can be optimised
        * What timeout applies
        * What other Calibrations this one depends on

        Apart from Calibration methods, you can also call any :module:`ndscan`
        methods available for build_fragment() in this method.

        Raises:
            NotImplementedError: Raised if the user did not override this method
                                 - you must write this method for your own
                                   classes.
        """
        raise NotImplementedError

    def run_once(self) -> None:
        raise NotImplementedError  # TODO: decide what to do with run_once()

    def build_fragment(self, *args, **kwargs) -> None:
        """
        Set up the calibration
        """
        self.__timeout = 0
        self.__dependencies = []
        self.__optimizable_params = []
        self.__most_recent_calibration_timestamp = None
        self.__most_recent_data_timestamp = None
        self.__most_recent_data_result = None

        self.__in_build_calibration = True
        self.build_calibration()
        self.__in_build_calibration = False

        # Register this Calibration as having been built
        add_to_dependency_map(self, None)

    def setattr_param_optimizable(
        self, name: str, description: str, min: float, max: float, *args, **kwargs
    ) -> ParamHandle:
        """Create an ndscan parameter that's available for optimization by the
        calibrator

        This method can only be called during the build() phase.

        The syntax for this method is exactly the same as for :method:
        `ndscan.experiment.Fragment.setattr_param`, but also requires minimum
        and maximum bounds for the optimizer. Note that these may differ from
        the min/max bounds specified by the param_class instance.

        For now, only :class:`ndscan.experiment.parameters.FloatParam`s are
        supported.

        Parameters created via this method will behave exactly the same as
        normal ndscan parameters, except they'll also be optimized during
        calibrate() routines.

        Args:
            name (str): The parameter name, to be part of its FQN. Must be a
            valid Python
                        identifier; the parameter handle will be accessible as
                        ``self.<name>``.
            description (str): The human-readable parameter name.
            min (float): Minimum value for the optimizer to try
            max (float): Maximum value for the optimizer to try
            args: Any extra arguments to pass to the ``param_class`` constructor.
            kwargs: Any extra keyword arguments to pass to the the ``param_class``
                    constructor.

        Returns:
            ParamHandle: The newly created parameter handle.
        """
        if not self.__in_build_calibration:
            return TypeError("This method must only be called in build_calibration()")

        p = self.setattr_param(name, FloatParam, description, *args, **kwargs)
        self._optimizable_params.append((min, max, p))
        return p

    def add_dependency(
        self, dep_calibration: Type["Calibration"], name: str = None
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
            return TypeError("This method must only be called in build_calibration()")

        if name is None:
            name = dep_calibration.__name__

        self.setattr_fragment(name, dep_calibration)

        add_to_dependency_map(self, dep_calibration)
        self.__dependencies.append(dep_calibration)

    def set_timeout(self, timeout: float):
        """
        Set the timeout after which previously performed calibration checks
        become invalid.

        This method can only be called during the build() phase.

        After this timeout has elapsed, future calls to
        :method:`Calibration.guess_state` will return a
        :class:`CalibrationResult` of type BAD_EXPIRED. This will trigger a
        check of the process via a call to :method: `Calibration.check_state` on
        the next request.

        If this method is not called, timeout defaults to 0 seconds.

        Args:
            timeout (float): _description_
        """
        if not self.__in_build_calibration:
            return TypeError("This method must only be called in build_calibration()")

        self.timeout = timeout

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
        for dep in self._get_dependencies():
            state = dep.guess_own_state()
            if not (state & CalibrationResult.OK):
                return state

        return self.guess_own_state()

    def check_state(self, force=False, continue_on_fail=False) -> CalibrationResult:
        """
        Check the state of this Calibration and dependents

        This method will perform quick measurements where necessairy to update
        any expired / bad / invalid Calibrations. If a dependent Calibration is
        still within its timeout, it won't be checked unless force==True.

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
            CalibrationResult:  Result of the checks. If continue_on_fail was
                                passed, this is the bitwise combination of all results; otherwise it
                                is the first bad result, or OK.
        """
        # Iterate over the dependencies, starting with the ones furthest away,
        # and check their states, ending with this object
        r = CalibrationResult.OK
        for dep in self._get_dependencies():
            current_state = dep.guess_own_state()
            if force or current_state != CalibrationResult.OK:
                r |= dep._do_check_own_state()
                if r != CalibrationResult.OK and not continue_on_fail:
                    return r

        return r

    def _do_check_own_state(self) -> CalibrationResult:
        self.__most_recent_data_result = self.check_own_state()
        self.__most_recent_data_timestamp = time()
        return self.__most_recent_data_result

    def guess_own_state(self) -> CalibrationResult:
        if (
            self.__most_recent_data_result is None
            or self.__most_recent_calibration_timestamp is None
        ):
            return CalibrationResult.BAD_EXPIRED

        if self.__most_recent_data_timestamp + self.__timeout < time():
            self.__most_recent_data_result = CalibrationResult.BAD_EXPIRED

        return self.__most_recent_data_result

    def check_own_state(self) -> CalibrationResult:
        raise NotImplementedError

    def calibrate_self(self) -> CalibrationResult:
        raise NotImplementedError

    def _get_dag(self):
        return graph_containing_calibration(self)

    def _get_dependencies(self, furthest_first=True) -> List["Calibration"]:
        """
        Return a list of this Calibration's dependencies, including this calibration itself
        """
        # Get a dict of lengths of paths to all dependencies
        paths = nx.single_source_shortest_path(self._get_dag(), self)

        # Convert to a list of tuples of (target, distance) with the furthest ones first
        targets_and_distances = [(t, len(p)) for t, p in paths.items()]
        targets_and_distances = sorted(
            targets_and_distances, key=lambda d: d[1], reverse=furthest_first
        )

        return [t for t, _ in targets_and_distances]
