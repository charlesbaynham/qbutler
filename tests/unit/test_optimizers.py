"""Unit tests for qbutler.optimizers."""

import warnings

import numpy as np
import pytest

from qbutler.optimizers import NUM_SCAN_POINT
from qbutler.optimizers import ParamSpec
from qbutler.optimizers import grid_search_optimizer
from qbutler.optimizers import zoom_grid_optimizer


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


# ---------------------------------------------------------------------------
# zoom_grid_optimizer
# ---------------------------------------------------------------------------


OK = 0  # CalibrationResult.OK == 0
BAD = 1  # any non-zero result is not OK


def drive(optimizer, objective, result=OK):
    """Drive a feedback-consuming optimizer generator to exhaustion.

    Mirrors ``Calibration._run_optimizer``: for each yielded param dict, feed
    back ``(result, objective(params))``. ``result`` may be a constant or a
    callable ``params -> result``. Returns the list of yielded points.
    """
    points = []

    def result_for(params):
        return result(params) if callable(result) else result

    try:
        params = next(optimizer)
        while True:
            points.append(params)
            params = optimizer.send((result_for(params), objective(params)))
    except StopIteration:
        pass
    return points


def test_zoom_two_stages_yield_double_the_points():
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=5)(specs)
    points = drive(opt, lambda p: -((p["x"] - 0.5) ** 2))
    # Stage 1 (5) + stage 2 (5)
    assert len(points) == 2 * 5


def test_zoom_stage1_covers_full_range():
    specs = [make_spec("x", 2.0, 8.0)]
    opt = zoom_grid_optimizer(num_points=7)(specs)
    points = drive(opt, lambda p: -((p["x"] - 5.0) ** 2))
    stage1 = points[:7]
    xs = [p["x"] for p in stage1]
    assert xs[0] == pytest.approx(2.0)
    assert xs[-1] == pytest.approx(8.0)


def test_zoom_stage2_is_centred_on_optimum_and_zoomed():
    # 1D parabola peaking at x = 0.5 over [0, 1]; num_points odd so a coarse
    # node lands exactly on 0.5.
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=11, zoom_factor=10)(specs)
    points = drive(opt, lambda p: -((p["x"] - 0.5) ** 2))
    stage2 = points[11:]
    xs = [p["x"] for p in stage2]
    # Centred on the coarse optimum (0.5)...
    assert np.mean(xs) == pytest.approx(0.5)
    # ...and 10x smaller: full width 1.0 -> refined width 0.1
    assert (max(xs) - min(xs)) == pytest.approx(0.1)
    assert min(xs) == pytest.approx(0.45)
    assert max(xs) == pytest.approx(0.55)


def test_zoom_refines_estimate_better_than_coarse_alone():
    # Optimum at an irrational-ish point the coarse grid misses.
    center = 0.5234
    specs = [make_spec("x", 0.0, 1.0)]

    def objective(p):
        return -((p["x"] - center) ** 2)

    opt = zoom_grid_optimizer(num_points=11, zoom_factor=10)(specs)
    points = drive(opt, objective)
    best = max(points, key=objective)
    coarse_best = max(points[:11], key=objective)
    # The refined pass gets strictly closer to the true optimum.
    assert abs(best["x"] - center) < abs(coarse_best["x"] - center)


def test_zoom_window_clamped_and_shifted_at_edge():
    # Optimum at the max edge: refined window must stay within [min, max]
    # and keep its zoomed width by shifting inward.
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=11, zoom_factor=10)(specs)
    points = drive(opt, lambda p: p["x"])  # monotonic increasing -> best at 1.0
    stage2 = points[11:]
    xs = [p["x"] for p in stage2]
    assert min(xs) >= 0.0
    assert max(xs) == pytest.approx(1.0)
    # Width preserved (0.1) by shifting the window inward.
    assert (max(xs) - min(xs)) == pytest.approx(0.1)


def test_zoom_min_strategy_centres_on_minimum():
    specs = [make_spec("x", 0.0, 1.0)]
    # Parabola with a minimum at 0.3.
    opt = zoom_grid_optimizer(num_points=11, optimization_type="min")(specs)
    points = drive(opt, lambda p: (p["x"] - 0.3) ** 2)
    stage2 = points[11:]
    xs = [p["x"] for p in stage2]
    assert np.mean(xs) == pytest.approx(0.3)


def test_zoom_zero_strategy_centres_on_root():
    specs = [make_spec("x", -1.0, 1.0)]
    # Signal crossing zero at x = 0.2 (coarse node at 0.2 with num_points=11).
    opt = zoom_grid_optimizer(num_points=11, optimization_type="zero")(specs)
    points = drive(opt, lambda p: p["x"] - 0.2)
    stage2 = points[11:]
    xs = [p["x"] for p in stage2]
    assert np.mean(xs) == pytest.approx(0.2)


def test_zoom_2d_yields_squared_counts_per_stage():
    specs = [make_spec("x", 0.0, 1.0), make_spec("y", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=4)(specs)
    points = drive(opt, lambda p: -((p["x"] - 0.5) ** 2 + (p["y"] - 0.5) ** 2))
    assert len(points) == 2 * (4 * 4)


def test_zoom_default_zoom_factor_is_10():
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=11)(specs)
    points = drive(opt, lambda p: -((p["x"] - 0.5) ** 2))
    xs = [p["x"] for p in points[11:]]
    assert (max(xs) - min(xs)) == pytest.approx(0.1)  # 1.0 / 10


def test_zoom_no_valid_points_returns_gracefully():
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=5)(specs)
    # All non-finite data -> no centre found -> only stage 1 runs.
    points = drive(opt, lambda p: float("nan"))
    assert len(points) == 5


def test_zoom_ignores_non_ok_points_when_centring():
    # The globally-highest data sits at x >= 0.8 but is reported BAD; the best
    # OK point is at x = 0.3, so the refine window must centre on 0.3.
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=11, zoom_factor=10)(specs)

    def objective(p):
        return p["x"]  # monotonically increasing -> raw max at the top edge

    def result(p):
        return BAD if p["x"] > 0.75 else OK  # top of the range is not OK

    points = drive(opt, objective, result=result)
    stage2 = points[11:]
    xs = [p["x"] for p in stage2]
    # Best OK coarse node is 0.7 (0.8, 0.9, 1.0 are BAD); centre there.
    assert np.mean(xs) == pytest.approx(0.7)


def test_zoom_only_stage1_if_all_points_bad():
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=5)(specs)
    # Every point BAD -> no OK centre -> refine pass skipped.
    points = drive(opt, lambda p: p["x"], result=BAD)
    assert len(points) == 5


def test_zoom_invalid_zoom_factor_raises():
    with pytest.raises(ValueError):
        zoom_grid_optimizer(zoom_factor=0)
    with pytest.raises(ValueError):
        zoom_grid_optimizer(zoom_factor=-3)


def test_zoom_invalid_optimization_type_raises():
    with pytest.raises(ValueError):
        zoom_grid_optimizer(optimization_type="sideways")


def test_zoom_invalid_num_points_raises():
    with pytest.raises(ValueError):
        zoom_grid_optimizer(num_points=0)


def test_zoom_warns_for_large_grid():
    specs = [make_spec(c, 0.0, 1.0) for c in "abc"]  # 3D x 10 = 1000 > 500
    with pytest.warns(UserWarning, match="Zoom grid search will evaluate"):
        opt = zoom_grid_optimizer(num_points=10)(specs)
        drive(opt, lambda p: -sum((v - 0.5) ** 2 for v in p.values()))


def test_zoom_default_n_stages_is_two():
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=5)(specs)
    points = drive(opt, lambda p: -((p["x"] - 0.5) ** 2))
    assert len(points) == 2 * 5


def test_zoom_n_stages_yields_that_many_grids():
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=7, n_stages=4)(specs)
    points = drive(opt, lambda p: -((p["x"] - 0.5) ** 2))
    assert len(points) == 4 * 7


def test_zoom_n_stages_one_is_plain_grid():
    specs = [make_spec("x", 2.0, 8.0)]
    opt = zoom_grid_optimizer(num_points=7, n_stages=1)(specs)
    points = drive(opt, lambda p: -((p["x"] - 5.0) ** 2))
    xs = [p["x"] for p in points]
    assert len(points) == 7  # coarse scan only, no refinement
    assert xs[0] == pytest.approx(2.0)
    assert xs[-1] == pytest.approx(8.0)


def test_zoom_each_stage_narrows_by_zoom_factor():
    # Full range 1.0; each successive stage's window is zoom_factor (10x)
    # narrower than the last: 1.0 -> 0.1 -> 0.01.
    specs = [make_spec("x", 0.0, 1.0)]
    opt = zoom_grid_optimizer(num_points=11, zoom_factor=10, n_stages=3)(specs)
    points = drive(opt, lambda p: -((p["x"] - 0.5) ** 2))
    widths = []
    for stage in range(3):
        xs = [p["x"] for p in points[stage * 11 : (stage + 1) * 11]]
        widths.append(max(xs) - min(xs))
    assert widths[0] == pytest.approx(1.0)
    assert widths[1] == pytest.approx(0.1)
    assert widths[2] == pytest.approx(0.01)


def test_zoom_more_stages_refine_estimate_further():
    # An optimum the coarse grid misses; more zoom stages get strictly closer.
    center = 0.523456
    specs = [make_spec("x", 0.0, 1.0)]

    def objective(p):
        return -((p["x"] - center) ** 2)

    two = drive(zoom_grid_optimizer(num_points=11, n_stages=2)(specs), objective)
    four = drive(zoom_grid_optimizer(num_points=11, n_stages=4)(specs), objective)
    best_two = max(two, key=objective)
    best_four = max(four, key=objective)
    assert abs(best_four["x"] - center) < abs(best_two["x"] - center)


def test_zoom_invalid_n_stages_raises():
    with pytest.raises(ValueError):
        zoom_grid_optimizer(n_stages=0)
    with pytest.raises(ValueError):
        zoom_grid_optimizer(n_stages=-2)
