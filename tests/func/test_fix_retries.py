"""A calibration that does not come good on the first attempt is retried, not
fatal (see :meth:`qbutler.calibration.Calibration._fix_own_state_until_ok`)."""

import pytest

from qbutler import calibration as calibration_module
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationError
from qbutler.calibration import CalibrationResult
from qbutler.calibration import fix_targets


def make_stubborn_class(attempts_needed, max_fix_attempts="unset"):
    """A calibration that stays BAD until it has been fixed ``attempts_needed``
    times, counting its fixes and checks."""

    class Stubborn(Calibration):
        def build_calibration(self):
            self.fixes = 0
            self.checks = 0
            self.attempts_needed = attempts_needed
            if max_fix_attempts != "unset":
                self.set_max_fix_attempts(max_fix_attempts)

        def check_own_state(self):
            self.checks += 1
            if self.fixes >= self.attempts_needed:
                return CalibrationResult.OK, None
            return CalibrationResult.BAD_DATA, None

        def fix_own_state(self) -> None:
            self.fixes += 1

    return Stubborn


def test_fix_retries_until_the_check_passes(fragment_factory):
    c = fragment_factory(make_stubborn_class(3))

    c.fix_state()

    assert c.fixes == 3
    assert c.check_state()[0] == CalibrationResult.OK


def test_fix_retries_a_dependency(fragment_factory):
    Stubborn = make_stubborn_class(3)

    class Dependant(Calibration):
        def build_calibration(self):
            self.add_dependency(Stubborn)

        def check_own_state(self):
            return CalibrationResult.OK, None

    c = fragment_factory(Dependant)

    c.fix_state()

    assert c.check_state()[0] == CalibrationResult.OK


def test_fix_targets_retries(fragment_factory):
    c = fragment_factory(make_stubborn_class(3))

    fix_targets([c])

    assert c.fixes == 3
    assert c.check_state()[0] == CalibrationResult.OK


def test_no_retries_needed_when_the_first_fix_works(fragment_factory):
    c = fragment_factory(make_stubborn_class(1))

    c.fix_state()

    assert c.fixes == 1


def test_max_fix_attempts_bounds_the_retries(fragment_factory):
    c = fragment_factory(make_stubborn_class(10, max_fix_attempts=3))

    with pytest.raises(CalibrationError, match="failed after 3 attempts"):
        c.fix_state()

    assert c.fixes == 3


def test_max_fix_attempts_of_one_fails_fast(fragment_factory):
    c = fragment_factory(make_stubborn_class(10, max_fix_attempts=1))

    with pytest.raises(CalibrationError, match="failed after 1 attempt"):
        c.fix_state()

    assert c.fixes == 1


def test_bounded_dependency_failure_marks_the_dependant_bad(fragment_factory):
    Stubborn = make_stubborn_class(10, max_fix_attempts=2)

    class Dependant(Calibration):
        def build_calibration(self):
            self.add_dependency(Stubborn)
            # So the guess below is not simply "expired"
            self.set_check_timeout(60)

        def check_own_state(self):
            return CalibrationResult.OK, None

    c = fragment_factory(Dependant)

    with pytest.raises(CalibrationError):
        c.fix_state()

    assert c._guess_own_state() == CalibrationResult.BAD_DEPS


def test_default_max_fix_attempts_applies_when_unset(fragment_factory, monkeypatch):
    c = fragment_factory(make_stubborn_class(10))

    # Read at fix time, so it binds even though the calibration is already built
    monkeypatch.setattr(calibration_module, "DEFAULT_MAX_FIX_ATTEMPTS", 2)

    with pytest.raises(CalibrationError, match="failed after 2 attempts"):
        c.fix_state()

    assert c.fixes == 2


def test_set_max_fix_attempts_beats_the_module_default(fragment_factory, monkeypatch):
    monkeypatch.setattr(calibration_module, "DEFAULT_MAX_FIX_ATTEMPTS", 1)

    c = fragment_factory(make_stubborn_class(3, max_fix_attempts=None))

    c.fix_state()

    assert c.fixes == 3


def test_a_failing_optimizer_is_retried_too(fragment_factory):
    """A fix that raises CalibrationError (here: an optimizer that finds no
    valid point) is a failed attempt, not a fatal error."""

    class LateOptimum(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("test", "A test", 0, 1, default=0.5)
            self.sweeps = 0
            self.usable = False

        def check_own_state(self):
            if not self.usable:
                return CalibrationResult.BAD_DATA, None
            return CalibrationResult.OK, self.test.get()

        def fix_own_state(self) -> None:
            # No point checks out on the first sweep, so the optimizer gives up
            # with "No valid parameters found"; by the second, one does.
            self.sweeps += 1
            self.usable = self.sweeps >= 2
            super().fix_own_state()

    c = fragment_factory(LateOptimum)

    c.fix_state(force=True)

    assert c.sweeps == 2
    assert c.check_state()[0] == CalibrationResult.OK
    assert c.test.get() == 1.0


def test_bugs_in_a_calibration_are_not_retried(fragment_factory):
    """Errors that are not CalibrationError still propagate immediately."""

    class Buggy(Calibration):
        def build_calibration(self):
            self.fixes = 0

        def check_own_state(self):
            return CalibrationResult.BAD_DATA, None

        def fix_own_state(self) -> None:
            self.fixes += 1
            raise KeyError("this is a bug, not a drifting system")

    c = fragment_factory(Buggy)

    with pytest.raises(KeyError):
        c.fix_state()

    assert c.fixes == 1


def test_set_max_fix_attempts_rejects_nonsense(fragment_factory):
    class Zero(Calibration):
        def build_calibration(self):
            self.set_max_fix_attempts(0)

    with pytest.raises(ValueError):
        fragment_factory(Zero)


def test_set_max_fix_attempts_is_build_phase_only(fragment_factory):
    c = fragment_factory(make_stubborn_class(1))

    with pytest.raises(TypeError):
        c.set_max_fix_attempts(3)


def test_get_max_fix_attempts_reports_the_budget(fragment_factory):
    unbounded = fragment_factory(make_stubborn_class(1))
    bounded = fragment_factory(make_stubborn_class(1, max_fix_attempts=4))

    assert unbounded.get_max_fix_attempts() is None
    assert bounded.get_max_fix_attempts() == 4
