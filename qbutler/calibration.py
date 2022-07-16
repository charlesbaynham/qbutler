from enum import auto
from enum import Flag
from typing import Iterable
from typing import Type
from typing import Union

from ndscan.experiment import ExpFragment
from ndscan.experiment.parameters import FloatParam
from ndscan.experiment.parameters import ParamHandle


class CalibrationResult(Flag):
    OK = auto()

    BAD_EXPIRED = auto()
    BAD_DEPS = auto()
    BAD_DATA = auto()
    BAD = BAD_EXPIRED | BAD_DEPS | BAD_DATA

    INVALID_DATA = auto()


class Calibration(ExpFragment):
    def __init__(self, *args, **kwargs) -> None:
        self._timeout = 0
        self._dependencies = []
        self._optimizable_params = []
        super().__init__(*args, **kwargs)

    def setattr_param_optimizable(
        self,
        name: str,
        param_class: Type[FloatParam],
        description: str,
        min: float,
        max: float,
        *args,
        **kwargs
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
            param_class (Type[FloatParam]): The type of parameter to
            instantiate. description (str): The human-readable parameter name.
            min (float): Minimum value for the optimizer to try max (float):
            Maximum value for the optimizer to try args: Any extra arguments to
            pass to the ``param_class`` constructor. kwargs: Any extra keyword
            arguments to pass to the the ``param_class``
                    constructor.

        Returns:
            ParamHandle: The newly created parameter handle.
        """

        p = self.setattr_param(name, param_class, description, *args, **kwargs)
        self._optimizable_params.append((min, max, p))
        return p

    def add_dependency(
        self, dep_calibration: Union[Iterable[Type["Calibration"]], Type["Calibration"]]
    ) -> None:
        """
        Add a dependency of this Calibration

        This method can only be called during the build() phase.

        Adds another Calibration as a dependency of this one. This method can be
        called multiple times to add multiple dependencies, or it can be passed
        an Iterable.

        Note that this method should be passed the dependency's *class*, not an
        instantiated object.

        Args:
            dep_calibration (Type[&quot;Calibration&quot;]): _description_
        """
        if isinstance(dep_calibration, Iterable):
            self._dependencies += dep_calibration
        else:
            self._dependencies.append(dep_calibration)

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

        If this method is not called, timeout default to 0 seconds.

        Args:
            timeout (float): _description_
        """
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
        raise NotImplementedError  # TODO: This need to be written here, not by the user

    def check_state(self) -> CalibrationResult:
        raise NotImplementedError

    def calibrate(self) -> CalibrationResult:
        raise NotImplementedError
