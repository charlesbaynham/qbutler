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


# The full point list is known up front (send() feedback is ignored), so
# kernel-mode calibrations can evaluate the whole sweep in one kernel call.
grid_search_optimizer.batchable = True


def coordinate_descent_optimizer(param_specs, num_points=7, n_rounds=2):
    """Optimize one parameter at a time (a "walk-in"), using feedback.

    Round ``r`` scans each axis over a window of width ``(max - min) / 2**r``
    centred on the current best value, clamped to ``[min, max]``, with
    ``num_points`` evenly-spaced candidates. After each axis sweep the
    current point moves to the best OK candidate; if no candidate was OK the
    axis keeps its previous value. Total evaluations:
    ``n_rounds * len(param_specs) * num_points``.

    Assumes the optimization target is "max" (larger data = better).

    Feedback protocol: ``result, data = yield {name: value, ...}``, where
    ``result`` is the CalibrationResult of measuring the yielded point
    (OK == 0; compared via ``int(result)`` so this module stays free of
    qbutler imports) and ``data`` is the metric.

    Returns the best ``{name: value}`` dict via ``StopIteration.value``.
    """
    current = {spec.name: spec.handle.get() for spec in param_specs}

    logger.debug(
        "Coordinate descent over %s from %s (%s rounds, %s points/axis)",
        [s.name for s in param_specs],
        current,
        n_rounds,
        num_points,
    )

    for rnd in range(n_rounds):
        for spec in param_specs:
            centre = current[spec.name]
            half_window = (spec.max - spec.min) / 2 ** (rnd + 1)
            lo = max(spec.min, centre - half_window)
            hi = min(spec.max, centre + half_window)

            best_value = None
            best_data = None
            for v in np.linspace(lo, hi, num_points):
                result, data = yield {**current, spec.name: float(v)}
                if int(result) != 0 or not isinstance(data, (int, float)):
                    continue
                if best_data is None or data > best_data:
                    best_data, best_value = data, float(v)

            if best_value is not None:
                current[spec.name] = best_value
            logger.debug(
                "Round %s axis %s: best %s (data %s)",
                rnd,
                spec.name,
                best_value,
                best_data,
            )

    return current
