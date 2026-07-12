"""qbutler - Manage a complex research experiment with lots of moving parts and drifting calibrations automatically and repeatably. """

__author__ = "Charles Baynham <charles.baynham@gmail.com>"
__all__ = []
__version__ = "0.2"

# Patch setattr_calibration into ndscan if qbutler is already installed
try:
    from . import entrypoints

    del entrypoints
except ImportError:
    pass

# Re-export the user-facing API so a client can `from qbutler import ...`.
# Guarded: the submodules need artiq/ndscan, which may be absent (e.g. docs).
try:
    from .calibration import Calibration
    from .calibration import CalibrationError
    from .calibration import CalibrationEscape
    from .calibration import CalibrationResult
    from .client import CalibratedExpFragment
    from .client import make_calibrated_experiment
    from .precompile import PrecompilePool

    __all__ += [
        "Calibration",
        "CalibrationError",
        "CalibrationEscape",
        "CalibrationResult",
        "CalibratedExpFragment",
        "make_calibrated_experiment",
        "PrecompilePool",
    ]
except ImportError:
    pass

# Patch CalibrationResult encoding into sipyco
try:
    from . import patch_sipyco

    del patch_sipyco
except ImportError:
    pass
