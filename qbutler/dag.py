from typing import TYPE_CHECKING

import networkx as nx


if TYPE_CHECKING:
    from .calibration import Calibration


_dependency_map = set()


# Cache of networkx graphs containing a given calibration. TODO
_network_cache = {}


def add_to_dependency_map(cal_object, dependent_cal_object):
    """
    Add to a singleton map (_dependency_map), used to log
    :meth:`Calibration.add_dependency` calls so that a DAG can be constructed.

    Also logs build_calibration calls for Calibrations without dependencies, in
    the form (cal_object, None).

    The map will hold a set of tuples in the form:

        (cal_object, dependent_cal_object)
    """
    _dependency_map.add((cal_object, dependent_cal_object))

    # Invalidate cache
    G = None
    if hash(cal_object) in _network_cache:
        G = _network_cache[hash(cal_object)]
    elif dependent_cal_object and hash(dependent_cal_object) in _network_cache:
        G = _network_cache[hash(dependent_cal_object)]

    if G:
        for hashed_cal, network in _network_cache.items():
            if network == G:
                _network_cache.pop(hashed_cal)


def graph_containing_calibration(cal: "Calibration"):
    """
    Return the graph containing the passed Calibration object, describing its
    dependency links with both dependents and dependees.

    If the graph has not already been constructed it will be built from a list
    of all Calibration types that have had their build_calibration() methods
    called.

    Args:
        cal (Calibration): An instance of a Calibration object
    """

    # First, try to look up the network in the cache. This isn't just for
    # performance, it also allows us to find dependees of our own Calibration
    # object as well as dependents.
    if hash(cal) in _network_cache:
        return _network_cache[hash(cal)]

    G = nx.DiGraph()
    for cal_object, dependent_cal_object in _dependency_map:
        if dependent_cal_object is None:
            G.add_node(cal_object)
        else:
            G.add_edge(cal_object, dependent_cal_object)

    for cal_object, dependent_cal_object in _dependency_map:
        # Store a reference to this network that can be looked up by any
        # Calibration object in it
        _network_cache[hash(cal)] = G
        if dependent_cal_object:
            _network_cache[hash(dependent_cal_object)] = G

    return G
