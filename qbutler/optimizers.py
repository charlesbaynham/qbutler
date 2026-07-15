"""Built-in optimizers for Calibration parameter search.

This module is self-contained: it does not depend on ndscan or ARTIQ,
making it straightforward to unit-test independently.
"""

import itertools
import logging
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


NUM_SCAN_POINT = 11


@dataclass
class ParamSpec:
    name: str
    min: float
    max: float
    handle: Any


def grid_search_optimizer(param_specs, num_points=NUM_SCAN_POINT):
    """Built-in N-dimensional grid search optimizer.

    A generator that yields one ``{name: value}`` dict per grid point.  It
    does not receive feedback (the ``send`` value is ignored), so it simply
    exhausts the full Cartesian product of linearly-spaced values.

    Args:
        param_specs: Sequence of :class:`ParamSpec` objects describing each
            parameter's name and ``[min, max]`` range.
        num_points: Number of evenly-spaced points along each axis.

    Yields:
        dict[str, float]: Mapping of parameter name to proposed value.
    """
    n_params = len(param_specs)
    n_points = num_points**n_params
    if n_points > 500:
        warnings.warn(
            f"Grid search will evaluate {n_points} points ({n_params}D × {num_points} points). "
            "Consider using a custom optimizer for high-dimensional spaces.",
            UserWarning,
        )

    axes = [np.linspace(spec.min, spec.max, num_points) for spec in param_specs]
    names = [spec.name for spec in param_specs]

    logger.debug("Running grid search over %s parameters: %s", n_params, names)

    for point in itertools.product(*axes):
        yield dict(zip(names, point))


def _better(candidate, incumbent, optimization_type):
    """Return True if ``candidate`` beats ``incumbent`` under the given strategy.

    Mirrors :meth:`Calibration._is_better` so the zoom optimizer centres its
    refinement on the same point the driver would ultimately select.
    """
    if incumbent is None:
        return True
    if optimization_type == "max":
        return candidate > incumbent
    if optimization_type == "min":
        return candidate < incumbent
    if optimization_type == "zero":
        return abs(candidate) < abs(incumbent)
    raise ValueError(f"Unknown optimization_type: {optimization_type!r}")


def _refined_axis(spec, center, num_points, zoom_factor):
    """A linearly-spaced axis of ``num_points`` centred on ``center``.

    The window spans the original ``[min, max]`` range shrunk by
    ``zoom_factor`` and is clamped to ``[min, max]``. Where the range allows,
    a window that overruns an edge is shifted back inside the bounds so it
    keeps its full (zoomed) width instead of being truncated.
    """
    span = (spec.max - spec.min) / zoom_factor
    lo = center - span / 2
    hi = center + span / 2
    if lo < spec.min:
        hi += spec.min - lo
        lo = spec.min
    if hi > spec.max:
        lo -= hi - spec.max
        hi = spec.max
    lo = max(lo, spec.min)
    hi = min(hi, spec.max)
    return np.linspace(lo, hi, num_points)


def zoom_grid_optimizer(
    num_points=NUM_SCAN_POINT, zoom_factor=10, optimization_type="max"
):
    """Two-stage "zoom" grid search.

    Stage 1 scans the full ``[min, max]`` grid exactly like
    :func:`grid_search_optimizer`. It then picks the best point (per
    ``optimization_type``) and, in stage 2, re-scans the same number of points
    per axis over a window centred on that point whose width is ``zoom_factor``
    times smaller, refining the estimate of the optimum.

    Unlike :func:`grid_search_optimizer`, this optimizer consumes the
    ``(result, data)`` feedback sent for each point in order to locate the
    stage-1 optimum. A point is eligible to become the stage-1 centre only if
    its ``data`` is a finite real number; the driving :class:`Calibration`
    remains the sole authority on which point is ultimately chosen (it enforces
    the ``OK`` result requirement), so a spurious point can at worst mis-centre
    the refinement, never corrupt the final result.

    This is a factory: it returns a generator function suitable for
    :meth:`Calibration.set_optimizer`, e.g.
    ``self.set_optimizer(zoom_grid_optimizer(zoom_factor=10))``.

    Args:
        num_points: Number of evenly-spaced points along each axis, in *each*
            stage.
        zoom_factor: How much smaller the stage-2 window is than the full range
            (default 10, i.e. one tenth of the width).
        optimization_type: ``"max"`` (default), ``"min"`` or ``"zero"``. Must
            match the Calibration's own optimization type so the refinement
            centres on the right point.

    Returns:
        Callable[[list[ParamSpec]], Generator]: an optimizer generator function.
    """
    if zoom_factor <= 0:
        raise ValueError(f"zoom_factor must be positive, got {zoom_factor}")
    if num_points < 1:
        raise ValueError(f"num_points must be >= 1, got {num_points}")
    optimization_type = optimization_type.lower()
    if optimization_type not in ("max", "min", "zero"):
        raise ValueError(f"Unknown optimization_type: {optimization_type!r}")

    def optimizer(param_specs):
        n_params = len(param_specs)
        n_points = num_points**n_params
        if n_points > 500:
            warnings.warn(
                f"Zoom grid search will evaluate up to {2 * n_points} points "
                f"({n_params}D × {num_points} points × 2 stages). "
                "Consider using a custom optimizer for high-dimensional spaces.",
                UserWarning,
            )

        names = [spec.name for spec in param_specs]
        coarse_axes = [np.linspace(s.min, s.max, num_points) for s in param_specs]

        logger.debug(
            "Running zoom grid search (zoom_factor=%s, %s) over: %s",
            zoom_factor,
            optimization_type,
            names,
        )

        best_value = None
        best_point = None

        # Stage 1: full-range coarse grid.
        for point in itertools.product(*coarse_axes):
            params = dict(zip(names, point))
            _, data = yield params
            if isinstance(data, (int, float)) and np.isfinite(data):
                if _better(data, best_value, optimization_type):
                    best_value = data
                    best_point = params

        if best_point is None:
            # Nothing usable in stage 1; let the driver raise on no valid params.
            return None

        logger.debug(
            "Zoom stage 1 optimum %s (value=%s); refining", best_point, best_value
        )

        # Stage 2: refined grid centred on the stage-1 optimum.
        refined_axes = [
            _refined_axis(s, best_point[s.name], num_points, zoom_factor)
            for s in param_specs
        ]
        for point in itertools.product(*refined_axes):
            yield dict(zip(names, point))

        return None

    return optimizer
