"""A measurement that raises an ndscan ``TransitoryError`` is simply repeated;
an ``RTIOUnderflow`` is not.

The distinction is deliberate. A transitory error means the apparatus is fine
and this particular shot needs taking again, so repeating it costs nothing but
time. An underflow is a real timing failure: only the fragment that raised it
can judge it benign, and the way to say so is to convert it to a
``TransitoryError`` there (see
:meth:`qbutler.calibration.Calibration._measure_with_transitory_retries`).
"""

import pytest
from artiq.coredevice.exceptions import RTIOUnderflow
from artiq.experiment import TBool
from artiq.experiment import kernel
from artiq.experiment import rpc
from ndscan.experiment.fragment import TransitoryError

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


def make_flaky_class(failures, exception):
    """A calibration whose check raises ``exception`` on its first ``failures``
    shots and then measures OK, counting every shot it takes."""

    class Flaky(Calibration):
        def build_calibration(self):
            self.shots = 0
            self.failures = failures

        def check_own_state(self):
            self.shots += 1
            if self.shots <= self.failures:
                raise exception("synthetic failure")
            return CalibrationResult.OK, None

    return Flaky


def test_transitory_error_repeats_the_shot(fragment_factory):
    c = fragment_factory(make_flaky_class(3, TransitoryError))

    assert c.check_state()[0] == CalibrationResult.OK
    assert c.shots == 4


def test_transitory_error_gives_up_at_the_limit(fragment_factory):
    c = fragment_factory(make_flaky_class(99, TransitoryError))
    c.max_transitory_error_retries = 2

    with pytest.raises(TransitoryError):
        c.check_state()

    assert c.shots == 3


def test_underflow_is_not_repeated(fragment_factory):
    c = fragment_factory(make_flaky_class(1, RTIOUnderflow))

    with pytest.raises(RTIOUnderflow):
        c.check_state()

    assert c.shots == 1


class TransitoryKernelCalibration(Calibration):
    """Kernel-mode optimizable calibration whose first ``failures`` shots raise
    a transitory error. The optimum sits at 7.0, as in
    :mod:`tests.func.kernel_calibrations`."""

    def build_calibration(self):
        self.setattr_device("core")
        self.setattr_param_optimizable(
            "param1",
            "Test param",
            min=0.0,
            max=10.0,
            default=5.0,
        )
        self.shots = 0
        self.failures = 0

    @rpc
    def _shot_fails(self) -> TBool:
        self.shots += 1
        return self.shots <= self.failures

    @kernel
    def check_own_state(self):
        if self._shot_fails():
            raise TransitoryError("synthetic failure")
        p = self.param1.get()
        data = 10.0 - abs(p - 7.0)
        if data > 8.0:
            return CalibrationResult.OK, data
        return CalibrationResult.BAD_DATA, data


@pytest.mark.withartiq
def test_resident_optimizer_loop_repeats_transitory_shots(fragment_factory):
    """The resident kernel loop repeats the shot in-kernel and still finds the
    optimum, rather than unwinding the whole fix walk."""
    c = fragment_factory(TransitoryKernelCalibration)
    c.failures = 2

    c.fix_own_state()

    assert c.check_own_state()[0] == CalibrationResult.OK
    # Two wasted shots, then the sweep proper.
    assert c.shots > 2


@pytest.mark.withartiq
def test_resident_optimizer_loop_gives_up_at_the_limit(fragment_factory):
    """The in-kernel repeat is bounded: the shot count stops dead at the limit
    rather than looping forever."""
    c = fragment_factory(TransitoryKernelCalibration)
    c.failures = 99
    c.max_transitory_error_retries = 2

    with pytest.raises(TransitoryError):
        c.fix_own_state()

    assert c.shots == 3
