"""The DAG registry: one strongly-referenced registry per calibration tree.

Each build gets its own :class:`~qbutler.dag.DagRegistry` (via
``dag.building()``), so "does this dependency already exist?" always means
"was it built in this tree" — an instance from any other build, dead or
alive, is unreachable by construction. That is what lets the registry hold
strong references and never need ``gc.collect()``: the old process-global
weakref map could only answer dedup truthfully after a whole-heap collection
(Calibrations die in reference cycles), and one collection per
add_dependency is what blew the ARTIQ master's 15 s build deadline.
"""

import gc

import pytest

from qbutler import dag
from qbutler.dag import _get_graph_containing_calibration
from qbutler.dag import add_to_dependency_map
from qbutler.dag import get_dependencies
from qbutler.dag import get_graph
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

    G = get_graph(a)

    plot_graph(a)

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

    G = get_graph(a)

    plot_graph(a)

    assert len(G) == 4
    assert G.number_of_edges() == 4


def test_dag_separated(plot_graph):
    # Two disconnected chains built outside any scope adopt registries
    # pairwise, so they end up as two independent trees.
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

    plot_graph(a1)

    G_first = get_graph(a1)
    G_second = get_graph(a2)

    assert len(G_first) == 4
    assert G_first.number_of_edges() == 3
    for node in (a1, b1, c1, d1):
        assert node in G_first
        assert node not in G_second

    assert len(G_second) == 3
    assert G_second.number_of_edges() == 2
    for node in (a2, b2, c2):
        assert node in G_second
        assert node not in G_first

    # Within one scope the same two chains would share a registry instead
    with dag.building():
        x1, y1 = DummyCal("x1"), DummyCal("y1")
        x2, y2 = DummyCal("x2"), DummyCal("y2")
        add_to_dependency_map(x1, y1)
        add_to_dependency_map(x2, y2)
    G = get_graph(x1)
    assert G is get_graph(x2)
    assert len(G) == 4

    G_component = _get_graph_containing_calibration(x1)
    assert len(G_component) == 2
    assert x1 in G_component and y1 in G_component
    assert x2 not in G_component


def test_a_discarded_tree_does_not_disturb_the_next(plot_graph):
    a1 = DummyCal("a")
    b1 = DummyCal("b")

    add_to_dependency_map(a1, b1)
    assert len(get_graph(a1)) == 2

    del b1  # a1 still holds the registry; the tree stays intact
    assert len(get_graph(a1)) == 2

    a2 = DummyCal("1")
    b2 = DummyCal("2")
    add_to_dependency_map(a2, b2)

    G = get_graph(a2)
    assert len(G) == 2
    assert a1 not in G

    plot_graph(a2)


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

    plot_graph(a1)

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

    plot_graph(a)

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

    plot_graph(a)

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

    plot_graph(a)

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

    plot_graph(up)

    order = get_union_dependencies([up, down])
    assert len(order) == 4
    assert order.index(coarse) < order.index(delivery)
    assert order.index(delivery) < order.index(up)
    assert order.index(delivery) < order.index(down)

    # Single target reduces to get_dependencies
    assert get_union_dependencies([up]) == get_dependencies(up)


def test_union_of_targets_from_different_trees_raises():
    a = DummyCal("a")
    b = DummyCal("b")
    add_to_dependency_map(a, None)
    add_to_dependency_map(b, None)  # separate tree: no shared edge, no scope

    with pytest.raises(RuntimeError, match="different calibration trees"):
        get_union_dependencies([a, b])


def test_get_type_from_cache():
    with dag.building():
        c = DummyCal("hello")
        dag.add_to_dependency_map(c, None)

        assert dag.get_calibrations_of_type(DummyCal)[0] == c
        assert dag.get_calibrations_of_type(DummyCal)[0].id == "hello"
        assert len(dag.get_calibrations_of_type(DummyCal)) == 1


def test_get_calibrations_of_type_requires_a_build_scope():
    with pytest.raises(RuntimeError, match="building"):
        dag.get_calibrations_of_type(DummyCal)


def test_get_calibrataion_from_cache(fragment_factory):
    from qbutler.calibration import Calibration

    class RealCal(Calibration):
        def build_calibration(self):
            pass

    with dag.building():
        c = fragment_factory(RealCal)  # registers itself during build

        assert dag.get_calibrations_of_type(RealCal)[0] == c
        assert len(dag.get_calibrations_of_type(RealCal)) == 1


def test_get_calibrataions_from_cache(fragment_factory):
    from qbutler.calibration import Calibration

    class RealCal(Calibration):
        def build_calibration(self):
            pass

    with dag.building():
        c1 = fragment_factory(RealCal)
        c2 = fragment_factory(RealCal)

        cals = dag.get_calibrations_of_type(RealCal)

    assert len(cals) == 2
    assert c1 in cals
    assert c2 in cals


# --- The properties that used to cost a whole-heap gc.collect ---------------


def _make_chain():
    """An Upstream <- Downstream Calibration pair for build-lifecycle tests."""
    from qbutler.calibration import Calibration
    from qbutler.calibration import CalibrationResult

    class Upstream(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)

        def check_own_state(self):
            return CalibrationResult.OK, None

    class Downstream(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Upstream)

        def check_own_state(self):
            return CalibrationResult.OK, None

    return Upstream, Downstream


def test_dedup_needs_no_garbage_collection(fragment_factory, monkeypatch):
    """The headline property: correctness no longer depends on gc.collect.

    The old weakref registry HAD to collect the whole heap before every dedup
    lookup — a previous build's corpse, kept breathing by its reference
    cycles, would otherwise answer its weakref and be aliased into the new
    tree (silently dropping its parameters from the arginfo). Forbid
    collection outright and build twice: each build must still get fresh
    instances.
    """

    def no_collect(*args, **kwargs):
        raise AssertionError("gc.collect must not be load-bearing")

    monkeypatch.setattr(gc, "collect", no_collect)

    Upstream, Downstream = _make_chain()

    first = fragment_factory(Downstream)
    first_up = first.Upstream
    first.cycle = first  # keep the corpse breathing, as real fragments do

    del first
    second = fragment_factory(Downstream)

    assert second.Upstream is not first_up
    assert len(get_dependencies(second)) == 2


def test_a_live_previous_tree_is_never_aliased(fragment_factory):
    """Sharing is per-tree, not per-process: even a fully alive tree from an
    earlier build must not donate its instances to a new one."""
    Upstream, Downstream = _make_chain()

    first = fragment_factory(Downstream)
    second = fragment_factory(Downstream)

    assert first is not second
    assert first.Upstream is not second.Upstream
    # And each tree's walk stays inside that tree
    assert get_dependencies(second) == [second.Upstream, second]


def test_trees_share_dependencies_inside_one_scope(fragment_factory):
    """A client that attaches several targets in one build scope gets one
    shared instance of a common dependency (the multi-target dedup)."""
    Upstream, Downstream = _make_chain()

    with dag.building():
        first = fragment_factory(Downstream)
        second = fragment_factory(Downstream)

    assert first.Upstream is second.Upstream
    order = get_union_dependencies([first, second])
    assert len(order) == 3
    assert order.index(first.Upstream) == 0


def test_wiring_in_an_object_from_another_tree_raises():
    a1 = DummyCal("a1")
    b1 = DummyCal("b1")
    add_to_dependency_map(a1, b1)

    with dag.building():
        a2 = DummyCal("a2")
        add_to_dependency_map(a2, None)
        with pytest.raises(RuntimeError, match="different calibration tree"):
            add_to_dependency_map(a2, b1)


def test_nested_building_scopes_share_one_registry():
    with dag.building() as outer:
        with dag.building() as inner:
            assert inner is outer
        # Still in the outer scope after the inner exits
        c = DummyCal("c")
        add_to_dependency_map(c, None)
        assert dag.get_calibrations_of_type(DummyCal) == [c]
    # Fully unwound: the next scope is a fresh tree
    with dag.building() as fresh:
        assert fresh is not outer


def test_union_of_no_targets_is_empty():
    assert get_union_dependencies([]) == []


def test_building_on_a_second_thread_raises():
    """A concurrent build must fail loudly, not silently share the registry."""
    import threading

    result = {}

    def other_thread():
        try:
            with dag.building():
                result["registry"] = "entered"
        except RuntimeError as e:
            result["error"] = str(e)

    with dag.building():
        t = threading.Thread(target=other_thread)
        t.start()
        t.join()

    assert "error" in result and "another thread" in result["error"]
    assert "registry" not in result
    # And the scope is intact for this thread afterwards
    with dag.building():
        c = DummyCal("after")
        add_to_dependency_map(c, None)
        assert dag.get_calibrations_of_type(DummyCal) == [c]
