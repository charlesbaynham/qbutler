import gc
import logging
import weakref
from typing import TYPE_CHECKING
from typing import List
from typing import Type

import networkx as nx

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .calibration import Calibration

# See docstring for add_to_dependency_map
_dependency_map = []

_dag = None
_dag_valid = False


def add_to_dependency_map(cal_object, dependent_cal_object):
    """
    Add to a map (_dependency_map), used to log
    :meth:`Calibration.add_dependency` calls so that a DAG can be constructed.

    This list records the existence and relationships between all Calibration
    objects that have been initialized. It holds a list of tuples, each one
    mapping an object to one of its dependent.Each object also appears listed once
    with a dependency on "None".

    Objects are held here as weakrefs so that they can be removed from the DAG
    when they are garbage-collected. Each weakref contains a callback to set
    _dag_valid = False to trigger a rebuild of the DAG from this list.
    """
    global _dag_valid

    def invalidate_cache(r):
        # logger.debug(f"Invalidating DAG cache due to deletion of {hash(r)}")
        global _dag_valid
        _dag_valid = False

    r = weakref.ref(cal_object, invalidate_cache)
    # Hash the weakref now so that debug calls later definitely know the weakref.ref of
    # the underlying object
    hash(r)

    if dependent_cal_object is None:
        _dependency_map.append((r, None))
    else:
        r_dep = weakref.ref(dependent_cal_object, invalidate_cache)
        # Same as above hash:
        hash(r_dep)
        _dependency_map.append((r, r_dep))

    _dag_valid = False


def get_dependencies(obj, furthest_first=True) -> List:
    """
    Return a list of a Calibration's dependent objects, including the calibration itself
    """
    # Get a dict of lengths of paths to all dependencies
    G = _get_graph_containing_calibration(obj)
    paths = nx.single_source_shortest_path(G, weakref.ref(obj))

    # Convert to a list of tuples of (target, distance) with the furthest ones first
    targets_and_distances = [(t, len(p)) for t, p in paths.items()]
    targets_and_distances = sorted(
        targets_and_distances, key=lambda d: d[1], reverse=furthest_first
    )

    # Dereference the weakrefs and return
    return [t() for t, _ in targets_and_distances]


def get_calibrations_of_type(obj_type: Type["Calibration"]) -> List["Calibration"]:
    """
    If the DAG cache contains Calibrations of the passed type, return the
    instances of it.

    N.B. Do not keep this list for long! If you do, you might keep Calibration
    objects alive beyond their natural lifespan, keeping them in the DAG when
    they should have been garbage-collected.

    Returns:
        List[Calibration]:
            Returns a list of the already-instantiated Calibration instances
            found
    """
    global _dependency_map

    # Clear out any invalid objects from the map
    _filter_dependency_map()

    # Search only the first half of the map, since all Calibrations appear first
    # at some point
    out = set()
    for ref, _ in _dependency_map:
        if type(ref()) == obj_type:
            out.add(ref())

    return list(out)


def _get_graph_containing_calibration(cal: "Calibration"):
    """
    Return the graph containing a reference to the passed Calibration object,
    describing its dependency links with both dependents and dependees.

    If the graph has not already been constructed it will be built from a list
    of all Calibration types that have had their build_calibration() methods
    called.

    Note that the nodes of this graph are WeakRefs to Calibration objects.

    Args:
        cal (Calibration): An instance of a Calibration object
    """

    G = _get_graph()

    try:
        nodes = next(
            filter(lambda s: weakref.ref(cal) in s, nx.weakly_connected_components(G))
        )
    except StopIteration:
        raise KeyError(f"Calibration {cal} not found in DAG")

    return G.subgraph(nodes)


def _filter_dependency_map():
    """
    Clear out any weakrefs from the _dependency_map which have gone bad due to
    object deletion and garbage collection
    """
    global _dag, _dag_valid, _dependency_map

    def both_refs_valid(refs):
        ref_1, ref_2 = refs

        return ref_1() is not None and (  # The first weakref.ref is valid
            ref_2 is None  # The second weakref.ref never existed
            or ref_2() is not None  # The second weakref.ref exists and is valid
        )

    gc.collect()
    filtered_dependency_map = list(filter(both_refs_valid, _dependency_map))

    if len(_dependency_map) != len(filtered_dependency_map):
        logger.debug(
            "Reduced dependency map from %s to %s elements",
            len(_dependency_map),
            len(filtered_dependency_map),
        )
        _dag_valid = False
        _dag = None

    _dependency_map = filtered_dependency_map


def _get_graph():
    """
    Get the current DAG

    Retrieves or builds a DAG showing all relationships between all initialized
    and non-garbage-collected Calibration objects. Note that this DAG is not
    guaranteed to be connected - there might be independent dependency chains or
    Calibrations without dependents.

    The nodes in this graph are weakref references to Calibration objects - this
    protects from accidentally keeping Calibration objects alive when they
    should have been garbage collected, although a better protection is to not
    save references to DAGs and only retrieve them anew from this module.
    """
    global _dag, _dag_valid, _dependency_map

    if _dag_valid:
        logger.debug("DAG cache valid: returning")
        return _dag

    logger.debug("DAG cache invalid: rebuilding")

    # Clear out any invalid objects from the map
    _filter_dependency_map()

    # We then rebuild from the _dependency_map
    _dag = nx.DiGraph()

    for ref_1, ref_2 in _dependency_map:
        _dag.add_node(ref_1)
        if ref_2 is not None:
            _dag.add_node(ref_2)
            _dag.add_edge(ref_1, ref_2)

    _dag_valid = True

    return _dag
