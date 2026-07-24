#!/usr/bin/env python3
"""Live view of a single qbutler calibration's optimizer as it walks.

Renders the per-point trace published to ``calibrations.optimizer`` (see
:meth:`qbutler.calibration.Calibration._publish_optimizer_point`): every point
the calibration's optimizer measures is appended live, so you can watch the
scan happen instead of only seeing the settled number.

Each applet is scoped to one calibration class via ``--calibration``: qbutler
launches one of these per class as its optimizer first runs (see
:func:`qbutler.ccb.create_optimizer_applet`), so a DAG fix shows a separate,
independently-namespaced plot for every node it touches. Points are coloured by
check result — green OK, red bad — and the most recent point is ringed. For a
single swept parameter the x-axis is the parameter value; for multi-parameter
sweeps it is the point index.

Invoke with:
    ${python} -m qbutler.applets.optimizer_applet \\
        --calibration MyCalibration calibrations.optimizer
"""

import pyqtgraph as pg
from artiq.applets.simple import TitleApplet
from PyQt5.QtCore import QTimer

OK_COLOUR = (60, 180, 75)
BAD_COLOUR = (220, 50, 47)
LATEST_RING = (255, 200, 0)


class QbutlerOptimizerWidget(pg.PlotWidget):
    def __init__(self, args, req):
        super().__init__()
        self.args = args
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setLabel("left", "metric (data)")
        self._latest = None

        # Points are event-driven, but a light timer keeps the view honest
        # if broadcasts are coalesced.
        self.timer = QTimer()
        self.timer.timeout.connect(self._render)
        self.timer.start(1000)

    def data_changed(self, value, metadata, persist, mods, title):
        self._latest = (value.get(self.args.trace) or {}, title)
        self._render()

    def _render(self):
        if self._latest is None:
            return
        table, title = self._latest
        if not isinstance(table, dict):
            return

        name = self.args.calibration
        entry = table.get(name)
        self.clear()
        if not isinstance(entry, dict) or not entry.get("points"):
            self.setTitle(title or f"{name}: waiting for optimizer")
            return

        points = entry["points"]
        data = entry["data"]
        status = entry["status"]
        names = entry.get("param_names") or []

        one_param = len(names) == 1
        xs, ys, brushes = [], [], []
        for i, (point, d, s) in enumerate(zip(points, data, status)):
            if d is None:
                continue
            xs.append(point[0] if one_param else i)
            ys.append(d)
            brushes.append(pg.mkBrush(*(OK_COLOUR if s == 0 else BAD_COLOUR)))

        if xs:
            # Trajectory in measurement order (points are appended in order).
            self.plot(xs, ys, pen=pg.mkPen(150, 150, 150, width=1))
            self.addItem(
                pg.ScatterPlotItem(xs, ys, size=12, brush=brushes, pen=pg.mkPen(None))
            )
            # Ring the most recent point.
            self.addItem(
                pg.ScatterPlotItem(
                    [xs[-1]],
                    [ys[-1]],
                    size=20,
                    brush=pg.mkBrush(None),
                    pen=pg.mkPen(*LATEST_RING, width=3),
                )
            )

        self.setLabel("bottom", names[0] if one_param else "point index")
        n = len(points)
        base = title or name
        self.setTitle(f"{base} — {name}: {n} point{'s' if n != 1 else ''}")


def main():
    applet = TitleApplet(QbutlerOptimizerWidget)
    applet.argparser.add_argument(
        "--calibration",
        required=True,
        help="Class name of the calibration whose optimizer trace to show",
    )
    applet.add_dataset("trace", "calibrations.optimizer trace dataset")
    applet.run()


if __name__ == "__main__":
    main()
