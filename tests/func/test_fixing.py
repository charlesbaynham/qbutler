from time import time

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class OverriddenFixableCalibration(Calibration):
    def build_calibration(self):
        self.broken = True

    def run_once(self) -> None:
        if self.broken:
            self.status.push(CalibrationResult.BAD_DATA)
        else:
            self.status.push(CalibrationResult.OK)

    def fix_own_state(self) -> CalibrationResult:
        self.broken = False


def test_can_fix_broken_calibration(experiment_factory):
    c: Calibration = experiment_factory(OverriddenFixableCalibration)

    assert c.check_state() == CalibrationResult.BAD_DATA

    c.fix_own_state()

    assert c.check_state() == CalibrationResult.OK


class DependantCalibration(Calibration):
    def build_calibration(self):
        self.add_dependency(OverriddenFixableCalibration)

    def run_once(self) -> None:
        self.status.push(CalibrationResult.OK)

    def fix_own_state(self) -> None:
        pass


def test_can_fix_broken_child_calibration(experiment_factory):
    c: Calibration = experiment_factory(DependantCalibration)

    assert c.check_state() == CalibrationResult.BAD_DATA

    c.fix_state()

    assert c.check_state() == CalibrationResult.OK


def test_correct_order_fixes(experiment_factory):

    log_calls = {}

    def log_a_call(k):
        if k not in log_calls:
            log_calls[k] = []
        log_calls[k].append(time())

    class Dep1A(Calibration):
        def build_calibration(self):
            pass

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            log_a_call(self.__class__)

    class Dep1B(Calibration):
        def build_calibration(self):
            pass

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            log_a_call(self.__class__)

    class Dep2A(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep1A)
            self.add_dependency(Dep1B)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            log_a_call(self.__class__)

    class Dep3A(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep2A)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            log_a_call(self.__class__)

    class Dep3B(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep2A)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            log_a_call(self.__class__)

    class Dep4A(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep3A)
            self.add_dependency(Dep3B)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            log_a_call(self.__class__)

    c: Calibration = experiment_factory(Dep4A)

    c.fix_state(force=True)

    print(log_calls)

    # Check all classes only fixed once each
    assert all([len(call_times) == 1 for _, call_times in log_calls.items()])

    assert log_calls[Dep1A][0] < log_calls[Dep2A][0]
    assert log_calls[Dep1B][0] < log_calls[Dep2A][0]

    assert log_calls[Dep2A][0] < log_calls[Dep3A][0]
    assert log_calls[Dep2A][0] < log_calls[Dep3B][0]

    assert log_calls[Dep3A][0] < log_calls[Dep4A][0]
    assert log_calls[Dep3B][0] < log_calls[Dep4A][0]
