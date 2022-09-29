from weakref import ref


import pytest

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult

import qbutler.dag


@pytest.fixture
def simple_network(fragment_factory):
    class DepA(Calibration):
        def build_calibration(self):
            pass

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            pass

    class DepB(Calibration):
        def build_calibration(self):
            self.add_dependency(DepA)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            pass

    class DepC(Calibration):
        def build_calibration(self):
            self.add_dependency(DepB)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            pass

    return fragment_factory(DepC)


@pytest.fixture
def complex_network(fragment_factory):
    class Dep1A(Calibration):
        def build_calibration(self):
            pass

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            pass

    class Dep1B(Calibration):
        def build_calibration(self):
            pass

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            pass

    class Dep2A(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep1A)
            self.add_dependency(Dep1B)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            pass

    class Dep3(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep2A)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            pass

    class Dep4A(Calibration):
        def build_calibration(self):
            self.add_dependency(Dep3, name="Dep3A", create_duplicates=True)
            self.add_dependency(Dep3, name="Dep3B", create_duplicates=True)

        def run_once(self) -> None:
            self.status.push(CalibrationResult.OK)

        def fix_own_state(self) -> None:
            pass

    return fragment_factory(Dep4A)


def test_complex_network_build(complex_network: Calibration, plot_graph):
    plot_graph("complex_network")


def test_simple_network_build(simple_network: Calibration, plot_graph):
    plot_graph("simple_network")


def test_simple_network_size(simple_network: Calibration):
    dag = qbutler.dag._get_graph_containing_calibration(simple_network)

    assert len(dag) == 3


def test_simple_network_deps(simple_network: Calibration):
    assert len(simple_network._get_dependencies()) == 3


def test_complex_network_size(complex_network: Calibration):
    dag = qbutler.dag._get_graph_containing_calibration(complex_network)

    assert len(dag) == 6


def test_complex_network_deps(complex_network: Calibration):
    assert len(complex_network._get_dependencies()) == 6
