"""Tests for the 2026-07 extensions: status datasets, dataset-backed expiry,
DAG publishing, coordinate-descent optimizer, and build-guard fixes."""

import functools

import numpy as np
import pytest

import qbutler.calibration
from qbutler.calibration import STATUS_DATASET
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.dag import DAG_DATASET
from qbutler.optimizers import ParamSpec
from qbutler.optimizers import coordinate_descent_optimizer


class SimpleCalibration(Calibration):
    def build_calibration(self):
        self.set_timeout(100)

    def check_own_state(self):
        return CalibrationResult.OK, 42.0


class Descent2DCalibration(Calibration):
    CENTRE = (0.3, -0.2)

    def build_calibration(self):
        self.setattr_param_optimizable("x", "X", -1, 1, default=0.0)
        self.setattr_param_optimizable("y", "Y", -1, 1, default=0.0)
        self.set_optimization_type("max")
        self.set_optimizer(
            functools.partial(coordinate_descent_optimizer, num_points=7, n_rounds=2)
        )

    def check_own_state(self):
        cx, cy = self.CENTRE
        g = np.exp(-((self.x.get() - cx) ** 2 + (self.y.get() - cy) ** 2) / 0.5)
        return CalibrationResult.OK, g


def test_coordinate_descent_converges(fragment_factory):
    c = fragment_factory(Descent2DCalibration)
    c.fix_state(force=True)
    assert c.check_state()[0] == CalibrationResult.OK
    assert abs(c.x.get() - Descent2DCalibration.CENTRE[0]) < 0.15
    assert abs(c.y.get() - Descent2DCalibration.CENTRE[1]) < 0.15


def test_coordinate_descent_protocol():
    """Drive the generator by hand: eval count and BAD-point rejection."""

    class FakeHandle:
        def get(self):
            return 0.0

    specs = [ParamSpec("a", -1, 1, FakeHandle()), ParamSpec("b", -1, 1, FakeHandle())]
    opt = coordinate_descent_optimizer(specs, num_points=5, n_rounds=2)

    n_evals = 0
    point = next(opt)
    best = None
    try:
        while True:
            n_evals += 1
            # Peak at a=0.5; all b points reported BAD so b must stay at 0.0
            if abs(point["b"]) > 1e-9:
                feedback = (CalibrationResult.BAD_DATA, None)
            else:
                feedback = (CalibrationResult.OK, -((point["a"] - 0.5) ** 2))
            point = opt.send(feedback)
    except StopIteration as stop:
        best = stop.value

    assert n_evals == 2 * 2 * 5
    assert abs(best["a"] - 0.5) < 0.15
    assert best["b"] == 0.0  # no OK candidate ever seen off b=0


def test_status_dataset_published(fragment_factory, dataset_db):
    c = fragment_factory(SimpleCalibration)
    c.check_state()

    table = dataset_db.get(STATUS_DATASET)
    entry = table["SimpleCalibration"]
    assert entry["status"] == int(CalibrationResult.OK)
    assert entry["timeout"] == 100
    assert entry["data"] == 42.0
    assert entry["last_check"] is not None


def test_status_survives_new_instance(fragment_factory, monkeypatch):
    c = fragment_factory(SimpleCalibration)
    c.check_state()
    checked_at = qbutler.calibration.time()
    del c

    c2 = fragment_factory(SimpleCalibration)
    # In-memory state is gone; the dataset recall must say "recently checked"
    assert c2._guess_own_state() == CalibrationResult.OK

    c3 = fragment_factory(SimpleCalibration)
    monkeypatch.setattr(qbutler.calibration, "time", lambda: checked_at + 200)
    assert c3._guess_own_state() == CalibrationResult.BAD_EXPIRED


def test_dag_published(fragment_factory, dataset_db):
    class Leaf(Calibration):
        def build_calibration(self):
            pass

        def check_own_state(self):
            return CalibrationResult.OK, None

    class Root(Calibration):
        def build_calibration(self):
            self.add_dependency(Leaf)

        def check_own_state(self):
            return CalibrationResult.OK, None

    c = fragment_factory(Root)
    c.check_state()

    dag = dataset_db.get(DAG_DATASET)
    assert "Leaf" in dag["nodes"] and "Root" in dag["nodes"]
    assert ["Root", "Leaf"] in dag["edges"]


def test_build_guards_raise(fragment_factory):
    c = fragment_factory(SimpleCalibration)
    with pytest.raises(TypeError):
        c.set_timeout(5)
    with pytest.raises(TypeError):
        c.setattr_param_optimizable("late", "Too late", 0, 1, default=0.5)
