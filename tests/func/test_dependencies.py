from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class GoodCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self):
        return CalibrationResult.OK, None


class BadCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self):
        return CalibrationResult.BAD_DATA, None


def test_good_and_bad_calibrations_work_as_expected(fragment_factory):
    c_good = fragment_factory(GoodCalibration)
    c_bad = fragment_factory(BadCalibration)

    assert c_good.check_state()[0] == CalibrationResult.OK
    assert c_bad.check_state()[0] == CalibrationResult.BAD_DATA


class CalibrationWithGoodDependency(Calibration):
    def build_calibration(self):
        self.add_dependency(GoodCalibration)

    def check_own_state(self):
        return CalibrationResult.OK, None


class CalibrationWithBadDependency(Calibration):
    def build_calibration(self):
        self.add_dependency(BadCalibration)

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_inherit_good(fragment_factory):
    c = fragment_factory(CalibrationWithGoodDependency)

    assert c.check_state()[0] == CalibrationResult.OK


def test_inherit_bad(fragment_factory):
    c = fragment_factory(CalibrationWithBadDependency)

    assert c.check_state()[0] == CalibrationResult.BAD_DEPS


class CalibrationWithRepeatedBadDependencies(Calibration):
    def build_calibration(self):
        self.add_dependency(BadCalibration, "dep1")
        self.add_dependency(BadCalibration, "dep2")

    def check_own_state(self):
        return CalibrationResult.OK, None


class AlternativeBadCalibration(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self):
        return CalibrationResult.BAD_DATA, None


class CalibrationWithMultipleBadDependencies(Calibration):
    def build_calibration(self):
        self.add_dependency(BadCalibration)
        self.add_dependency(AlternativeBadCalibration)

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_inherited_two_bad(fragment_factory):
    c = fragment_factory(CalibrationWithMultipleBadDependencies)

    assert c.check_state()[0] == CalibrationResult.BAD_DEPS


class CalibrationWithOneBadOneGoodDep(Calibration):
    def build_calibration(self):
        self.add_dependency(BadCalibration)
        self.add_dependency(GoodCalibration)

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_inherited_one_bad_one_good(fragment_factory):
    c = fragment_factory(CalibrationWithOneBadOneGoodDep)

    assert c.check_state()[0] == CalibrationResult.BAD_DEPS


def test_inherited_two_repeated_bad(fragment_factory):
    c = fragment_factory(CalibrationWithRepeatedBadDependencies)

    assert c.check_state()[0] == CalibrationResult.BAD_DEPS


def test_can_rename_calibrations(fragment_factory):
    c = fragment_factory(CalibrationWithRepeatedBadDependencies)

    assert hasattr(c, "dep1")
    assert hasattr(c, "dep2")
    assert not hasattr(c, "BadCalibration")


class SharedDependency(Calibration):
    def build_calibration(self):
        pass

    def check_own_state(self):
        return CalibrationResult.OK, None


class MiddleCal(Calibration):
    def build_calibration(self):
        self.add_dependency(SharedDependency)

    def check_own_state(self):
        return CalibrationResult.OK, None


class DiamondTop(Calibration):
    def build_calibration(self):
        # SharedDependency is created here...
        self.add_dependency(SharedDependency)
        # ...so MiddleCal's own add_dependency(SharedDependency) hits the
        # dedup path. The edge must still be recorded (issue #31).
        self.add_dependency(MiddleCal)

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_deduplicated_dependency_edge_is_recorded(fragment_factory):
    from qbutler import dag

    top = fragment_factory(DiamondTop)

    deps = dag.get_dependencies(top)
    assert [type(d).__name__ for d in deps] == [
        "SharedDependency",
        "MiddleCal",
        "DiamondTop",
    ]
