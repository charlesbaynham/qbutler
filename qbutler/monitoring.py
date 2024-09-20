"""
(Ab)use the Calibration framework to create a network of monitors, each of which
regularly checks itself, reports its state to the database and concludes whether
it is "OK" or "BAD".

Requirements
------------

* Each monitor has a configurable timeout
* Each monitor writes to the database (using the ARTIQ device as an interface)
* Each monitor can be configured with arbitary parameters
* All monitors get probed regardless of the state of an individual one

Stretch goals
-------------

- [ ]   A DAG is drawn in an applet which shows the Good / Bad state of each Monitor

Design plan
-----------

- [x]   `check_own_state` samples the Calibration once.
- [x]   `calibrate` would (by default) run `check_own_state` in a ndscan style
        scan, then draw some conclusion from it
- [x]   Monitors differ from normal Calibrations only in that they have
        no optimizable parameters (and therefore can't be calibrated)

MonitorMaster
-------------

- [x]   Contains database logging code

- [x]   Calls check_state at an appropriate set of times, then
        logs the results

- [x]   Uses async to do these things. I.e. the Monitors are not
        dependencies of some master Calibration, they're just a collection of independent Calibrations.

- [x]   Despite using async, launch each monitor in its own thread
        so that users can ignore the complexities of asyncronous coding
"""

import asyncio
import logging
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Type

from artiq.experiment import BooleanValue
from artiq.master.scheduler import Scheduler
from ndscan.experiment import ExpFragment
from ndscan.experiment.entry_point import make_fragment_scan_exp

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult

logger = logging.getLogger(__name__)


def make_monitor_controller(
    name: str,
    monitors: Dict[str, Type[Calibration]],
    data_logger: Callable[[Calibration, str, CalibrationResult, Any], None] = None,
    devices: Iterable[str] = (),
    pipeline: str = "monitors",
):
    """
    Make an EnvExperiment that manages a list of Calibrations and regularly logs
    their output

    This function returns an EnvExperiment class (or, actually, a
    FragmentScanExperiment) which will launch and manage a list of Calibrations,
    logging their status to a user-defined destination.

    A valid Calibration for this controller is known as a Monitor. It is simply
    a Calibration whose :meth:`check_own_state` method returns a status and a
    float. These :class:`Calibration` objects will not be fixed, only monitored.

    Monitors must have a timeout set that is not zero, otherwise an error will
    be thrown (see :meth:`.set_timeout`).

    TODO: Have the MonitorController optionally try to repair broken
    Calibrations

    **Example usage**::

        class SimpleMonitor(Calibration):
            def build_calibration(self):
                self.set_timeout(1.0)  # 1s timeout

            def check_own_state(self):
                return CalibrationResult.OK, 123.0

        MyMonitorMaster = make_monitor_controller(
            "MyMonitorMaster", monitors={"simple": SimpleMonitor}
        )

    Parameters:

        name (str):
            Name of the monitor master to be created

        monitors (dict):
            A dict of names -> Monitor classes which will be constructed and
            monitored.

        data_logger (callable):
            A callback which should log the results of the checks somehow (e.g.
            to a database). The default logger just prints them.

        devices (list):
            A list of devices to request from `device_db` in the MonitorMaster.

        pipeline (str):
            Default pipeline for the monitors to run in
    """

    class MonitorController(ExpFragment):
        def build_fragment(self):
            self.setattr_device("scheduler")
            self.scheduler: Scheduler

            self.set_default_scheduling(pipeline_name=pipeline)

            self._monitors: Dict[str, Calibration] = {}
            self._monitor_tasks: Dict[str, asyncio.Task] = {}
            self._stop_now = False

            for monitor_name, monitor_type in monitors.items():
                enable_key = f"enable_{monitor_name}"
                self.setattr_argument(enable_key, BooleanValue(default=True))

                if getattr(self, enable_key):
                    self.setattr_calibration(monitor_type, name=monitor_name)

                    monitor = getattr(self, monitor_name)
                    self._monitors[monitor_name] = monitor

                    if monitor.get_timeout() == 0:
                        raise ValueError(
                            f"Monitor {monitor} has timeout == 0 - this won't work!"
                        )

            for device_key in devices:
                self.setattr_device(device_key)

        def request_stop(self):
            logger.info("Stop requested")
            self._stop_now = True

        def run_once(self) -> None:
            logger.debug("Launching monitor loop")
            asyncio.run(self.main())

        async def main(self):
            await self.start_monitors()
            monitor_task = asyncio.create_task(self.monitor_monitors())
            await self.wait_for_termination()
            for task in list(self._monitor_tasks.values()) + [monitor_task]:
                task.cancel()

        async def monitor_monitors(self):
            """
            Monitor the monitors,
            """
            while True:
                for monitor_name, monitor_task in self._monitor_tasks.items():
                    logger.debug(
                        "Checking monitor's task for monitor_task %s", monitor_name
                    )
                    if monitor_task.done():
                        if monitor_task.exception():
                            try:
                                monitor_task.result()
                                logger.error(
                                    "Monitor %s exited with no error", monitor_name
                                )
                            except Exception:
                                logger.error(
                                    "Monitor %s failed with exception",
                                    monitor_name,
                                    exc_info=True,
                                )

                        self._monitor_tasks[monitor_name] = asyncio.create_task(
                            self.recover_a_monitor(monitor_name)
                        )

                if not self._monitor_tasks:
                    logger.error("All monitor tasks have ended")
                    self._stop_now = True
                    return

                await asyncio.sleep(0.5)

        async def recover_a_monitor(self, monitor_name):
            """
            A monitor has failed: try to recover it
            """
            monitor: Calibration = self._monitors[monitor_name]

            logger.warning(
                "Monitor %s has failed - waiting %s seconds before recovery",
                monitor_name,
                monitor.get_timeout(),
            )
            await asyncio.sleep(monitor.get_timeout())

            logger.warning("Attempting recovery of monitor %s", monitor_name)

            self._monitor_tasks[monitor_name] = asyncio.create_task(
                self.run_monitor(monitor_name, monitor)
            )

        async def wait_for_termination(self):
            while True:
                logger.debug("Checking for termination request")
                if self.scheduler.check_pause():
                    logger.debug("Stopping at user request")
                    return

                if self._stop_now:
                    logger.debug("Stopping at internal request")
                    return

                await asyncio.sleep(0.5)

        async def start_monitors(self):
            for name, monitor in self._monitors.items():
                logger.debug("Launching monitor %s", name)
                self._monitor_tasks[name] = asyncio.create_task(
                    self.run_monitor(name, monitor)
                )

        async def run_monitor(self, name: str, monitor: Calibration):
            timeout = monitor.get_timeout()

            logger.debug("Monitor %s started with timeout %s", name, timeout)

            while True:
                loop = asyncio.get_event_loop()

                logger.debug("Checking state of monitor %s", name)
                state, data = await loop.run_in_executor(None, monitor.check_state)

                logger.debug("Monitor %s reported state %s/%s", name, state, data)

                self.data_logger(name, state, data)

                logger.debug("Monitor %s sleeping for %s seconds", name, timeout)

                await asyncio.sleep(timeout)

    if data_logger is None:
        data_logger = lambda _, name, state, data: logger.info(
            "Monitor %s - %s - %s", name, state, data
        )

    setattr(MonitorController, "data_logger", data_logger)
    setattr(MonitorController, "__name__", name)

    return make_fragment_scan_exp(MonitorController)
