"""qbutler auto-launches its dashboard applets via the CCB: a per-class
optimizer-trace applet as each optimizer runs, plus a DAG overview applet
whenever the DAG is published. Both are namespaced by the pipeline the run is in
— in the applet-tree group and in the dataset keys they plot — so simultaneous
runs in different pipelines do not fight over one dashboard entry. See
:mod:`qbutler.ccb` and :mod:`qbutler.scoping`.
"""

from unittest.mock import Mock

from qbutler import scoping
from qbutler.calibration import OPTIMIZER_DATASET
from qbutler.calibration import STATUS_DATASET
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.dag import DAG_DATASET


class AppletTracedCalibration(Calibration):
    """Host check_own_state with one optimizable parameter."""

    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0.0, 1.0, default=0.5)

    def check_own_state(self):
        return CalibrationResult.OK, 10.0 * self.test.get()


class OtherCalibration(Calibration):
    """A second, unrelated calibration, to stand in for another pipeline's work."""

    def build_calibration(self):
        pass

    def check_own_state(self):
        return CalibrationResult.OK, None


def _create_applet_calls(issue_mock):
    return [
        call for call in issue_mock.call_args_list if call.args[0] == "create_applet"
    ]


def _call_named(calls, name):
    return next(call for call in calls if call.args[1] == name)


def test_fix_creates_per_class_optimizer_and_dag_applets(fragment_factory, device_mgr):
    issue = Mock()
    device_mgr.get("ccb").issue = issue

    c = fragment_factory(AppletTracedCalibration)
    c.fix_state(force=True)

    calls = _create_applet_calls(issue)
    names = [call.args[1] for call in calls]
    assert "Calibration DAG" in names
    assert "Optimizer: AppletTracedCalibration" in names

    # The DAG applet plots this pipeline's own DAG dataset, but the shared,
    # unscoped status table: state is a property of the apparatus, not the
    # pipeline that measured it.
    dag_applet = _call_named(calls, "Calibration DAG")
    command = dag_applet.args[2]
    assert "qbutler.applets.dag_applet" in command
    assert f"{DAG_DATASET}.main {STATUS_DATASET}" in command
    assert dag_applet.kwargs["group"] == ["Calibrations", "main"]

    # The optimizer applet is namespaced to this class in its command + group,
    # so each class gets its own separate plot, and to this pipeline.
    opt = _call_named(calls, "Optimizer: AppletTracedCalibration")
    command = opt.args[2]
    assert "qbutler.applets.optimizer_applet" in command
    assert "--calibration AppletTracedCalibration" in command
    assert f"{OPTIMIZER_DATASET}.main" in command
    assert opt.kwargs["group"] == ["Calibrations", "main", "Optimizers"]


def test_applets_and_datasets_are_isolated_per_pipeline(
    fragment_factory, device_mgr, dataset_db
):
    """Two pipelines running at once must not publish over each other.

    The dashboard keys an applet on (name, group) and replaces the spec of an
    existing entry, so without the pipeline level in the group both runs would
    share one applet flickering between their two graphs.
    """
    issue = Mock()
    device_mgr.get("ccb").issue = issue
    scheduler = device_mgr.get("scheduler")

    scheduler.pipeline_name = "main"
    first = fragment_factory(AppletTracedCalibration)
    first.check_state()

    main_dag = dict(dataset_db.get(f"{DAG_DATASET}.main"))
    assert main_dag["nodes"] == ["AppletTracedCalibration"]

    # A second pipeline starts. (In production these are separate worker
    # processes with their own DAGs; in-process the second walk sees both
    # calibrations, which is precisely why its payload must not land on the
    # first pipeline's key.)
    scheduler.pipeline_name = "monitors"
    second = fragment_factory(OtherCalibration)
    second.check_state()

    monitors_dag = dataset_db.get(f"{DAG_DATASET}.monitors")
    assert "OtherCalibration" in monitors_dag["nodes"]

    # The first pipeline's graph is untouched by the second's walk.
    assert dataset_db.get(f"{DAG_DATASET}.main") == main_dag
    assert "OtherCalibration" not in main_dag["nodes"]

    # Two separate applets, one per pipeline, rather than one contended entry.
    dag_groups = [
        tuple(call.kwargs["group"])
        for call in _create_applet_calls(issue)
        if call.args[1] == "Calibration DAG"
    ]
    assert ("Calibrations", "main") in dag_groups
    assert ("Calibrations", "monitors") in dag_groups

    # Status stays shared and unscoped: both pipelines' checks land in one table,
    # so a walk in either sees what the other already measured.
    status = dataset_db.get(STATUS_DATASET)
    assert {"AppletTracedCalibration", "OtherCalibration"} <= set(status)
    assert f"{STATUS_DATASET}.main" not in dataset_db.data


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
    key = scoping.scoped_key(OPTIMIZER_DATASET, c)
    entry = dataset_db.get(key)["AppletTracedCalibration"]
    assert len(entry["points"]) > 0
