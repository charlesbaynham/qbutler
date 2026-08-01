import logging
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING
from typing import List
from typing import Type

import networkx as nx

from . import ccb
from . import scoping

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .calibration import Calibration

#: Base of the broadcast dataset holding {"nodes": [...], "edges": [[parent,
#: dependency], ...]} describing the currently-instantiated calibration DAG
#: (class names). Suffixed with the running pipeline's name, so simultaneous
#: walks in different pipelines each publish their own graph rather than
#: overwriting each other's (see :mod:`qbutler.scoping`).
DAG_DATASET = "calibrations.dag"

#: Attribute under which a registered object stores its :class:`DagRegistry`.
#: An attribute rather than a module-level map, so the registry lives exactly
#: as long as the calibration tree that owns it and every lookup is local to
#: that tree.
_REGISTRY_ATTR = "_qbutler_dag_registry"


class DagRegistry:
    """The dependency graph of ONE calibration tree.

    Each top-level build gets its own registry (see :func:`building`), which
    records every Calibration built in that tree and the
    :meth:`~qbutler.calibration.Calibration.add_dependency` edges between
    them. References are strong and the registry is reachable only from the
    tree's own objects, so:

    - dedup can never alias an instance from a previous build — a new build's
      lookups simply cannot see the old registry, dead or alive;
    - nothing needs ``gc.collect()`` to be correct. The old design kept one
      process-global list of *weakrefs*, where "does this class already have
      an instance?" meant "is this weakref dead yet?" — a question only a
      whole-heap collection could answer truthfully, because Calibrations die
      in reference cycles. Every ``add_dependency`` paid for one, and the cost
      scaled with the heap (it is what blew the ARTIQ master's 15 s build
      deadline);
    - the registry dies with its tree, by ordinary garbage collection,
      whenever Python gets around to it. Nobody has to care when.
    """

    def __init__(self):
        #: (calibration, dependency-or-None) in registration order. Every
        #: registered object appears at least once as a first element (with
        #: dependency None); add_dependency records the real edges.
        self.edges = []
        self._graph = None
        # Walks may run while monitor threads read the same tree; the graph
        # cache is built lazily, so its construction is the one mutation that
        # can race after build time.
        self._graph_lock = threading.Lock()

    def record(self, cal_object, dependent_cal_object) -> None:
        self.edges.append((cal_object, dependent_cal_object))
        self._graph = None

    def graph(self) -> nx.DiGraph:
        """The tree's dependency graph; nodes are the Calibration objects
        themselves and edges point parent -> dependency."""
        with self._graph_lock:
            if self._graph is None:
                G = nx.DiGraph()
                for cal, dep in self.edges:
                    G.add_node(cal)
                    if dep is not None:
                        G.add_node(dep)
                        G.add_edge(cal, dep)
                self._graph = G
            return self._graph

    def calibrations_of_type(self, obj_type) -> List["Calibration"]:
        """The already-registered instances of exactly ``obj_type`` (no
        subclass matching), oldest first, deduplicated."""
        out = []
        for cal, _ in self.edges:
            if type(cal) is obj_type and cal not in out:
                out.append(cal)
        return out


#: The registry of the build currently in progress, if any. Set by
#: :func:`building`; the outermost scope creates it, nested scopes share it.
_scope_registry = None
_scope_depth = 0


@contextmanager
def building():
    """Scope the construction of one calibration tree onto one registry.

    The outermost ``building()`` creates a fresh :class:`DagRegistry`; every
    registration and dedup lookup inside the scope (however deeply nested —
    each Calibration's own build enters the scope too) lands on it. So "does
    this dependency already exist?" means "was it already built *in this
    tree*", which is the only sense in which sharing an instance is safe: a
    hit from any other tree — a previous build in the same process, or a
    sibling experiment — would alias a fragment wired into someone else's
    fragment path.

    A client whose targets should share dependencies must therefore build
    them all inside one scope; :class:`~qbutler.client.CalibratedExpFragment`
    experiments get this from the shim, which wraps the whole experiment
    build.
    """
    global _scope_registry, _scope_depth

    if _scope_depth == 0:
        _scope_registry = DagRegistry()
    _scope_depth += 1
    try:
        yield _scope_registry
    finally:
        _scope_depth -= 1
        if _scope_depth == 0:
            _scope_registry = None


def _registry_of(obj) -> DagRegistry:
    registry = getattr(obj, _REGISTRY_ATTR, None)
    if registry is None:
        raise KeyError(
            f"{obj!r} is not registered in any calibration DAG (was it built "
            "as a Calibration?)"
        )
    return registry


def _attach(registry: DagRegistry, obj) -> None:
    existing = getattr(obj, _REGISTRY_ATTR, None)
    if existing is None:
        setattr(obj, _REGISTRY_ATTR, registry)
    elif existing is not registry:
        # The one way to alias across trees is to wire an object from another
        # build into this one by hand; that must fail loudly, because a walk
        # of this tree would wander into that one.
        raise RuntimeError(
            f"{obj!r} already belongs to a different calibration tree; a "
            "Calibration instance cannot be shared across builds"
        )


def add_to_dependency_map(cal_object, dependent_cal_object) -> None:
    """
    Record ``cal_object`` (and, if given, its edge to
    ``dependent_cal_object``) in the calibration DAG.

    This records the existence and relationships of every Calibration built
    in the current tree, from which :func:`get_dependencies` orders walks.
    Each object appears listed once with a dependency of ``None`` (its
    registration, made by ``Calibration.build_fragment``); each
    :meth:`~qbutler.calibration.Calibration.add_dependency` call records a
    real edge.

    The target registry is the active :func:`building` scope's; outside any
    scope (hand-built graphs in tests), whichever argument is already
    registered donates its registry, and a fresh one is created when neither
    is.
    """
    if _scope_registry is not None:
        registry = _scope_registry
    else:
        registry = getattr(cal_object, _REGISTRY_ATTR, None)
        if registry is None and dependent_cal_object is not None:
            registry = getattr(dependent_cal_object, _REGISTRY_ATTR, None)
        if registry is None:
            registry = DagRegistry()

    _attach(registry, cal_object)
    if dependent_cal_object is not None:
        _attach(registry, dependent_cal_object)

    registry.record(cal_object, dependent_cal_object)


def publish_dag(cal: "Calibration") -> None:
    """Best-effort publish of the DAG structure to this pipeline's DAG dataset.

    Also ensures the calibration-DAG overview applet exists, so the tree is
    visualised as soon as any walk runs. Never raises: dataset plumbing must
    not be able to break a calibration run. ``cal`` is used for its
    ``set_dataset``, to reach the ``ccb`` device, for the pipeline its
    scheduler is running in, and for the tree it belongs to.
    """
    ccb.create_dag_applet(cal)
    try:
        G = _registry_of(cal).graph()
        nodes = sorted({type(node).__name__ for node in G.nodes})
        edges = sorted([type(a).__name__, type(b).__name__] for a, b in G.edges)
        cal.set_dataset(
            scoping.scoped_key(DAG_DATASET, cal),
            {"nodes": nodes, "edges": edges},
            broadcast=True,
            persist=True,
            archive=False,
        )
    except Exception:
        logger.warning("Could not publish calibration DAG", exc_info=True)


def _reachable_nodes(G, obj) -> set:
    """The nodes reachable from ``obj`` along dependency edges (self
    included). Raises KeyError if ``obj`` is not in the graph."""
    if obj not in G:
        raise KeyError(f"Calibration {obj} not found in DAG")
    return nx.descendants(G, obj) | {obj}


def _topo_order(G, nodes, furthest_first) -> List["Calibration"]:
    """Order ``nodes`` of ``G`` topologically.

    Edges point parent -> dependency, so a topological sort yields parents
    before the things they depend on; ``furthest_first`` reverses it so the
    deepest dependency comes first — a *correct* walk order for any DAG,
    unlike the BFS-distance sort this replaces, which could place a node
    before its own dependency whenever a "shortcut" edge made the dependency's
    shortest path no longer than its dependent's (e.g. a -> b -> c plus
    a -> c).
    """
    order = list(nx.topological_sort(G.subgraph(nodes)))
    if furthest_first:
        order.reverse()
    return order


def get_dependencies(obj, furthest_first=True) -> List["Calibration"]:
    """
    Return a list of a Calibration's dependent objects, including the
    calibration itself, in dependency (topological) order: with
    ``furthest_first`` every node appears after all of its dependencies.
    """
    G = _registry_of(obj).graph()
    return _topo_order(G, _reachable_nodes(G, obj), furthest_first)


def get_union_dependencies(targets, furthest_first=True) -> List:
    """Merge several targets' dependency lists into one walk order.

    Every node in the union of the targets' DAGs appears exactly once, in
    topological order, so a dependency shared by several targets is placed
    ahead of every node that depends on it — walking this order fixes a shared
    node once, before its dependents. With a single target this reduces to
    :func:`get_dependencies`. All targets must belong to the same tree (they
    were built inside one :func:`building` scope).

    Args:
        targets: an iterable of :class:`~qbutler.calibration.Calibration`
            instances (the leaf calibrations a client maintains).

    Returns:
        List: the deduplicated calibration objects, furthest dependency first.
    """
    targets = list(targets)
    registries = {id(_registry_of(t)): _registry_of(t) for t in targets}
    if len(registries) > 1:
        raise RuntimeError(
            "Cannot walk targets from different calibration trees together: "
            f"{[type(t).__name__ for t in targets]} were not built in one "
            "building() scope"
        )
    G = next(iter(registries.values())).graph()
    nodes = set()
    for target in targets:
        nodes |= _reachable_nodes(G, target)
    return _topo_order(G, nodes, furthest_first)


def get_calibrations_of_type(obj_type: Type["Calibration"]) -> List["Calibration"]:
    """
    The instances of ``obj_type`` already built in the tree currently being
    built (the active :func:`building` scope). This is the
    :meth:`~qbutler.calibration.Calibration.add_dependency` dedup lookup:
    sharing is per-tree by construction, so an instance from any other build
    can never be found here.

    Raises:
        RuntimeError: if called outside a :func:`building` scope — there is
            then no tree for the question to be about.
    """
    if _scope_registry is None:
        raise RuntimeError(
            "get_calibrations_of_type() is only meaningful during a tree "
            "build (inside a dag.building() scope)"
        )
    return _scope_registry.calibrations_of_type(obj_type)


def get_graph(obj) -> nx.DiGraph:
    """The dependency graph of the tree ``obj`` belongs to. Nodes are the
    Calibration objects; edges point parent -> dependency."""
    return _registry_of(obj).graph()


def _get_graph_containing_calibration(cal: "Calibration"):
    """
    Return the graph containing a reference to the passed Calibration object,
    describing its dependency links with both dependents and dependees.

    Args:
        cal (Calibration): An instance of a Calibration object
    """
    G = _registry_of(cal).graph()

    try:
        nodes = next(filter(lambda s: cal in s, nx.weakly_connected_components(G)))
    except StopIteration:
        raise KeyError(f"Calibration {cal} not found in DAG")

    return G.subgraph(nodes)
