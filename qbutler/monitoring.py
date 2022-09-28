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

- [x]   `run_once` samples the Calibration once. It returns a value and a conclusion to two `ResultsChannel`s
- [x]   `check_own_state` runs `run_once` once and reads the results
- [x]   `calibrate` would (by default) run `run_once` in a ndscan style scan, then draw some conclusion from it
- [x]   Monitors differ from normal Calibrations only in that they have no optimizable parameters (and therefore can't be calibrated)

MonitorMaster
-------------

- [x]   Contains database logging code

- [x]   Calls check_state at an appropriate set of times, then logs the results

- [x]   Uses async to do these things. I.e. the Monitors are not dependencies of some master Calibration, they're just a collection of independent Calibrations.

- [ ]   Despite using async, launch each monitor in its own thread so that users can ignore the complexities of asyncronous coding
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from typing import List
from typing import Type

from artiq.master.scheduler import Scheduler
from ndscan.experiment import ExpFragment
from ndscan.experiment.entry_point import make_fragment_scan_exp

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult

logger = logging.getLogger(__name__)


def make_monitor_controller(
    name: str,
    monitors: List[Type[Calibration]],
    data_logger: Callable[[Calibration, str, CalibrationResult, float], None] = None,
    devices: List[str] = [],
):
    """
    Make an EnvExperiment that manages a list of Calibrations and regularly logs
    their output

    This function returns an EnvExperiment class (or, actually, a
    FragmentScanExperiment) which will launch and manage a list of Calibrations,
    logging their status to a user-defined destination.

    A valid Calibration for this controller is known as a Monitor. It is simply
    a Calibration whose :meth:`run_once` method outputs a float to the "data"
    :class:`ResultsChannel`. These :class:`Calibration` objects will not be
    fixed, only monitored.

    Monitors must have a timeout set that is not zero, otherwise an error will
    be thrown (see :meth:`.set_timeout`).

    TODO: Have the MonitorController optionally try to repair broken
    Calibrations

    **Example usage**::

        class SimpleMonitor(Calibration):
            def build_calibration(self):
                self.set_timeout(1.0)  # 1s timeout

            def run_once(self):
                self.status.push(CalibrationResult.OK)
                self.data.push(123.0)

        MyMonitorMaster = make_monitor_controller(
            "MyMonitorMaster", monitors=[SimpleMonitor]
        )

    Parameters:

        name (str):
            Name of the monitor master to be created

        monitors (list):
            A list of Monitor classes which will be constructed and monitored.

        data_logger (callable):
            A callback which should log the results of the checks somehow (e.g.
            to a database). The default logger just prints them.

        devices (list):
            A list of devices to request from `device_db` in the MonitorMaster.
    """

    class MonitorController(ExpFragment):
        def build_fragment(self):
            self.setattr_device("scheduler")
            self.scheduler: Scheduler

            self._monitors: List[Calibration] = []
            self.monitor_tasks: List[asyncio.Task] = []
            self._stop_now = False

            for monitor_type in monitors:
                self.setattr_calibration(monitor_type)

                monitor = getattr(self, monitor_type.__name__)
                self._monitors.append(monitor)

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
            for task in self.monitor_tasks + [monitor_task]:
                task.cancel()

        async def monitor_monitors(self):
            """
            Monitor the monitors,
            """
            while True:

                for monitor_task in self.monitor_tasks:
                    logger.debug(
                        "Checking monitor's task for monitor_task %s", monitor_task
                    )
                    if monitor_task.done():
                        if monitor_task.exception():
                            try:
                                monitor_task.result()
                            except Exception:
                                logger.error(
                                    "Monitor %s failed with exception",
                                    exc_info=True,
                                )

                        self.monitor_tasks.remove(monitor_task)

                if not self.monitor_tasks:
                    logger.error("All monitor tasks have ended")
                    self._stop_now = True
                    return

                await asyncio.sleep(0.5)

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
            for monitor in self._monitors:
                logger.debug("Launching monitor %s", monitor)
                self.monitor_tasks.append(
                    asyncio.create_task(self.run_monitor(monitor))
                )

        async def run_monitor(self, monitor: Calibration):
            timeout = monitor.get_timeout()

            logger.debug("Monitor %s started with timeout %s", monitor, timeout)

            while True:
                loop = asyncio.get_event_loop()

                logger.debug("Checking state of monitor %s", monitor)
                state, data = await loop.run_in_executor(None, monitor.check_state)

                logger.debug("Monitor %s reported state %s/%s", monitor, state, data)

                self.data_logger(monitor.__class__.__name__, state, data)

                logger.debug("Monitor %s sleeping for %s seconds", monitor, timeout)

                await asyncio.sleep(timeout)

    if data_logger is None:
        data_logger = lambda _, name, state, data: logger.info(
            "Monitor %s - %s - %s", name, state, data
        )

    setattr(MonitorController, "data_logger", data_logger)
    setattr(MonitorController, "__name__", name)

    return make_fragment_scan_exp(MonitorController)
