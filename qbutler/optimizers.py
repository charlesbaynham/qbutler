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
