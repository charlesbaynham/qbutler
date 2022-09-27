from weakref import ref

from qbutler import dag
from qbutler.dag import _get_graph
from qbutler.dag import add_to_dependency_map
from qbutler.dag import get_dependencies
from qbutler.dag import get_graph_containing_calibration


class DummyCal:
    def __init__(self, id):
        self.id = id

    def __repr__(self):
        return self.id


def test_dag_simple(plot_graph):
    a = DummyCal("a")
    b = DummyCal("b")
    c = DummyCal("c")
    d = DummyCal("d")

    add_to_dependency_map(a, b)
    add_to_dependency_map(b, c)
    add_to_dependency_map(c, d)

    G = _get_graph()

    plot_graph()

    assert len(G) == 4


def test_dag_fork(plot_graph):
    a = DummyCal("a")
    b = DummyCal("b")
    c = DummyCal("c")
    d = DummyCal("d")

    add_to_dependency_map(a, b)
    add_to_dependency_map(b, c)
    add_to_dependency_map(c, d)
    add_to_dependency_map(a, d)

    G = _get_graph()

    plot_graph()

    assert len(G) == 4
    assert G.number_of_edges() == 4


def test_dag_separated(plot_graph):
    a1 = DummyCal("a")
    b1 = DummyCal("b")
    c1 = DummyCal("c")
    d1 = DummyCal("d")

    a2 = DummyCal("1")
    b2 = DummyCal("2")
    c2 = DummyCal("3")

    add_to_dependency_map(a1, b1)
    add_to_dependency_map(b1, c1)
    add_to_dependency_map(c1, d1)

    add_to_dependency_map(a2, b2)
    add_to_dependency_map(b2, c2)

    G = _get_graph()

    plot_graph()

    assert len(G) == 7
    assert G.number_of_edges() == 5

    G_first = get_graph_containing_calibration(b1)
    G_second = get_graph_containing_calibration(a2)

    assert len(G_first) == 4
    assert G_first.number_of_edges() == 3
    assert ref(a1) in G_first
    assert ref(b1) in G_first
    assert ref(c1) in G_first
    assert ref(d1) in G_first
    assert ref(a2) not in G_first
    assert ref(b2) not in G_first
    assert ref(c2) not in G_first

    assert len(G_second) == 3
    assert G_second.number_of_edges() == 2
    assert ref(a1) not in G_second
    assert ref(b1) not in G_second
    assert ref(c1) not in G_second
    assert ref(d1) not in G_second
    assert ref(a2) in G_second
    assert ref(b2) in G_second
    assert ref(c2) in G_second


def test_dag_deleted(plot_graph):
    a1 = DummyCal("a")
    b1 = DummyCal("b")
    c1 = DummyCal("c")
    d1 = DummyCal("d")

    add_to_dependency_map(a1, b1)
    add_to_dependency_map(b1, c1)
    add_to_dependency_map(c1, d1)

    G = _get_graph()

    assert len(G) == 4
    assert G.number_of_edges() == 3
    assert ref(a1) in G
    assert ref(b1) in G
    assert ref(c1) in G
    assert ref(d1) in G

    a2 = DummyCal("1")
    b2 = DummyCal("2")
    c2 = DummyCal("3")

    del a1, b1, c1, d1

    add_to_dependency_map(a2, b2)
    add_to_dependency_map(b2, c2)

    G = _get_graph()

    assert len(G) == 3
    assert G.number_of_edges() == 2
    assert ref(a2) in G
    assert ref(b2) in G
    assert ref(c2) in G

    plot_graph()


def test_get_dependencies_simple(plot_graph):
    a1 = DummyCal("a1")
    a2 = DummyCal("a2")
    b = DummyCal("b")
    c = DummyCal("c")
    d = DummyCal("d")

    add_to_dependency_map(a1, b)
    add_to_dependency_map(b, c)
    add_to_dependency_map(c, d)
    add_to_dependency_map(a2, c)

    plot_graph()

    assert get_dependencies(a1) == [d, c, b, a1]
    assert get_dependencies(a2) == [d, c, a2]
    assert get_dependencies(b) == [d, c, b]
    assert get_dependencies(c) == [d, c]
    assert get_dependencies(d) == [d]


def test_get_dependencies_forking(plot_graph):
    a = DummyCal("a")
    b1 = DummyCal("b1")
    b2 = DummyCal("b2")
    c = DummyCal("c")
    d = DummyCal("d")

    add_to_dependency_map(a, b1)
    add_to_dependency_map(a, b2)
    add_to_dependency_map(b1, d)
    add_to_dependency_map(b2, c)
    add_to_dependency_map(c, d)

    plot_graph()

    assert get_dependencies(d) == [d]
    assert get_dependencies(c) == [d, c]
    assert get_dependencies(b1) == [d, b1]
    assert get_dependencies(b2) == [d, c, b2]

    a_deps = get_dependencies(a)
    assert (
        a_deps == [d, c, b2, b1, a]
        or a_deps == [d, b1, c, b2, a]
        or a_deps == [d, c, b1, b2, a]
    )


def test_get_type_from_cache():
    c = DummyCal("hello")
    dag.add_to_dependency_map(c, None)

    assert dag.get_calibration_from_type(DummyCal) == c
    assert dag.get_calibration_from_type(DummyCal).id == "hello"


def test_get_calibrataion_from_cache(fragment_factory):
    from qbutler.calibration import Calibration

    class RealCal(Calibration):
        def build_calibration(self):
            pass

    c = fragment_factory(RealCal)
    dag.add_to_dependency_map(c, None)

    assert dag.get_calibration_from_type(RealCal) == c
