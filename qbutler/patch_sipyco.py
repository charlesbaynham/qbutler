import logging

from sipyco import pyon

from qbutler.calibration import CalibrationResult

logger = logging.getLogger(__name__)


def encode_calibrationresult(self, r):
    # Just convert CalibrationResults into integers - they're basically the same.
    # Hopefully I don't regret this
    return str(int(r))


if hasattr(pyon, "register"):
    # This is a newer version of sipyco that supports custom encoders
    try:
        pyon.register(
            [CalibrationResult],
            name="calibrationresult",
            encode=lambda r: str(int(r)),
            decode=lambda x: x,
        )

        logger.debug("CalibrationResult registered with sipyco.pyon")
    except AssertionError:
        # Already registered - we are being scanned by ARTIQ
        pass

else:
    # This is an old version of sipyco that doesn't support custom encoders
    pyon._encode_map[CalibrationResult] = "calibrationresult"
    pyon._Encoder.encode_calibrationresult = encode_calibrationresult

    logger.debug("sipyco.pyon manually patched with CalibrationResult handler")
