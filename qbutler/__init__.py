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

# Patch CalibrationResult encoding into sipyco
try:
    from . import patch_sipyco

    del patch_sipyco
except ImportError:
    pass
