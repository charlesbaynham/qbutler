"""qbutler auto-launches its dashboard applets via the CCB: a per-class
optimizer-trace applet as each optimizer runs, plus a single DAG overview applet
whenever the DAG is published. See :mod:`qbutler.ccb`.
"""

from unittest.mock import Mock

from qbutler.calibration import OPTIMIZER_DATASET
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class AppletTracedCalibration(Calibration):
    """Host check_own_state with one optimizable parameter."""

    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0.0, 1.0, default=0.5)

    def check_own_state(self):
        return CalibrationResult.OK, 10.0 * self.test.get()


def _create_applet_calls(issue_mock):
    return [
        call for call in issue_mock.call_args_list if call.args[0] == "create_applet"
    ]


def test_fix_creates_per_class_optimizer_and_dag_applets(fragment_factory, device_mgr):
    issue = Mock()
    device_mgr.get("ccb").issue = issue

    c = fragment_factory(AppletTracedCalibration)
    c.fix_state(force=True)

    calls = _create_applet_calls(issue)
    names = [call.args[1] for call in calls]
    assert "Calibration DAG" in names
    assert "Optimizer: AppletTracedCalibration" in names

    # The optimizer applet is namespaced to this class in its command + group,
    # so each class gets its own separate plot.
    opt = next(
        call for call in calls if call.args[1] == "Optimizer: AppletTracedCalibration"
    )
    command = opt.args[2]
    assert "qbutler.applets.optimizer_applet" in command
    assert "--calibration AppletTracedCalibration" in command
    assert OPTIMIZER_DATASET in command
    assert opt.kwargs["group"] == ["Calibrations", "Optimizers"]


def test_applet_creation_failure_does_not_break_fix(
    fragment_factory, device_mgr, dataset_db
):
    # A dashboard hiccup (or a worker with no usable ccb) must not stop a fix:
    # applet creation is strictly best-effort.
    def boom(*args, **kwargs):
        raise RuntimeError("simulated ccb failure")

    device_mgr.get("ccb").issue = boom

    c = fragment_factory(AppletTracedCalibration)
    c.fix_state(force=True)  # must not raise

    # The fix still ran to completion and published its optimizer trace.
    entry = dataset_db.get(OPTIMIZER_DATASET)["AppletTracedCalibration"]
    assert len(entry["points"]) > 0
