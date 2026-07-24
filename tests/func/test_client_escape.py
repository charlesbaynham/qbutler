"""End-to-end: a CalibratedExpFragment, wrapped as an ndscan scan experiment,
escapes from its kernel for a recalibration and re-enters ndscan's run loop.

Needs the ARTIQ emulator for the kernel tests. The custom-exception escape is
raised inside a real kernel, caught on the host by class, the DAG is fixed with
pooled kernels, and ndscan's run loop is re-entered. The wrapper is ndscan-
native (FragmentScanExperiment), so the same tests also pin the dashboard
surface: PARAMS schema exposure and override processing.
"""

import gc

import pytest
from artiq.experiment import kernel
from artiq.language.environment import ProcessArgumentManager
from ndscan.experiment.entry_point import FragmentScanExperiment
from ndscan.experiment.parameters import FloatParam
from ndscan.utils import PARAMS_ARG_KEY
from sipyco import pyon

from qbutler import dag
from qbutler import worker_ipc_lock
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.client import CalibratedExpFragment
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


class EscapingClient(CalibratedExpFragment):
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


def _wrap(experiment_factory, fragment_class):
    exp = experiment_factory(make_calibrated_experiment(fragment_class))
    exp.prepare()
    return exp


def test_wrapper_is_ndscan_native():
    Experiment = make_calibrated_experiment(EscapingClient)
    assert issubclass(Experiment, FragmentScanExperiment)
    assert Experiment.__name__ == "EscapingClient"


def test_build_installs_ipc_lock(experiment_factory):
    """The transaction lock is armed at the earliest worker entry point,
    before any thread qbutler ever starts."""
    worker_ipc_lock._installed = False
    try:
        experiment_factory(make_calibrated_experiment(EscapingClient))  # build()
        assert worker_ipc_lock._installed
    finally:
        worker_ipc_lock._installed = True


def test_params_schema_exposes_fragment_tree(experiment_factory):
    """The dashboard argument UI sees every parameter in the fragment tree."""
    exp = experiment_factory(make_calibrated_experiment(EscapingClient))
    schemata = exp.args._schemata
    assert any(fqn.endswith("DriftingCal.p") for fqn in schemata)
    assert any(fqn.endswith("DriftingCal.optimization_type") for fqn in schemata)


def test_dashboard_override_reaches_fragment(device_mgr, dataset_mgr):
    """A PARAMS override (what the dashboard editor submits) lands on the
    fragment's parameter through ndscan's own processing."""
    Experiment = make_calibrated_experiment(EscapingClient)

    probe = Experiment((device_mgr, dataset_mgr, ProcessArgumentManager({}), None))
    p_fqn = probe.fragment.DriftingCal._free_params["p"].fqn

    params = {"overrides": {p_fqn: [{"path": "*", "value": 3.25}]}}
    exp = Experiment(
        (
            device_mgr,
            dataset_mgr,
            ProcessArgumentManager({PARAMS_ARG_KEY: pyon.encode(params)}),
            None,
        )
    )
    exp.prepare()
    assert exp.fragment.DriftingCal.p.get() == pytest.approx(3.25)


class ScannableClient(CalibratedExpFragment):
    """Client with a plain (non-calibration) scan axis, to exercise the
    scanned-submission path end to end."""

    def build_fragment(self):
        self.setattr_device("core")
        self.setattr_calibration(DriftingCal)
        self.setattr_param("x", FloatParam, "Scan axis", default=0.0)
        self.n_runs = 0

    def _count(self):
        self.n_runs += 1

    @kernel
    def run_once(self):
        self._count()
        self.recalibrate_if_needed()


def _scan_params(fqn, num_points):
    return {
        "scan": {
            "axes": [
                {
                    "type": "linear",
                    "range": {
                        "start": 0.0,
                        "stop": 1.0,
                        "num_points": num_points,
                        "randomise_order": False,
                    },
                    "fqn": fqn,
                    "path": "*",
                }
            ],
            "num_repeats": 1,
            "no_axes_mode": "single",
            "randomise_order_globally": False,
        }
    }


@pytest.mark.withartiq
def test_scan_axes_escape_fix_and_complete(device_mgr, dataset_mgr):
    """A scanned submission runs through ndscan's scan loop: the first point
    escapes for recalibration, the host fixes the DAG, the scan resumes at the
    interrupted point, and every point lands exactly once."""
    Experiment = make_calibrated_experiment(ScannableClient)

    probe = Experiment((device_mgr, dataset_mgr, ProcessArgumentManager({}), None))
    x_fqn = probe.fragment._free_params["x"].fqn

    exp = Experiment(
        (
            device_mgr,
            dataset_mgr,
            ProcessArgumentManager(
                {PARAMS_ARG_KEY: pyon.encode(_scan_params(x_fqn, 3))}
            ),
            None,
        )
    )
    exp.prepare()
    exp.run()

    frag = exp.fragment
    # One escape (first point re-runs after the fix) then the three scan points.
    assert frag.n_runs == 4
    assert frag.DriftingCal.p.get() == pytest.approx(7.0)


@pytest.mark.withartiq
def test_escape_fix_and_reenter(experiment_factory):
    exp = _wrap(experiment_factory, EscapingClient)

    exp.run()

    frag = exp.fragment
    # First entry escapes (cal never checked -> BAD), host fixes, re-entry is OK:
    assert frag.n_runs == 2
    assert frag.DriftingCal.p.get() == pytest.approx(7.0)
    assert frag.DriftingCal.check_state()[0] == CalibrationResult.OK


@pytest.mark.withartiq
def test_no_escape_when_already_healthy(experiment_factory):
    exp = _wrap(experiment_factory, EscapingClient)
    frag = exp.fragment

    # Fix up front so the DAG is healthy before the science kernel runs.
    frag.host_setup()
    frag.DriftingCal.fix_state()
    frag.n_runs = 0

    exp.run()

    assert frag.n_runs == 1  # ran once, no escape


@pytest.mark.withartiq
def test_target_auto_discovered(fragment_factory):
    client = fragment_factory(EscapingClient)
    client.host_setup()
    assert client._cal_targets == [client.DriftingCal]
    client._shutdown_calibration()


class GuardedClient(CalibratedExpFragment):
    """A client with a shot-to-shot first-run guard and a device_setup counter,
    to pin the re-entry contract."""

    def build_fragment(self):
        self.setattr_device("core")
        self.setattr_calibration(DriftingCal)
        self.first_run = True
        self.n_init = 0
        self.n_device_setup = 0

    def _count_device_setup(self):
        self.n_device_setup += 1

    def _count_init(self):
        self.n_init += 1

    @kernel
    def device_setup(self):
        self._count_device_setup()

    @kernel
    def run_once(self):
        if self.first_run:
            self._count_init()
            self.first_run = False
        self.recalibrate_if_needed()


@pytest.mark.withartiq
def test_reentry_reruns_device_setup_and_first_run_guard(experiment_factory):
    """The re-entry contract through the ndscan wrapper: after one escape the
    main kernel restarts from the top — device_setup runs again, and the
    first-run guard RE-TRIGGERS (an escaping kernel never writes attributes
    back, so the guard is True again), re-running persisted-state
    initialisation after the calibration detour."""
    exp = _wrap(experiment_factory, GuardedClient)

    exp.run()

    assert exp.fragment.n_device_setup == 2
    assert exp.fragment.n_init == 2


class RefreshClient(CalibratedExpFragment):
    """Kernel reads a param whose dataset default a dependency's fix rewrites:
    the post-re-entry cycle must observe the NEW value despite the precompiled
    main kernel (compile-time-baked stores refreshed on-core at entry)."""

    def build_fragment(self):
        self.setattr_device("core")
        self.setattr_calibration(DriftingCal)
        self.setattr_param(
            "t",
            FloatParam,
            "reads the cal's committed optimum",
            default='dataset("DriftingCal.p", 5.0)',
        )
        self.seen = []

    def _record(self, value):
        self.seen.append(value)

    @kernel
    def run_once(self):
        self._record(self.t.get())
        self.recalibrate_if_needed()


@pytest.mark.withartiq
def test_precompiled_reentry_sees_committed_param(experiment_factory):
    """Escape -> fix commits DriftingCal.p = 7.0 -> re-entry: ndscan's
    recompute_param_defaults refreshes the HOST store, and the precompiled
    entry's on-core refresh carries it into the baked kernel."""
    exp = _wrap(experiment_factory, RefreshClient)

    exp.run()

    assert exp.fragment.seen == [pytest.approx(5.0), pytest.approx(7.0)]


@pytest.mark.withartiq
def test_main_kernel_compiled_once_across_escape(experiment_factory):
    """Timing evidence: one escape means two kernel entries but exactly one
    compile of the main kernel entry (re-entry redeploys the artifact)."""
    exp = _wrap(experiment_factory, RefreshClient)

    core = exp.fragment.core
    original_compile = core.compile
    compiled_names = []

    def counting_compile(function, *args, **kwargs):
        compiled_names.append(getattr(function, "__name__", "?"))
        return original_compile(function, *args, **kwargs)

    core.compile = counting_compile
    try:
        exp.run()
    finally:
        core.compile = original_compile

    assert len(exp.fragment.seen) == 2  # entered twice (one escape)
    assert compiled_names.count("_entry") == 1
    assert compiled_names.count("_run_continuous_kernel") == 0
