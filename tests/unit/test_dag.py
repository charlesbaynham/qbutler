from weakref import ref

from qbutler import dag
from qbutler.dag import _get_graph
from qbutler.dag import _get_graph_containing_calibration
from qbutler.dag import add_to_dependency_map
from qbutler.dag import get_dependencies
from qbutler.dag import get_union_dependencies


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

    G_first = _get_graph_containing_calibration(b1)
    G_second = _get_graph_containing_calibration(a2)

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


def test_get_dependencies_diamond(plot_graph):
    # Issue #31: a -> b -> c plus the shortcut a -> c. The BFS-distance sort
    # put b and c at equal distance from a and (deterministically, from BFS
    # insertion order) walked b before its own dependency c. Only [c, b, a]
    # is a valid walk.
    a = DummyCal("a")
    b = DummyCal("b")
    c = DummyCal("c")

    add_to_dependency_map(a, b)
    add_to_dependency_map(b, c)
    add_to_dependency_map(a, c)

    plot_graph()

    assert get_dependencies(a) == [c, b, a]
    assert get_dependencies(a, furthest_first=False) == [a, b, c]
    assert get_dependencies(b) == [c, b]


def test_get_dependencies_deep_shortcut(plot_graph):
    # A longer shortcut: a -> b -> c -> d plus a -> d. BFS distance made d
    # (depth 4) look *closer* than c (depth 3), ordering d after c.
    a = DummyCal("a")
    b = DummyCal("b")
    c = DummyCal("c")
    d = DummyCal("d")

    add_to_dependency_map(a, b)
    add_to_dependency_map(b, c)
    add_to_dependency_map(c, d)
    add_to_dependency_map(a, d)

    plot_graph()

    assert get_dependencies(a) == [d, c, b, a]


def test_get_union_dependencies_shared_chain(plot_graph):
    # Two leaves sharing one chain (the EnsureClockPiTimes shape): the shared
    # chain must come before both leaves, each exactly once.
    up = DummyCal("up")
    down = DummyCal("down")
    delivery = DummyCal("delivery")
    coarse = DummyCal("coarse")

    add_to_dependency_map(up, delivery)
    add_to_dependency_map(down, delivery)
    add_to_dependency_map(delivery, coarse)

    plot_graph()

    order = get_union_dependencies([up, down])
    assert len(order) == 4
    assert order.index(coarse) < order.index(delivery)
    assert order.index(delivery) < order.index(up)
    assert order.index(delivery) < order.index(down)

    # Single target reduces to get_dependencies
    assert get_union_dependencies([up]) == get_dependencies(up)


def test_get_type_from_cache():
    c = DummyCal("hello")
    dag.add_to_dependency_map(c, None)

    assert dag.get_calibrations_of_type(DummyCal)[0] == c
    assert dag.get_calibrations_of_type(DummyCal)[0].id == "hello"
    assert len(dag.get_calibrations_of_type(DummyCal)) == 1


def test_get_calibrataion_from_cache(fragment_factory):
    from qbutler.calibration import Calibration

    class RealCal(Calibration):
        def build_calibration(self):
            pass

    c = fragment_factory(RealCal)
    dag.add_to_dependency_map(c, None)

    assert dag.get_calibrations_of_type(RealCal)[0] == c
    assert len(dag.get_calibrations_of_type(RealCal)) == 1


def test_get_calibrataions_from_cache(fragment_factory):
    from qbutler.calibration import Calibration

    class RealCal(Calibration):
        def build_calibration(self):
            pass

    c1 = fragment_factory(RealCal)
    dag.add_to_dependency_map(c1, None)

    c2 = fragment_factory(RealCal)
    dag.add_to_dependency_map(c2, None)

    cals = dag.get_calibrations_of_type(RealCal)

    assert len(cals) == 2
    assert c1 in cals
    assert c2 in cals


def _count_sweeps(monkeypatch):
    """Count whole-heap sweeps, still performing them."""
    sweeps = []
    real_collect = dag.gc.collect

    def counting_collect(*args, **kwargs):
        sweeps.append(1)
        return real_collect(*args, **kwargs)

    monkeypatch.setattr(dag.gc, "collect", counting_collect)
    return sweeps


def test_building_sweeps_the_heap_once_per_tree(monkeypatch):
    class _Cal(DummyCal):
        pass

    sweeps = _count_sweeps(monkeypatch)

    # Uninstrumented callers sweep on every lookup, exactly as before
    dag.get_calibrations_of_type(_Cal)
    dag.get_calibrations_of_type(_Cal)
    assert len(sweeps) == 2

    # Inside one build scope, the first lookup sweeps and the rest ride on it
    sweeps.clear()
    with dag.building():
        for _ in range(5):
            dag.get_calibrations_of_type(_Cal)
    assert len(sweeps) == 1

    # The next tree gets its own sweep: the previous one's corpses are new
    sweeps.clear()
    with dag.building():
        dag.get_calibrations_of_type(_Cal)
    assert len(sweeps) == 1


def test_nested_building_scopes_sweep_once(monkeypatch):
    class _Cal(DummyCal):
        pass

    sweeps = _count_sweeps(monkeypatch)

    with dag.building():
        dag.get_calibrations_of_type(_Cal)
        with dag.building():
            dag.get_calibrations_of_type(_Cal)
        dag.get_calibrations_of_type(_Cal)

    assert len(sweeps) == 1
    assert dag._build_depth == 0


def test_a_previous_builds_corpse_is_purged_when_the_next_starts():
    """The load-bearing property: the sweep must survive the throttle.

    A Calibration in a reference cycle outlives its build until the collector
    runs. If the next build could still see it, add_dependency would alias the
    corpse instead of building the subtree, and its parameters would silently
    vanish from the experiment's arginfo.
    """

    class _Cal(DummyCal):
        pass

    a = _Cal("a")
    a.cycle = a  # only the collector can free this
    dag.add_to_dependency_map(a, None)
    del a

    with dag.building():
        assert dag.get_calibrations_of_type(_Cal) == []


def test_live_calibrations_are_still_found_within_a_build():
    """Skipping the later sweeps must not break intra-build dedup."""

    class _Cal(DummyCal):
        pass

    with dag.building():
        dag.get_calibrations_of_type(_Cal)  # the build's one sweep

        a = _Cal("a")
        dag.add_to_dependency_map(a, None)

        assert dag.get_calibrations_of_type(_Cal) == [a]
