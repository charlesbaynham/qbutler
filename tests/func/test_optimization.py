import pytest

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class ParamsCalibration(Calibration):
    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0, 1, default=0.5)

    def run_once(self) -> None:
        self.status.push(CalibrationResult.OK)
        self.data.push(10 * self.test.get())


def test_can_make_params_calibration(fragment_factory):
    c = fragment_factory(ParamsCalibration)
    assert c.test.get() == 0.5


def test_can_optimize(fragment_factory):
    c = fragment_factory(ParamsCalibration)
    c.fix_state(force=True)


def test_cannot_optimize_without_params(fragment_factory):
    class Cali(Calibration):
        def build_calibration(self):
            pass

    c = fragment_factory(Cali)

    with pytest.raises(ValueError):
        c.fix_state(force=True)


def test_cannot_optimize_without_run_once(fragment_factory):
    class Cali(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("test", "A test", 0, 1, default=0.5)

    c = fragment_factory(Cali)

    with pytest.raises(NotImplementedError):
        c.fix_state(force=True)


def test_optimize_once_works(fragment_factory):
    c = fragment_factory(ParamsCalibration)
    assert c.test.get() == 0.5
    c.fix_state(force=True)
    assert c.test.get() == 1
    assert c.check_state()[0] == CalibrationResult.OK


def test_can_optimize_twice(fragment_factory):
    c = fragment_factory(ParamsCalibration)

    c.fix_state(force=True)
    c.fix_state(force=True)


def test_optimize_twice_works(fragment_factory):
    c = fragment_factory(ParamsCalibration)

    assert c.test.get() == 0.5
    c.fix_state(force=True)
    assert c.test.get() == 1
    assert c.check_state()[0] == CalibrationResult.OK

    c.fix_state(force=True)
    assert c.test.get() == 1
    assert c.check_state()[0] == CalibrationResult.OK


@pytest.mark.parametrize(
    ("strategy", "expected_result"),
    [
        ("max", 1.0),
        pytest.param("min", -1.0, marks=pytest.mark.xfail),
        pytest.param("zero", 0.0, marks=pytest.mark.xfail),
    ],
)
def test_strategies(fragment_factory, strategy, expected_result):
    class OptimizingCalibration(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("test", "A test", -1, 1, default=0.5)
            self.set_optimization_type(strategy)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)
            self.data.push(self.test.get())

    c = fragment_factory(OptimizingCalibration)
    assert c.test.get() == 0.5
    c.fix_state(force=True)
    assert c.check_state()[0] == CalibrationResult.OK
    assert c.test.get() == expected_result


def test_optimum_params_are_saved(fragment_factory, dataset_db):
    c = fragment_factory(ParamsCalibration)
    dataset_key = c._param_dataset_key_from_name("test")

    assert dataset_db.get(dataset_key) == 0.5

    c.fix_state(force=True)

    assert dataset_db.get(dataset_key) == 1.0


def test_optimum_params_are_remembered(fragment_factory):
    c = fragment_factory(ParamsCalibration)
    assert c.test.get() == 0.5
    c.fix_state(force=True)
    assert c.test.get() == 1.0

    del c

    c = fragment_factory(ParamsCalibration)
    assert c.test.get() == 1.0
