from time import sleep
from unittest.mock import Mock

import pytest

import example.example_monitor
from example.example_monitor import MyMonitorMaster
from example.example_monitor import RandomMonitor
from example.example_monitor import SimpleMonitor
from qbutler.calibration import CalibrationResult
from qbutler.monitoring import make_monitor_controller


def db_logger(self, name, state, value):
    self.mock_db_writer.write(name, value)


MonitorMasterWithMockDB = make_monitor_controller(
    "MonitorMasterWithMockDB",
    monitors={"simple": SimpleMonitor, "random": RandomMonitor},
    data_logger=db_logger,
    devices=["mock_db_writer"],
)


def test_monitor_can_both_pass_and_fail(fragment_factory):
    c = fragment_factory(RandomMonitor)

    c.init_params()

    states = [c.check_state() for _ in range(20)]
    assert not all([s == CalibrationResult.OK for s in states])
    assert not all([s == CalibrationResult.BAD_DATA for s in states])


def test_monitor_builds(build_experiment):
    build_experiment(MyMonitorMaster, experiment_file=example.example_monitor.__file__)


# If the monitor is set up wrong, this test can run forever. We therefore use
# pytest-timeout to run it in a separate process and kill it if it overruns
@pytest.mark.timeout(5, method="thread")
@pytest.mark.slow
def test_monitor_runs(build_experiment, device_mgr):
    import concurrent.futures

    # Replace the core with a Mock object
    mock_core = Mock()
    device_mgr.override_device("core", mock_core)

    RUN_FOR = 2
    WAIT_TIMEOUT = 4

    exp = build_experiment(
        MyMonitorMaster, experiment_file=example.example_monitor.__file__
    )

    # Run the monitor master - this will run forever unless closed
    def run_master():
        exp.prepare()
        exp.run()

    # Request ending of the master. This method relies on internal knowledge of
    # ndscan and shouldn't be used by the user - they should use the scheduler
    # to request termination in the normal way
    def close_master():
        print(f"Sleeping for {RUN_FOR} seconds")
        sleep(RUN_FOR)
        print("Requesting stop now")
        exp.fragment.request_stop()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        fut_exp = executor.submit(run_master)
        fut_close = executor.submit(close_master)
        futs = [fut_exp, fut_close]

        concurrent.futures.wait(futs, timeout=WAIT_TIMEOUT)

        futures_closed = all(fut.done() for fut in futs)

        if not futures_closed:
            executor.shutdown(wait=False, cancel_futures=True)
            raise RuntimeError("The monitor did not close successfully")

    # Check that the monitor didn't try to access the core
    assert len(mock_core.mock_calls) == 0


# If the monitor is set up wrong, this test can run forever. We therefore use
# pytest-timeout to run it in a separate process and kill it if it overruns
@pytest.mark.timeout(5, method="thread")
@pytest.mark.slow
def test_monitor_logs_to_db(build_experiment, device_mgr, mock_db_writer):
    import concurrent.futures

    # Replace the core with a Mock object
    mock_core = Mock()
    device_mgr.override_device("core", mock_core)

    RUN_FOR = 2
    WAIT_TIMEOUT = 4

    exp = build_experiment(MonitorMasterWithMockDB, experiment_file=__file__)

    # Run the monitor master - this will run forever unless closed
    def run_master():
        exp.prepare()
        exp.run()

    # Request ending of the master. This method relies on internal knowledge of
    # ndscan and shouldn't be used by the user - they should use the scheduler
    # to request termination in the normal way
    def close_master():
        print(f"Sleeping for {RUN_FOR} seconds")
        sleep(RUN_FOR)
        print("Requesting stop now")
        exp.fragment.request_stop()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        fut_exp = executor.submit(run_master)
        fut_close = executor.submit(close_master)
        futs = [fut_exp, fut_close]

        concurrent.futures.wait(futs, timeout=WAIT_TIMEOUT)

        futures_closed = all(fut.done() for fut in futs)

        if not futures_closed:
            executor.shutdown(wait=False, cancel_futures=True)
            raise RuntimeError("The monitor did not close successfully")

    # Check that the monitor didn't try to access the core
    assert len(mock_core.mock_calls) == 0

    # Check that the database was accessed at least once by each monitor
    assert len(mock_db_writer.mock_calls) >= 2
    assert len(mock_db_writer.write.call_args) >= 2

    db_called_names = [d[0][0] for d in mock_db_writer.write.call_args_list]

    assert "simple" in db_called_names
    assert "random" in db_called_names
