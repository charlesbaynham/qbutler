"""Unit tests for qbutler.optimizers."""

import warnings

import numpy as np
import pytest

from qbutler.optimizers import NUM_SCAN_POINT
from qbutler.optimizers import ParamSpec
from qbutler.optimizers import grid_search_optimizer


def make_spec(name, lo, hi):
    """Helper: create a ParamSpec with a dummy handle."""
    return ParamSpec(name=name, min=lo, max=hi, handle=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def collect(gen):
    """Drain a generator into a list."""
    return list(gen)


# ---------------------------------------------------------------------------
# Basic structural tests
# ---------------------------------------------------------------------------


def test_1d_yields_correct_number_of_points():
    specs = [make_spec("x", 0.0, 1.0)]
    points = collect(grid_search_optimizer(specs, num_points=5))
    assert len(points) == 5


def test_2d_yields_cartesian_product_count():
    specs = [make_spec("x", 0.0, 1.0), make_spec("y", -1.0, 1.0)]
    points = collect(grid_search_optimizer(specs, num_points=4))
    assert len(points) == 4 * 4


def test_3d_yields_cartesian_product_count():
    specs = [make_spec("a", 0, 1), make_spec("b", 0, 1), make_spec("c", 0, 1)]
    points = collect(grid_search_optimizer(specs, num_points=3))
    assert len(points) == 3**3


# ---------------------------------------------------------------------------
# Key / value content tests
# ---------------------------------------------------------------------------


def test_yields_dicts_with_correct_keys():
    specs = [make_spec("freq", 100.0, 200.0), make_spec("amp", 0.0, 1.0)]
    for pt in grid_search_optimizer(specs, num_points=3):
        assert set(pt.keys()) == {"freq", "amp"}


def test_1d_covers_min_and_max():
    specs = [make_spec("x", 2.0, 8.0)]
    points = collect(grid_search_optimizer(specs, num_points=7))
    values = [pt["x"] for pt in points]
    assert values[0] == pytest.approx(2.0)
    assert values[-1] == pytest.approx(8.0)


def test_2d_covers_extremes():
    specs = [make_spec("x", 0.0, 1.0), make_spec("y", 10.0, 20.0)]
    points = collect(grid_search_optimizer(specs, num_points=5))
    xs = sorted({pt["x"] for pt in points})
    ys = sorted({pt["y"] for pt in points})
    assert xs[0] == pytest.approx(0.0)
    assert xs[-1] == pytest.approx(1.0)
    assert ys[0] == pytest.approx(10.0)
    assert ys[-1] == pytest.approx(20.0)


def test_values_are_evenly_spaced():
    specs = [make_spec("x", 0.0, 1.0)]
    points = collect(grid_search_optimizer(specs, num_points=11))
    values = [pt["x"] for pt in points]
    expected = np.linspace(0.0, 1.0, 11)
    np.testing.assert_allclose(values, expected)


# ---------------------------------------------------------------------------
# Default num_points
# ---------------------------------------------------------------------------


def test_default_num_points_matches_constant():
    specs = [make_spec("x", 0.0, 1.0)]
    points = collect(grid_search_optimizer(specs))
    assert len(points) == NUM_SCAN_POINT


# ---------------------------------------------------------------------------
# Warning for large grids
# ---------------------------------------------------------------------------


def test_no_warning_for_small_grid():
    specs = [make_spec("x", 0.0, 1.0)]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        collect(grid_search_optimizer(specs, num_points=5))  # 5 points, no warning


def test_warning_when_points_exceed_500():
    # 3D × 10 points = 1000 > 500
    specs = [make_spec(c, 0.0, 1.0) for c in "abc"]
    with pytest.warns(UserWarning, match="Grid search will evaluate"):
        collect(grid_search_optimizer(specs, num_points=10))


def test_warning_threshold_is_strictly_above_500():
    # Exactly 500 points should NOT warn; 501 would, but linspace gives integers
    # Use 1D × 500 points — no warning expected
    specs = [make_spec("x", 0.0, 1.0)]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        collect(grid_search_optimizer(specs, num_points=500))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_single_point_per_axis():
    specs = [make_spec("x", 5.0, 5.0), make_spec("y", 3.0, 3.0)]
    points = collect(grid_search_optimizer(specs, num_points=1))
    assert len(points) == 1
    assert points[0]["x"] == pytest.approx(5.0)
    assert points[0]["y"] == pytest.approx(3.0)


def test_generator_protocol_send_is_ignored():
    """grid_search_optimizer doesn't use send() values; confirm it doesn't crash."""
    specs = [make_spec("x", 0.0, 1.0)]
    gen = grid_search_optimizer(specs, num_points=3)
    pt = next(gen)
    assert isinstance(pt, dict)
    # Sending a (result, data) tuple should not raise
    pt2 = gen.send(("OK", 0.5))
    assert isinstance(pt2, dict)
