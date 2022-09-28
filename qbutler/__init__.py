"""qbutler - Manage a complex research experiment with lots of moving parts and drifting calibrations automatically and repeatably. """

__author__ = "Charles Baynham <charles.baynham@gmail.com>"
__all__ = []

from ._version import get_version

__version__ = get_version()
del get_version

# Patch setattr_calibration into ndscan if qbutler is already installed
try:
    from . import entrypoints

    del entrypoints
except ImportError:
    pass
