"""End-to-end: a CalibratedExperiment escapes from its kernel for a
recalibration and re-enters the precompiled main kernel.

Needs the ARTIQ emulator. The custom-exception escape is raised inside a real
kernel, caught on the host by class, the DAG is fixed with pooled kernels, and
the (precompiled) main kernel is re-entered.
"""

import gc

import pytest
from artiq.experiment import kernel

from qbutler import dag
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.client import CalibratedExperiment
from qbutler.client import make_calibrated_experiment


@pytest.fixture(autouse=True)
def _clear_dag():
    gc.collect()
    dag._dependency_map.clear()
    yield
    dag._dependency_map.clear()


class DriftingCal(Calibration):
    """Kernel check that is BAD at its default and OK once its param is near 7.
    A non-zero timeout means a fresh fix stays OK, so the client settles."""

    def build_calibration(self):
        self.setattr_device("core")
        self.set_timeout(300.0)
        self.setattr_param_optimizable(
            "p", "Test param", min=0.0, max=10.0, default=5.0
        )

    @kernel
    def check_own_state(self):
        v = self.p.get()
        data = 10.0 - abs(v - 7.0)
        if data > 8.0:
            return CalibrationResult.OK, data
        return CalibrationResult.BAD_DATA, data


class EscapingClient(CalibratedExperiment):
    def build_fragment(self):
        self.setattr_device("core")
        self.setattr_calibration(DriftingCal)
        self.n_runs = 0

    def _count(self):
        self.n_runs += 1

    @kernel
    def run_once(self):
        self._count()
        self.recalibrate_if_needed()


@pytest.mark.withartiq
def test_escape_fix_and_reenter(fragment_factory):
    client = fragment_factory(EscapingClient)
    client.host_setup()

    client.run_calibrated()

    # First entry escapes (cal never checked -> BAD), host fixes, re-entry is OK:
    assert client.n_runs == 2
    assert client.DriftingCal.p.get() == pytest.approx(7.0)
    assert client.DriftingCal.check_state()[0] == CalibrationResult.OK
    client.host_cleanup()


@pytest.mark.withartiq
def test_no_escape_when_already_healthy(fragment_factory):
    client = fragment_factory(EscapingClient)
    client.host_setup()

    # Fix up front so the DAG is healthy before the science kernel runs.
    client.DriftingCal.fix_state()
    client.n_runs = 0

    client.run_calibrated()

    assert client.n_runs == 1  # ran once, no escape
    client.host_cleanup()


@pytest.mark.withartiq
def test_target_auto_discovered(fragment_factory):
    client = fragment_factory(EscapingClient)
    client.host_setup()
    assert client._cal_target is client.DriftingCal
    client.host_cleanup()


@pytest.mark.withartiq
def test_standalone_entry_point_runs(experiment_factory):
    """The exact physicist-usage path: make_calibrated_experiment(...).run()."""
    Experiment = make_calibrated_experiment(EscapingClient)
    exp = experiment_factory(Experiment)
    exp.prepare()
    exp.run()
    assert exp.frag.n_runs == 2
    assert exp.frag.DriftingCal.p.get() == pytest.approx(7.0)
