import copy
from types import FunctionType
from typing import Callable
from typing import Type

from artiq.experiment import EnvExperiment
from artiq.master.worker_db import DatasetManager
from pytest import fixture
from sipyco.sync_struct import process_mod

from qbutler.calibration import Calibration
from tests.test_dependencies import CalibrationWithRepeatedBadDependencies


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
    return lambda cal_class: cal_class(
        (None, dataset_mgr, None, None), fragment_path=[]
    )
