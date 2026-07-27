import pytest

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.optimizers import zoom_grid_optimizer


class ParamsCalibration(Calibration):
    def build_calibration(self):
        self.setattr_param_optimizable("test", "A test", 0, 1, default=0.5)

    def check_own_state(self):
        return CalibrationResult.OK, 10 * self.test.get()


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


def test_cannot_optimize_without_check_own_state(fragment_factory):
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
        pytest.param("min", -1.0),
        pytest.param("zero", 0.0),
    ],
)
def test_strategies(fragment_factory, strategy, expected_result):
    class OptimizingCalibration(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("test", "A test", -1, 1, default=0.5)
            self.set_optimization_type(strategy)

        def check_own_state(self):
            return CalibrationResult.OK, self.test.get()

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


import numpy as np


def test_grid_search_2d_max(fragment_factory):
    center_x, center_y = 0.3, -0.2
    sigma = 0.5

    class Gaussian2DMax(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("x", "X", -1, 1, default=0.0)
            self.setattr_param_optimizable("y", "Y", -1, 1, default=0.0)
            self.set_optimization_type("max")

        def check_own_state(self):
            x = self.x.get()
            y = self.y.get()
            g = np.exp(-((x - center_x) ** 2 + (y - center_y) ** 2) / (2 * sigma**2))
            return CalibrationResult.OK, g

    c = fragment_factory(Gaussian2DMax)
    c.fix_state(force=True)

    assert c.check_state()[0] == CalibrationResult.OK
    assert abs(c.x.get() - center_x) < 0.15
    assert abs(c.y.get() - center_y) < 0.15


def test_grid_search_2d_min(fragment_factory):
    center_x, center_y = 0.3, -0.2
    sigma = 0.5

    class Gaussian2DMin(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("x", "X", -1, 1, default=0.0)
            self.setattr_param_optimizable("y", "Y", -1, 1, default=0.0)
            self.set_optimization_type("min")

        def check_own_state(self):
            x = self.x.get()
            y = self.y.get()
            g = np.exp(-((x - center_x) ** 2 + (y - center_y) ** 2) / (2 * sigma**2))
            return CalibrationResult.OK, 1.0 - g

    c = fragment_factory(Gaussian2DMin)
    c.fix_state(force=True)

    assert c.check_state()[0] == CalibrationResult.OK
    assert abs(c.x.get() - center_x) < 0.15
    assert abs(c.y.get() - center_y) < 0.15


def test_grid_search_3d_max(fragment_factory):
    center_x, center_y, center_z = 0.3, -0.2, 0.4
    sigma = 0.5

    class Gaussian3DMax(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("x", "X", -1, 1, default=0.0)
            self.setattr_param_optimizable("y", "Y", -1, 1, default=0.0)
            self.setattr_param_optimizable("z", "Z", -1, 1, default=0.0)
            self.set_optimization_type("max")

        def check_own_state(self):
            x = self.x.get()
            y = self.y.get()
            z = self.z.get()
            g = np.exp(
                -((x - center_x) ** 2 + (y - center_y) ** 2 + (z - center_z) ** 2)
                / (2 * sigma**2)
            )
            return CalibrationResult.OK, g

    c = fragment_factory(Gaussian3DMax)
    c.fix_state(force=True)

    assert c.check_state()[0] == CalibrationResult.OK
    assert abs(c.x.get() - center_x) < 0.15
    assert abs(c.y.get() - center_y) < 0.15
    assert abs(c.z.get() - center_z) < 0.15


def test_grid_search_4d_max(fragment_factory):
    center = (0.3, -0.2, 0.4, -0.1)
    sigma = 0.5

    class Gaussian4DMax(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("x", "X", -1, 1, default=0.0)
            self.setattr_param_optimizable("y", "Y", -1, 1, default=0.0)
            self.setattr_param_optimizable("z", "Z", -1, 1, default=0.0)
            self.setattr_param_optimizable("w", "W", -1, 1, default=0.0)
            self.set_optimization_type("max")

        def check_own_state(self):
            vals = (self.x.get(), self.y.get(), self.z.get(), self.w.get())
            g = np.exp(
                -sum((v - c) ** 2 for v, c in zip(vals, center)) / (2 * sigma**2)
            )
            return CalibrationResult.OK, g

    import warnings

    c = fragment_factory(Gaussian4DMax)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        c.fix_state(force=True)

    assert c.check_state()[0] == CalibrationResult.OK
    assert abs(c.x.get() - center[0]) < 0.15
    assert abs(c.y.get() - center[1]) < 0.15
    assert abs(c.z.get() - center[2]) < 0.15
    assert abs(c.w.get() - center[3]) < 0.15


def test_zoom_grid_optimizer_refines_beyond_coarse_grid(fragment_factory):
    # Optimum deliberately placed between coarse-grid nodes so a single-pass
    # grid search can only get within half a grid spacing (~0.1 over [-1, 1]
    # with 11 points), but the zoom pass should land far closer.
    center_x, center_y = 0.37, -0.24
    sigma = 0.5

    class Gaussian2DZoom(Calibration):
        def build_calibration(self):
            self.setattr_param_optimizable("x", "X", -1, 1, default=0.0)
            self.setattr_param_optimizable("y", "Y", -1, 1, default=0.0)
            self.set_optimization_type("max")
            self.set_optimizer(zoom_grid_optimizer(zoom_factor=10))

        def check_own_state(self):
            x = self.x.get()
            y = self.y.get()
            g = np.exp(-((x - center_x) ** 2 + (y - center_y) ** 2) / (2 * sigma**2))
            return CalibrationResult.OK, g

    c = fragment_factory(Gaussian2DZoom)
    c.fix_state(force=True)

    assert c.check_state()[0] == CalibrationResult.OK
    # Far tighter than the 0.15 the coarse grid alone achieves.
    assert abs(c.x.get() - center_x) < 0.02
    assert abs(c.y.get() - center_y) < 0.02
