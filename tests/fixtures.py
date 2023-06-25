import copy
import inspect
import logging
import random as rand
import textwrap
from pathlib import Path
from typing import Callable
from typing import Type
from unittest.mock import Mock

import numpy
from artiq.experiment import EnvExperiment
from artiq.experiment import host_only
from artiq.language.environment import ProcessArgumentManager
from artiq.master.worker_db import DatasetManager
from artiq.master.worker_db import DeviceManager
from ndscan.experiment import Fragment
from pytest import fixture
from sipyco.sync_struct import Notifier
from sipyco.sync_struct import process_mod


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
def mock_db_writer():
    return Mock()


@fixture
def device_mgr(mock_db_writer):
    mock_device_db = {
        "core": {
            "type": "local",
            "module": "artiq.coredevice.core",
            "class": "Core",
            "arguments": {"host": None, "ref_period": 1e-9},
        }
    }

    class DummyDeviceDB:
        def __init__(self, device_db):
            self.data = Notifier(device_db)

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

    class DeviceManagerWithOverride(DeviceManager):
        # Add an "override" method to DeviceManger which lets us replace a
        # device with our own object
        def override_device(self, key, obj):
            self.close_devices()
            self.virtual_devices[key] = obj

    return DeviceManagerWithOverride(
        DummyDeviceDB(mock_device_db),
        virtual_devices={
            "scheduler": DummyScheduler(),
            "ccb": DummyCCB(),
            "mock_db_writer": mock_db_writer,
        },
    )


@fixture(scope="session", autouse=True)
def patch_artiq_install_hook():
    from artiq.compiler import import_cache

    import_cache.install_hook()

    setattr(import_cache, "install_hook", lambda: None)


@fixture
def build_experiment(device_mgr, dataset_mgr):
    from artiq.frontend.artiq_run import _build_experiment

    def experiment_builder(experiment_class, experiment_file=None):
        class_name = experiment_class.__name__
        if not experiment_file:
            experiment_file = inspect.getfile(experiment_class)

        class Args:
            pass

        args = Args()
        args.arguments = []
        args.file = experiment_file
        args.class_name = class_name

        return _build_experiment(device_mgr, dataset_mgr, args)

    return experiment_builder


@fixture
def build_and_run_experiment(build_experiment):
    def build_and_run(experiment_class, experiment_file=None):
        exp_inst = build_experiment(experiment_class, experiment_file)
        exp_inst.prepare()
        exp_inst.run()
        exp_inst.analyze()

    return build_and_run


@fixture
def free_port():
    import socket
    from contextlib import closing

    def find_free_port():
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.bind(("", 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return s.getsockname()[1]

    return find_free_port()


@fixture
def build_and_run_full_stack(artiq_master):
    import subprocess as sp
    import time

    def run_experiment(class_name, file_name):
        p_artiq_client = sp.run(
            ["artiq_client", "submit", "-c", class_name, file_name],
            stderr=sp.STDOUT,
            stdout=sp.PIPE,
            timeout=1,
        )

        # Wait two seconds then kill the master and read its output
        time.sleep(2)

        artiq_master.kill()
        _, out = artiq_master.communicate(timeout=1)

        out = out.decode()

        if "ERROR" in out:
            print(out)
            raise RuntimeError('"ERROR" detected in artiq_master output')

        return out

    return run_experiment


@fixture
def artiq_master(tmp_path: Path):
    """
    The deluxe version - make a new ARTIQ stack, launch it, submit this
    experiment to artiq_master using artiq_client and record the results
    """

    import subprocess as sp
    import os

    print(tmp_path)

    (tmp_path / "device_db.py").write_text(
        textwrap.dedent(
            """
        device_db = {
            "core": {
                "type": "local",
                "module": "artiq.coredevice.core",
                "class": "Core",
                "arguments": {"host": "1.2.3.4", "ref_period": 1e-09, "target": "rv32g"},
            },
        }
        """
        )
    )
    (tmp_path / "repository").mkdir()

    env = os.environ.copy()
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] += f":{os.getcwd()}"
    else:
        env["PYTHONPATH"] = f"{os.getcwd()}"

    p_artiq_master = sp.Popen(
        ["artiq_master", "-vv"], stderr=sp.PIPE, stdout=sp.PIPE, cwd=tmp_path, env=env
    )

    yield p_artiq_master

    p_artiq_master.kill()
    _, out = p_artiq_master.communicate()

    print(out)


@fixture(autouse=True)
def random():
    rand.seed(0)
    numpy.random.seed(0)
