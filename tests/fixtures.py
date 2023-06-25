import asyncio
import copy
import inspect
import logging
import os
import random as rand
import subprocess as sp
import textwrap
from pathlib import Path
from typing import Callable
from typing import Type
from unittest.mock import Mock

import numpy
from artiq.coredevice.core import Core
from artiq.experiment import EnvExperiment
from artiq.experiment import host_only
from artiq.language.environment import ProcessArgumentManager
from artiq.master.worker_db import DatasetManager
from artiq.master.worker_db import DeviceManager
from ndscan.experiment import Fragment
from pytest import fixture
from sipyco.sync_struct import Notifier
from sipyco.sync_struct import process_mod

from tests.marker_exp import MarkerExperiment
from tests.wait_for_port import wait_for_port

logger = logging.getLogger(__name__)

ARTIQ_MASTER_CHECK_PORT = 3251


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

    # Build the device manager with our dummy services
    mgr = DeviceManagerWithOverride(
        DummyDeviceDB(mock_device_db),
        virtual_devices={
            "scheduler": DummyScheduler(),
            "ccb": DummyCCB(),
            "mock_db_writer": mock_db_writer,
        },
    )

    # Replace the "run()" method of the mocked core's CommKernel with a Mock
    # object so we can keep track of calls to it
    dummy_core: Core = mgr.get("core")
    dummy_core.comm.run = Mock()

    return mgr


@fixture
def mock_core(device_mgr):
    """Returns a mock object that replaced the core's CommKernel.run() method

    This can be used to keep track of the number of times a kernel has been executed
    """
    return device_mgr.get("core").comm.run


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
def build_and_run_full_stack(tmp_path):
    import subprocess as sp
    import time

    # Start up an asyncio stack to monitor the master with a timeout
    async def async_run_experiment(class_name, file_name, timeout=5.0):
        # Start an artiq_master
        p_artiq_master = await launch_artiq_master(tmp_path)

        try:
            # Submit experiment with artiq_client
            p_artiq_client_exp = sp.run(
                ["artiq_client", "-vv", "submit", "-c", class_name, file_name],
                stderr=sp.STDOUT,
                stdout=sp.PIPE,
                timeout=1,
                check=True,
            )

            logger.info("artiq_client output: %s", p_artiq_client_exp.stdout.decode())

            # Read lines from artiq_master (sequence of chars ending with '\n') asynchronously
            output = []
            end_time = time.time() + timeout
            timed_out = False
            unexpected_close = False

            print("artiq_master output:")

            while True:
                try:
                    line = await asyncio.wait_for(
                        p_artiq_master.stdout.readline(), timeout=end_time - time.time()
                    )
                    line = line.decode().strip()
                    print(line)
                    output.append(line)
                except asyncio.TimeoutError:
                    # Time is up! Kill the master process
                    logger.error("Timeout - killing artiq_master")
                    timed_out = True
                    break

                if not line:
                    logger.error("artiq_master closed unexpectedly")
                    unexpected_close = True
                    break

                if "deletion of RID 0 completed" in line:
                    logger.info("Experiment completed")
                    break

            if any("ERROR" in l for l in output):
                raise RuntimeError('"ERROR" detected in artiq_master output')
            elif timed_out:
                raise TimeoutError("Experiment timed out")
            elif unexpected_close:
                raise RuntimeError("artiq_master closed unexpectedly")

        finally:
            if not unexpected_close:
                p_artiq_master.kill()
                await p_artiq_master.wait()

    def run_experiment(class_name, file_name, timeout=5.0):
        returncode = asyncio.run(async_run_experiment(class_name, file_name, timeout))

        return returncode

    return run_experiment


async def launch_artiq_master(tmp_path: Path) -> sp.Popen:
    """
    The deluxe version - make a new ARTIQ stack, launch it, submit this
    experiment to artiq_master using artiq_client and record the results
    """
    print(tmp_path)

    (tmp_path / "device_db.py").write_text(
        textwrap.dedent(
            """
        device_db = {
            "core": {
                "type": "local",
                "module": "artiq.coredevice.core",
                "class": "Core",
                "arguments": {"host": None, "ref_period": 1e-09, "target": "rv32g"},
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

    p_artiq_master = await asyncio.create_subprocess_exec(
        "artiq_master",
        "-vv",
        stderr=sp.STDOUT,
        stdout=sp.PIPE,
        cwd=tmp_path,
        env=env,
    )

    wait_for_port(ARTIQ_MASTER_CHECK_PORT, timeout=5)

    return p_artiq_master


@fixture(autouse=True)
def random():
    rand.seed(0)
    numpy.random.seed(0)
