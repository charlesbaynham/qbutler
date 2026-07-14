#!/usr/bin/env python3
"""Live view of the qbutler calibration DAG.

Renders the DAG published to the ``calibrations.dag`` dataset, coloured by
the per-calibration state in ``calibrations.status``:

- green: last check OK and within its timeout
- orange: expired (never checked, or past last_check + timeout)
- red: last check returned a BAD flag
- grey: no status recorded

qbutler launches this applet automatically the first time a calibration DAG is
published (see :func:`qbutler.ccb.create_dag_applet`). To run it by hand:

    ${python} -m qbutler.applets.dag_applet calibrations.dag calibrations.status
"""

import time

import pyqtgraph as pg
from artiq.applets.simple import TitleApplet
from PyQt5.QtCore import QTimer

STATE_COLOURS = {
    "ok": (60, 180, 75),
    "bad": (220, 50, 47),
    "expired": (255, 160, 0),
    "unknown": (130, 130, 130),
}


def _layer_by_depth(nodes, edges):
    """Longest-path layering: dependencies end up in deeper layers than
    their dependents. Pure python (no networkx in the dashboard env)."""
    children = {n: [] for n in nodes}
    for parent, dep in edges:
        if parent in children and dep in children:
            children[parent].append(dep)

    depth = {}

    def visit(node, seen):
        if node in depth:
            return depth[node]
        if node in seen:  # cycle guard - not expected in a DAG
            return 0
        seen = seen | {node}
        d = 1 + max((visit(c, seen) for c in children[node]), default=-1)
        depth[node] = d
        return d

    for n in nodes:
        visit(n, frozenset())
    return depth


def _node_state(entry, now):
    if not entry or entry.get("last_check") is None:
        return "unknown", ""
    status = int(entry["status"])
    age = now - float(entry["last_check"])
    age_text = f"{age:.0f} s ago"
    if status != 0:
        return "bad", age_text
    if age > float(entry.get("timeout", 0)):
        return "expired", age_text
    return "ok", age_text


class QbutlerDAGWidget(pg.PlotWidget):
    def __init__(self, args, req):
        super().__init__()
        self.args = args
        self.setAspectLocked(False)
        self.hideAxis("bottom")
        self.hideAxis("left")
        self._latest = None

        # Expiry is a function of wall-clock: re-render every second
        self.timer = QTimer()
        self.timer.timeout.connect(self._render)
        self.timer.start(1000)

    def data_changed(self, value, metadata, persist, mods, title):
        dag = value.get(self.args.dag)
        status = value.get(self.args.status) or {}
        self._latest = (dag, status, title)
        self._render()

    def _render(self):
        if self._latest is None:
            return
        dag, status, title = self._latest
        if not dag or "nodes" not in dag:
            return

        nodes = list(dag["nodes"])
        edges = [tuple(e) for e in dag.get("edges", [])]
        depth = _layer_by_depth(nodes, edges)

        layers = {}
        for n in nodes:
            layers.setdefault(depth[n], []).append(n)

        pos = {}
        for d, layer_nodes in layers.items():
            for i, n in enumerate(sorted(layer_nodes)):
                pos[n] = (i - (len(layer_nodes) - 1) / 2, d)

        now = time.time()

        self.clear()
        for parent, dep in edges:
            if parent in pos and dep in pos:
                x = [pos[parent][0], pos[dep][0]]
                y = [pos[parent][1], pos[dep][1]]
                self.plot(x, y, pen=pg.mkPen(120, 120, 120, width=2))

        for n in nodes:
            entry = status.get(n) if isinstance(status, dict) else None
            state, age_text = _node_state(entry, now)
            colour = STATE_COLOURS[state]

            scatter = pg.ScatterPlotItem(
                [pos[n][0]], [pos[n][1]], size=38, brush=pg.mkBrush(*colour)
            )
            self.addItem(scatter)

            data = entry.get("data") if entry else None
            lines = [n, state.upper()]
            if data is not None:
                lines.append(f"{data:.4g}")
            if age_text:
                lines.append(age_text)
            label = pg.TextItem("\n".join(lines), anchor=(0.5, -0.15))
            label.setPos(pos[n][0], pos[n][1])
            self.addItem(label)

        if title:
            self.setTitle(title)

        all_x = [p[0] for p in pos.values()] or [0]
        all_y = [p[1] for p in pos.values()] or [0]
        self.setXRange(min(all_x) - 1, max(all_x) + 1)
        self.setYRange(min(all_y) - 0.7, max(all_y) + 1.2)


def main():
    applet = TitleApplet(QbutlerDAGWidget)
    applet.add_dataset("dag", "calibrations.dag structure dataset")
    applet.add_dataset("status", "calibrations.status table dataset")
    applet.run()


if __name__ == "__main__":
    main()
