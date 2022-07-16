import networkx as nx

from qbutler.calibration import Calibration

# Singleton object, used to log add_dependency calls so that a DAG can be
# constructed. This will hold a set of tuples in the form:
#   (cal_object, type(cal_object), type(dependent_cal_object))
_dependency_map = set()


# Cache of networkx graphs containing a given calibration. TODO
_network_cache = {}


def graph_containing_calibration(cal: Calibration):
    """
    Return the graph containing the passed Calibration object's type, describing
    its dependency links with both dependents and dependees.

    If the graph has not already been constructed it will be built from a list
    of all Calibration types that have had their build_calibration() methods
    called.

    Args:
        cal (Calibration): An instance of a Calibration object
    """

    # if hash(cal) in _network_cache:
    #     return _network_cache[hash(cal)]

    G = nx.DiGraph()
    for _, type_cal_object, type_dependent_cal_object in _dependency_map:
        G.add_edge(type_cal_object, type_dependent_cal_object)

    #
