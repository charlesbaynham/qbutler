import logging

from sipyco import pyon

from qbutler.calibration import CalibrationResult

logger = logging.getLogger(__name__)


def encode_calibrationresult(self, r):
    # Just convert CalibrationResults into integers - they're basically the same.
    # Hopefully I don't regret this
    return str(int(r))


pyon._encode_map[CalibrationResult] = "calibrationresult"
pyon._Encoder.encode_calibrationresult = encode_calibrationresult

logger.debug("sipyco.pyon patched with CalibrationResult handler")
