import copy
import inspect
import logging
from typing import Callable
from typing import Type

from artiq.experiment import EnvExperiment
from artiq.experiment import host_only
from artiq.language.environment import ProcessArgumentManager
from artiq.master.worker_db import DatasetManager
from artiq.master.worker_db import DeviceManager
from ndscan.experiment import Fragment
from pytest import fixture
from sipyco.sync_struct import Notifier
from sipyco.sync_struct import process_mod

import qbutler.calibration
from qbutler.calibration import Calibration

logger = logging.getLogger(__name__)


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
def argument_mgr():
    arguments = {}
    return ProcessArgumentManager(arguments)


@fixture
def fragment_factory(
    device_mgr, dataset_mgr, argument_mgr
) -> Callable[[Type["Fragment"]], Fragment]:
    def fac(exp_class):
        frag = exp_class(
            (device_mgr, dataset_mgr, argument_mgr, None), fragment_path=[]
        )
        frag.init_params()
        return frag

    return fac


@fixture
def experiment_factory(
    device_mgr, dataset_mgr, argument_mgr
) -> Callable[[Type["EnvExperiment"]], EnvExperiment]:
    def fac(exp_class):
        return exp_class((device_mgr, dataset_mgr, argument_mgr, None))

    return fac


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


@fixture
def device_mgr():
    class DummyDeviceDB:
        def __init__(self):
            self.data = Notifier({})

        def scan(self):
            pass

        def get_device_db(self):
            return self.data.raw_view

        def get(self, key, resolve_alias=False):
            desc = self.data.raw_view[key]
            if resolve_alias:
                while isinstance(desc, str):
                    desc = self.data.raw_view[desc]
            return desc

    class DummyScheduler:
        def __init__(self):
            self.rid = 0
            self.pipeline_name = "main"
            self.priority = 0
            self.expid = None

            self._next_rid = 1

        def submit(
            self,
            pipeline_name=None,
            expid=None,
            priority=None,
            due_date=None,
            flush=False,
        ):
            rid = self._next_rid
            self._next_rid += 1
            logger.info("Submitting: %s, RID=%s", expid, rid)
            return rid

        def delete(self, rid):
            logger.info("Deleting RID %s", rid)

        def request_termination(self, rid):
            logger.info("Requesting termination of RID %s", rid)

        def get_status(self):
            return dict()

        def check_pause(self, rid=None):
            return False

        @host_only
        def pause(self):
            pass

    class DummyCCB:
        def issue(self, service, *args, **kwargs):
            logger.info(
                "CCB for service '%s' (args %s, kwargs %s)", service, args, kwargs
            )

    return DeviceManager(
        DummyDeviceDB(),
        virtual_devices={"scheduler": DummyScheduler(), "ccb": DummyCCB()},
    )


@fixture(scope="session", autouse=True)
def patch_artiq_install_hook():
    from artiq.compiler import import_cache

    import_cache.install_hook()

    setattr(import_cache, "install_hook", lambda: None)


@fixture
def build_and_run_experiment(device_mgr, dataset_mgr):
    from artiq.frontend.artiq_run import _build_experiment

    def build_and_run(experiment_class):
        file = inspect.getfile(experiment_class)
        class_name = experiment_class.__name__

        class Args:
            pass

        args = Args()
        args.arguments = []
        args.file = file
        args.class_name = class_name

        exp_inst = _build_experiment(device_mgr, dataset_mgr, args)
        exp_inst.prepare()
        exp_inst.run()
        exp_inst.analyze()

    return build_and_run
