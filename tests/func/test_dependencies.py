from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class GoodCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


class BadCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.BAD_DATA


def test_good_and_bad_calibrations_work_as_expected(calibration_factory):
    c_good = calibration_factory(GoodCalibration)
    c_bad = calibration_factory(BadCalibration)

    assert c_good.check_state() == CalibrationResult.OK
    assert c_bad.check_state() == CalibrationResult.BAD_DATA


class CalibrationWithGoodDependency(Calibration):
    def build_calibration(self):
        self.add_dependency(GoodCalibration)

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


class CalibrationWithBadDependency(Calibration):
    def build_calibration(self):
        self.add_dependency(BadCalibration)

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


def test_inherit_good(calibration_factory):
    c = calibration_factory(CalibrationWithGoodDependency)

    assert c.check_state() == CalibrationResult.OK


def test_inherit_bad(calibration_factory):
    c = calibration_factory(CalibrationWithBadDependency)

    assert c.check_state() == CalibrationResult.BAD_DATA


class CalibrationWithRepeatedBadDependencies(Calibration):
    def build_calibration(self):
        self.add_dependency(BadCalibration, "dep1")
        self.add_dependency(BadCalibration, "dep2")

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


class AlternativeBadCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.BAD_DATA


class CalibrationWithMultipleBadDependencies(Calibration):
    def build_calibration(self):
        self.add_dependency(BadCalibration)
        self.add_dependency(AlternativeBadCalibration)

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


def test_inherited_two_bad(calibration_factory):
    c = calibration_factory(CalibrationWithMultipleBadDependencies)

    assert c.check_state() == CalibrationResult.BAD_DATA


class CalibrationWithOneBadOneGoodDep(Calibration):
    def build_calibration(self):
        self.add_dependency(BadCalibration)
        self.add_dependency(GoodCalibration)

    def check_own_state(self) -> CalibrationResult:
        return CalibrationResult.OK


def test_inherited_one_bad_one_good(calibration_factory):
    c = calibration_factory(CalibrationWithOneBadOneGoodDep)

    assert c.check_state() == CalibrationResult.BAD_DATA


def test_inherited_two_repeated_bad(calibration_factory):
    c = calibration_factory(CalibrationWithRepeatedBadDependencies)

    assert c.check_state() == CalibrationResult.BAD_DATA


def test_can_rename_calibrations(calibration_factory):
    c = calibration_factory(CalibrationWithRepeatedBadDependencies)

    assert hasattr(c, "dep1")
    assert hasattr(c, "dep2")
    assert not hasattr(c, "BadCalibration")
