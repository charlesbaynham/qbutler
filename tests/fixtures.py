import copy
from typing import Callable
from typing import Type

from artiq.master.worker_db import DatasetManager
from pytest import fixture
from sipyco.sync_struct import process_mod

import qbutler.calibration
from qbutler.calibration import Calibration


@fixture
def dataset_db():
    class MockDatasetDB:
        def __init__(self):
            self.data = dict()

        def get(self, key):
            return self.data[key][1]

        def update(self, mod):
            # Copy mod before applying to avoid sharing references to objects
            # between this and the DatasetManager, which would lead to mods being
            # applied twice.
            process_mod(self.data, copy.deepcopy(mod))

        def delete(self, key):
            del self.data[key]

    return MockDatasetDB()


@fixture
def dataset_mgr(dataset_db):
    return DatasetManager(dataset_db)


@fixture
def calibration_factory(dataset_mgr) -> Callable[[Type["Calibration"]], Calibration]:

    yield lambda cal_class: cal_class((None, dataset_mgr, None, None), fragment_path=[])

    # Clear cache of initialized calibrations after each test
    qbutler.calibration._initialized_calibrations_cache.clear()


@fixture
def plot_graph(tmp_path):
    def func(name=None):
        import matplotlib.pyplot as plt
        from qbutler.dag import _get_graph
        import networkx as nx

        if name is None:
            name = "graph"

        G = _get_graph()

        G_no_refs = nx.DiGraph([(a(), b()) for a, b in G.edges])

        plt.figure()
        nx.draw_networkx(G_no_refs)
        plt.savefig(tmp_path / (name + ".png"))

    return func
