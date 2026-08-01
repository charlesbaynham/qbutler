"""Yielding to the ARTIQ scheduler while a calibration is being fixed.

A fix walk runs the whole DAG — a kernel optimizer sweep per node — without
ever returning to the scheduler, so before these checks existed a user asking
to terminate the run had to wait for every calibration to finish. The walk now
consults ``scheduler.check_pause()`` between the shots of a fix (rate-limited
to :data:`~qbutler.calibration.PAUSE_CHECK_INTERVAL`, so a microsecond-scale
calibration is not slowed by round trips to the master) and pauses when asked.
"""

import pytest
from artiq.language.core import TerminationRequested

from qbutler import calibration as calibration_module
from qbutler import scoping
from qbutler.calibration import OPTIMIZER_DATASET
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.calibration import pause_check_gate


class FakeScheduler:
    """Stands in for the ARTIQ scheduler, counting checks and pauses.

    Args:
        pause_on: which ``check_pause()`` calls should ask for a pause —
            ``None`` for never, ``"all"`` for every one, or a set of 1-based
            call numbers.
        terminate: make ``pause()`` raise ``TerminationRequested``, as the real
            scheduler does when the run is being killed rather than preempted.
    """

    def __init__(self, pause_on=None, terminate=False):
        self.pause_on = pause_on
        self.terminate = terminate
        self.checks = 0
        self.pauses = 0
        # As the real scheduler does: qbutler namespaces its live-view datasets
        # by the pipeline it reads from here.
        self.pipeline_name = "main"

    def check_pause(self, rid=None) -> bool:
        self.checks += 1
        if self.pause_on == "all":
            return True
        return self.pause_on is not None and self.checks in self.pause_on

    def pause(self):
        self.pauses += 1
        if self.terminate:
            raise TerminationRequested


@pytest.fixture
def check_every_shot(monkeypatch):
    """Drop the rate limit, so every opportunity to check is taken."""
    monkeypatch.setattr(calibration_module, "PAUSE_CHECK_INTERVAL", 0.0)
    pause_check_gate.reset()


class ParamsCalibration(Calibration):
    """Optimizable on the host; the metric peaks at the top of the range."""

    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0, 1, default=0.5)

    def check_own_state(self):
        return CalibrationResult.OK, 10 * self.test.get()


class BrokenDep(Calibration):
    def build_calibration(self):
        self.broken = True

    def check_own_state(self):
        if self.broken:
            return CalibrationResult.BAD_DATA, None
        return CalibrationResult.OK, None

    def fix_own_state(self):
        self.broken = False


class BrokenParent(Calibration):
    def build_calibration(self):
        self.add_dependency(BrokenDep)
        self.broken = True

    def check_own_state(self):
        if self.broken:
            return CalibrationResult.BAD_DATA, None
        return CalibrationResult.OK, None

    def fix_own_state(self):
        self.broken = False


def _optimizer_points(cal):
    """The points the live optimizer trace recorded for ``cal``'s last fix."""
    key = scoping.scoped_key(OPTIMIZER_DATASET, cal)
    table = cal.get_dataset(key, default={}, archive=False)
    return [tuple(point) for point in table[type(cal).__name__]["points"]]


def _install(cal, scheduler):
    """Point a calibration (and its dependencies) at a fake scheduler."""
    from qbutler import dag

    for node in dag.get_dependencies(cal):
        node._scheduler = scheduler


#
# The rate limiter
#


def test_gate_allows_one_check_per_interval(monkeypatch):
    monkeypatch.setattr(calibration_module, "PAUSE_CHECK_INTERVAL", 100.0)

    assert pause_check_gate.due()
    assert not pause_check_gate.due()
    assert not pause_check_gate.due()

    pause_check_gate.reset()
    assert pause_check_gate.due()


def test_gate_always_due_without_an_interval(check_every_shot):
    assert pause_check_gate.due()
    assert pause_check_gate.due()


def test_fast_fix_checks_the_scheduler_only_once(fragment_factory):
    """A sweep that finishes well inside PAUSE_CHECK_INTERVAL must not spend a
    round trip to the master per shot."""
    c = fragment_factory(ParamsCalibration)
    scheduler = FakeScheduler()
    _install(c, scheduler)

    c.fix_state(force=True)

    assert scheduler.checks == 1


#
# The host-side walk
#


def test_fix_walk_pauses_between_nodes(fragment_factory, check_every_shot):
    c = fragment_factory(BrokenParent)
    scheduler = FakeScheduler(pause_on={1})
    _install(c, scheduler)

    c.fix_state()

    assert scheduler.pauses == 1
    # The pause is a detour, not an abort: the walk still fixes everything.
    assert c.check_state()[0] == CalibrationResult.OK


def test_fix_walk_stops_on_termination(fragment_factory, check_every_shot):
    c = fragment_factory(BrokenParent)
    scheduler = FakeScheduler(pause_on={1}, terminate=True)
    _install(c, scheduler)

    with pytest.raises(TerminationRequested):
        c.fix_state()


def test_check_walk_does_not_pause(fragment_factory, check_every_shot):
    """Only fixes pause. Checks are run by MonitorController on worker threads,
    where calling scheduler.pause() would be unsafe."""
    c = fragment_factory(BrokenParent)
    scheduler = FakeScheduler(pause_on="all")
    _install(c, scheduler)

    c.check_state()

    assert scheduler.checks == 0


def test_scheduler_failure_does_not_break_a_fix(fragment_factory, check_every_shot):
    class ExplodingScheduler:
        def check_pause(self, rid=None):
            raise RuntimeError("no scheduler for you")

    c = fragment_factory(BrokenParent)
    _install(c, ExplodingScheduler())

    c.fix_state()

    assert c.check_state()[0] == CalibrationResult.OK


def test_calibration_without_a_scheduler_still_fixes(fragment_factory):
    c = fragment_factory(BrokenParent)
    _install(c, None)

    c.fix_state()

    assert c.check_state()[0] == CalibrationResult.OK


#
# The host optimizer loop
#


def test_host_optimizer_pauses_between_shots(fragment_factory, check_every_shot):
    c = fragment_factory(ParamsCalibration)
    scheduler = FakeScheduler(pause_on="all")
    _install(c, scheduler)

    c.fix_state(force=True)

    # One check per point of the sweep, and every one of them paused.
    assert scheduler.checks == scheduler.pauses > 1
    assert c.test.get() == 1
    assert c.check_state()[0] == CalibrationResult.OK


def test_host_optimizer_pause_does_not_disturb_the_sweep(
    fragment_factory, check_every_shot
):
    """Pausing at every opportunity must measure exactly the same points, in
    the same order, as not pausing at all."""
    undisturbed = fragment_factory(ParamsCalibration)
    _install(undisturbed, None)
    undisturbed.fix_state(force=True)
    expected = _optimizer_points(undisturbed)

    paused = fragment_factory(ParamsCalibration)
    _install(paused, FakeScheduler(pause_on="all"))
    paused.fix_state(force=True)

    assert _optimizer_points(paused) == expected


#
# The resident optimizer loop's pause protocol
#
# The loop itself is a kernel, so it only runs under --withartiq. Its host
# half — bail out, pause, re-enter, resume where we left off — is driven
# through the same RPCs by a Python stand-in, so it is covered everywhere.
#


def _simulated_kernel_loop(cal, calls):
    """A Python mirror of :meth:`Calibration._optimizer_kernel_loop`.

    Same RPCs in the same order; the only difference is that the measurement
    happens here rather than on the core device. Keep it in step with the
    kernel it mirrors.
    """

    def run_loop():
        calls.append(len(calls) + 1)
        values = cal._kopt_entry_point()
        while len(values) > 0:
            for i, store in enumerate(cal._kopt_stores):
                store.set_value(values[i])
            result, data = cal.check_own_state()
            values = cal._kopt_next_point(result, data)

        values = cal._kopt_get_best()
        if len(values) > 0:
            for i, store in enumerate(cal._kopt_stores):
                store.set_value(values[i])
            result, data = cal.check_own_state()
            cal._kopt_record_verify(result, data)

    return run_loop


def _run_simulated_loop(cal):
    """Fix ``cal`` the way the pooled kernel path does, returning the number of
    times the resident loop had to be (re-)entered."""
    calls = []
    cal._kopt_bind_stores()
    cal._kopt_prime_default()
    cal._drive_optimizer_kernel_loop(_simulated_kernel_loop(cal, calls))
    cal._kopt_commit()
    return len(calls)


def test_resident_loop_runs_once_when_not_paused(fragment_factory):
    c = fragment_factory(ParamsCalibration)
    _install(c, FakeScheduler())

    assert _run_simulated_loop(c) == 1
    assert c.test.get() == 1


def test_resident_loop_reenters_after_a_pause(fragment_factory, check_every_shot):
    """The kernel cannot pause itself, so it unwinds to the host, which pauses
    and re-enters it: one extra entry per pause."""
    c = fragment_factory(ParamsCalibration)
    scheduler = FakeScheduler(pause_on={2})
    _install(c, scheduler)

    entries = _run_simulated_loop(c)

    assert scheduler.pauses == 1
    assert entries == 2
    assert c.test.get() == 1


def test_resident_loop_pause_does_not_disturb_the_sweep(
    fragment_factory, check_every_shot
):
    """Pausing after every shot must measure exactly the same points, in the
    same order, as never pausing: a pause loses no point and repeats none."""
    undisturbed = fragment_factory(ParamsCalibration)
    _install(undisturbed, None)
    _run_simulated_loop(undisturbed)
    expected = _optimizer_points(undisturbed)

    paused = fragment_factory(ParamsCalibration)
    scheduler = FakeScheduler(pause_on="all")
    _install(paused, scheduler)
    entries = _run_simulated_loop(paused)

    assert _optimizer_points(paused) == expected
    # Every shot but the last one bailed out (the last returns [] because the
    # sweep is over, not because of a pause), and each bail-out costs an entry.
    assert entries == scheduler.pauses + 1 == len(expected)
    assert paused.test.get() == 1


def test_resident_loop_stops_on_termination(fragment_factory, check_every_shot):
    c = fragment_factory(ParamsCalibration)
    _install(c, FakeScheduler(pause_on={1}, terminate=True))

    with pytest.raises(TerminationRequested):
        _run_simulated_loop(c)


#
# The resident optimizer kernel loop, for real
#


@pytest.mark.withartiq
def test_kernel_optimizer_pauses_and_resumes(fragment_factory, mock_core, monkeypatch):
    """The kernel cannot pause itself, so it unwinds to the host, which pauses
    and re-enters it — one extra kernel call per pause, and no lost points."""
    from tests.func import kernel_calibrations

    monkeypatch.setattr(calibration_module, "PAUSE_CHECK_INTERVAL", 0.0)
    pause_check_gate.reset()

    c = fragment_factory(kernel_calibrations.KernelOptimizableCalibration)
    scheduler = FakeScheduler(pause_on={2})
    _install(c, scheduler)

    initial_calls = mock_core.call_count
    c.fix_own_state()

    assert scheduler.pauses == 1
    # One call for the sweep up to the pause, one for the rest of it.
    assert mock_core.call_count - initial_calls == 2

    result, _ = c.check_own_state()
    assert result == CalibrationResult.OK


@pytest.mark.withartiq
def test_kernel_optimizer_pause_does_not_disturb_the_sweep(
    fragment_factory, monkeypatch
):
    from tests.func import kernel_calibrations

    undisturbed = fragment_factory(kernel_calibrations.KernelOptimizableCalibration)
    _install(undisturbed, None)
    undisturbed.fix_own_state()
    expected = _optimizer_points(undisturbed)

    monkeypatch.setattr(calibration_module, "PAUSE_CHECK_INTERVAL", 0.0)
    pause_check_gate.reset()

    paused = fragment_factory(kernel_calibrations.KernelOptimizableCalibration)
    _install(paused, FakeScheduler(pause_on="all"))
    paused.fix_own_state()

    assert _optimizer_points(paused) == expected


@pytest.mark.withartiq
def test_kernel_optimizer_stops_on_termination(fragment_factory, monkeypatch):
    from tests.func import kernel_calibrations

    monkeypatch.setattr(calibration_module, "PAUSE_CHECK_INTERVAL", 0.0)
    pause_check_gate.reset()

    c = fragment_factory(kernel_calibrations.KernelOptimizableCalibration)
    _install(c, FakeScheduler(pause_on={1}, terminate=True))

    with pytest.raises(TerminationRequested):
        c.fix_own_state()
