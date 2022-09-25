from weakref import ref

import matplotlib.pyplot as plt
import networkx as nx
import pytest

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


@pytest.fixture
def simple_network(calibration_factory):
    class DepA(Calibration):
        def build_calibration(self):
            pass

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    class DepB(Calibration):
        def build_calibration(self):
            self.add_dependency(DepA)

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    class DepC(Calibration):
        def build_calibration(self):
            self.add_dependency(DepB)

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    return calibration_factory(DepC)


@pytest.fixture
def complex_network(calibration_factory):
    class Dep1A(Calibration):
        def build_calibration(self):
            pass

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    class Dep1B(Calibration):
        def build_calibration(self):
            pass

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    class Dep2A(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep1A)
            self.add_dependency(Dep1B)

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    class Dep3A(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep2A)

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    class Dep3B(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep2A)

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    class Dep4A(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep3A)
            self.add_dependency(Dep3B)

        def check_own_state(self) -> CalibrationResult:
            return CalibrationResult.OK

        def fix_own_state(self) -> None:
            pass

    return calibration_factory(Dep4A)


def test_complex_network_build(complex_network: Calibration, plot_graph):
    plot_graph("complex_network")


def test_simple_network_build(simple_network: Calibration, plot_graph):
    plot_graph("simple_network")


def test_simple_network_size(simple_network: Calibration):
    dag = simple_network._get_dag()

    assert len(dag) == 3


def test_simple_network_deps(simple_network: Calibration):
    assert len(simple_network.get_dependencies()) == 3


def test_complex_network_size(complex_network: Calibration):
    dag = complex_network._get_dag()

    assert len(dag) == 6


def test_complex_network_deps(complex_network: Calibration):
    assert len(complex_network.get_dependencies()) == 6
