"""The re-optimise timeout and the UNCALIBRATED state.

A node may opt in to a second, independent timeout
(:meth:`~qbutler.calibration.Calibration.set_reoptimise_timeout`): once it
lapses since the last *successful fix*, the node is UNCALIBRATED — the next
fix walk re-fixes it even though its checks keep passing, because a passing
check says the system is good *enough*, not that its parameters are still
optimal. UNCALIBRATED is cleared only by a successful fix, never by a check;
check walks and monitors ignore it entirely. A forced walk
(``fix_state(force=True)``) is just "mark every fixable node UNCALIBRATED,
then walk ordinarily".
"""

from types import SimpleNamespace

import pytest

import qbutler.calibration
from qbutler.calibration import STATUS_DATASET
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


def make_drifter(reoptimise_timeout=100):
    """One fixable node over mock hardware whose check always passes, so only
    the re-optimise machinery can ever make a walk touch it."""
    hw = SimpleNamespace(checks=0, fixes=0)

    class Drifter(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            if reoptimise_timeout is not None:
                self.set_reoptimise_timeout(reoptimise_timeout)

        def check_own_state(self):
            hw.checks += 1
            return CalibrationResult.OK, None

        def fix_own_state(self) -> None:
            hw.fixes += 1

    return hw, Drifter


# ---------------------------------------------------------------- build-time


def test_set_reoptimise_timeout_requires_fixable_node(fragment_factory):
    class CheckOnly(Calibration):
        def build_calibration(self):
            self.set_reoptimise_timeout(100)

        def check_own_state(self):
            return CalibrationResult.OK, None

    with pytest.raises(TypeError, match="not fixable"):
        fragment_factory(CheckOnly)


def test_set_reoptimise_timeout_rejects_nonpositive(fragment_factory):
    class Zero(Calibration):
        def build_calibration(self):
            self.set_reoptimise_timeout(0)

        def check_own_state(self):
            return CalibrationResult.OK, None

        def fix_own_state(self) -> None:
            pass

    with pytest.raises(ValueError, match="must be > 0"):
        fragment_factory(Zero)


def test_set_reoptimise_timeout_outside_build_raises(fragment_factory):
    hw, Drifter = make_drifter()
    c = fragment_factory(Drifter)
    with pytest.raises(TypeError, match="build_calibration"):
        c.set_reoptimise_timeout(100)


def test_is_fixable(fragment_factory):
    class CheckOnly(Calibration):
        def build_calibration(self):
            pass

        def check_own_state(self):
            return CalibrationResult.OK, None

    class WithParams(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("test", "A test", 0, 1, default=0.5)

        def check_own_state(self):
            return CalibrationResult.OK, 10 * self.test.get()

    hw, WithFixOverride = make_drifter(reoptimise_timeout=None)

    assert not fragment_factory(CheckOnly).is_fixable()
    assert fragment_factory(WithParams).is_fixable()
    assert fragment_factory(WithFixOverride).is_fixable()


def test_param_order_does_not_matter(fragment_factory):
    """set_reoptimise_timeout before setattr_param_optimizable must be legal:
    fixability is validated at the end of build, not at call time."""

    class OptimizableLater(Calibration):
        def build_calibration(self):
            self.set_reoptimise_timeout(100)
            self.setattr_param_optimizable("test", "A test", 0, 1, default=0.5)

        def check_own_state(self):
            return CalibrationResult.OK, 10 * self.test.get()

    c = fragment_factory(OptimizableLater)
    assert c.get_reoptimise_timeout() == 100
    assert c.is_fixable()


# ------------------------------------------------------------- core semantics


def test_node_without_reoptimise_timeout_behaves_exactly_as_today(
    fragment_factory,
):
    hw, Drifter = make_drifter(reoptimise_timeout=None)
    c = fragment_factory(Drifter)

    c.check_state()
    c.fix_state()
    # Healthy, in-timeout, not opted in: the walk never touches it
    assert hw.fixes == 0
    assert not c._needs_reoptimise()
    assert c.get_reoptimise_timeout() is None


def test_opted_in_node_with_no_stamp_needs_reoptimise(fragment_factory):
    hw, Drifter = make_drifter()
    c = fragment_factory(Drifter)

    # Even with a fresh passing check, an opted-in node that has never
    # provably been optimised is UNCALIBRATED
    c.check_state()
    assert c._guess_own_state() == CalibrationResult.OK
    assert c._needs_reoptimise()


def test_successful_fix_stamps_and_clears(fragment_factory, dataset_db):
    hw, Drifter = make_drifter()
    c = fragment_factory(Drifter)

    assert c.get_last_optimised() is None
    c.fix_state()

    assert hw.fixes == 1
    assert not c._needs_reoptimise()
    assert c.get_last_optimised() is not None
    entry = dataset_db.get(STATUS_DATASET)["Drifter"]
    assert entry["last_optimised"] == c.get_last_optimised()
    assert entry["reoptimise_timeout"] == 100
    assert entry["uncalibrated"] is False


def test_passing_check_does_not_clear_the_mark_or_stamp(fragment_factory):
    hw, Drifter = make_drifter()
    c = fragment_factory(Drifter)
    c._set_uncalibrated_mark()

    assert c.check_state()[0] == CalibrationResult.OK
    # The check passed and was recorded — but the mark survives: only a
    # successful fix answers "is the optimum still established?"
    assert c._needs_reoptimise()


def test_fix_walk_refixes_a_passing_node_whose_window_lapsed(
    fragment_factory, monkeypatch
):
    hw, Drifter = make_drifter(reoptimise_timeout=100)
    c = fragment_factory(Drifter)
    c.fix_state()
    assert hw.fixes == 1
    fixed_at = qbutler.calibration.time()

    # Inside the window: nothing to do
    c.fix_state()
    assert hw.fixes == 1

    # Past the window (check timeout notionally also lapsed — irrelevant,
    # UNCALIBRATED is selected for a fix directly, no rescuing re-check)
    checks_before = hw.checks
    monkeypatch.setattr(qbutler.calibration, "time", lambda: fixed_at + 200)
    c.fix_state()
    assert hw.fixes == 2
    # The first measurement after the lapse was the fix's own re-check, not a
    # pre-check that could have rescued the node
    assert hw.checks == checks_before + 1


def test_check_walks_ignore_uncalibrated(fragment_factory):
    hw, Drifter = make_drifter()
    c = fragment_factory(Drifter)
    c.check_state()
    checks_after_first = hw.checks
    assert c._needs_reoptimise()  # opted in, never fixed

    # check_state trusts the in-timeout OK: no re-measure, result OK
    assert c.check_state()[0] == CalibrationResult.OK
    assert hw.checks == checks_after_first
    assert c.guess_state() == CalibrationResult.OK


# --------------------------------------------------------------- persistence


def test_mark_and_stamp_survive_a_new_worker_process(fragment_factory):
    hw, Drifter = make_drifter()
    c = fragment_factory(Drifter)
    c.fix_state()
    stamp = c.get_last_optimised()
    del c

    c2 = fragment_factory(Drifter)
    assert not c2._needs_reoptimise()
    assert c2.get_last_optimised() == stamp

    c2._set_uncalibrated_mark()
    c2._publish_status()
    del c2

    c3 = fragment_factory(Drifter)
    assert c3._needs_reoptimise()


def test_force_mark_on_a_never_checked_node_survives_a_new_worker(
    fragment_factory,
):
    """The mark must be recalled even when no check has ever been recorded —
    a forced walk's mark can precede any check, and dropping it would let a
    later passing check rescue the node."""
    hw, Drifter = make_drifter()
    c = fragment_factory(Drifter)
    qbutler.calibration._mark_uncalibrated([c])
    del c

    c2 = fragment_factory(Drifter)
    assert c2._needs_reoptimise()


def test_legacy_status_entries_without_new_keys_are_tolerated(
    fragment_factory, dataset_db
):
    """Entries written before this feature existed simply mean "not marked,
    never stamped" — and a not-opted-in node stays exactly as today."""
    hw, Drifter = make_drifter(reoptimise_timeout=None)
    c = fragment_factory(Drifter)
    c.check_state()
    entry = dataset_db.data[STATUS_DATASET][1]["Drifter"]
    del entry["last_optimised"]
    del entry["reoptimise_timeout"]
    del entry["uncalibrated"]
    del c

    c2 = fragment_factory(Drifter)
    assert c2._guess_own_state() == CalibrationResult.OK
    assert not c2._needs_reoptimise()
