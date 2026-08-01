#!/usr/bin/env python3
"""Live view of the qbutler calibration DAG.

Renders the DAG published to the ``calibrations.dag`` dataset, coloured by
the per-calibration state in ``calibrations.status``:

- green: last check OK and within its timeout
- orange: expired (never checked, or past last_check + timeout)
- red: last check returned a BAD flag
- grey: no status recorded

Nodes are layered by dependency depth (dependents above their dependencies),
ordered within each layer to reduce edge crossings, and spaced by the pixel
size of their labels so nothing overlaps. The graph is drawn at its natural
size, centred in the widget — it is scaled down uniformly if it does not fit,
but never stretched to fill.

qbutler launches this applet automatically the first time a calibration DAG is
published (see :func:`qbutler.ccb.create_dag_applet`), one per pipeline, passing
that pipeline's own ``calibrations.dag.<pipeline>`` key. The status table is
shared across pipelines; only nodes present in the given DAG are drawn. To run
it by hand:

    ${python} -m qbutler.applets.dag_applet calibrations.dag.main calibrations.status
"""

import time

from artiq.applets.simple import TitleApplet
from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

STATE_COLOURS = {
    "ok": (60, 180, 75),
    "bad": (220, 50, 47),
    "expired": (255, 160, 0),
    "unknown": (130, 130, 130),
}

BACKGROUND_COLOUR = (0, 0, 0)
TEXT_COLOUR = (220, 220, 220)
EDGE_COLOUR = (120, 120, 120)

#: Node circle diameter, px
NODE_SIZE = 38
#: Minimum horizontal clearance between neighbouring labels, px
H_GAP = 28
#: Vertical clearance between one row's labels and the next row's nodes, px
V_GAP = 30
#: Gap between a node circle and the first line of its label, px
LABEL_OFFSET = 6
#: Padding around the whole drawing, px
MARGIN = 12


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


def _mean(values, default):
    return sum(values) / len(values) if values else default


def _neighbour_map(nodes, edges):
    neighbours = {n: [] for n in nodes}
    for parent, dep in edges:
        if parent in neighbours and dep in neighbours:
            neighbours[parent].append(dep)
            neighbours[dep].append(parent)
    return neighbours


def _ordered_layers(nodes, edges, depth):
    """Group nodes into layers and order each layer to reduce edge crossings.

    Returns the layers top-down as drawn: dependents first, deepest
    dependencies last. Ordering uses barycenter sweeps — each node moves
    towards the average position of its neighbours — and is deterministic
    (ties break alphabetically).
    """
    by_depth = {}
    for n in nodes:
        by_depth.setdefault(depth[n], []).append(n)
    layers = [sorted(by_depth[d]) for d in sorted(by_depth, reverse=True)]

    neighbours = _neighbour_map(nodes, edges)
    index = {n: i for layer in layers for i, n in enumerate(layer)}
    for _ in range(3):
        for layer in layers:
            layer.sort(
                key=lambda n: (_mean([index[m] for m in neighbours[n]], index[n]), n)
            )
            for i, n in enumerate(layer):
                index[n] = i
    return layers


def _x_positions(layers, edges, widths, gap):
    """Assign horizontal centres, in px, to every node in ``layers``.

    Adjacent nodes in a layer always clear each other's ``widths`` by at
    least ``gap``. Whole layers are then nudged towards the mean position of
    their neighbours so edges run as vertically as possible — rigid shifts,
    so the spacing guarantee is preserved.
    """
    xs = {}
    for layer in layers:
        x = 0.0
        prev = None
        for n in layer:
            if prev is not None:
                x += (widths[prev] + widths[n]) / 2 + gap
            xs[n] = x
            prev = n
        mid = (xs[layer[0]] + xs[layer[-1]]) / 2
        for n in layer:
            xs[n] -= mid

    neighbours = {n: ms for n, ms in _neighbour_map(list(xs), edges).items() if ms}
    for _ in range(3):
        for layer in layers:
            shifts = [
                _mean([xs[m] for m in neighbours[n]], xs[n]) - xs[n]
                for n in layer
                if n in neighbours
            ]
            if shifts:
                shift = sum(shifts) / len(shifts)
                for n in layer:
                    xs[n] += shift
    return xs


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


def _node_label(name, entry, now):
    """The lines of text drawn under a node, and its state colour key."""
    state, age_text = _node_state(entry, now)
    lines = [name, state.upper()]
    data = entry.get("data") if entry else None
    if data is not None:
        try:
            lines.append(f"{data:.4g}")
        except (TypeError, ValueError):
            lines.append(str(data))
    if age_text:
        lines.append(age_text)
    return lines, state


class QbutlerDAGWidget(QtWidgets.QWidget):
    def __init__(self, args, req):
        super().__init__()
        self.args = args
        self._latest = None

        # Expiry is a function of wall-clock: re-render every second
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(1000)

    def data_changed(self, value, metadata, persist, mods, title):
        dag = value.get(self.args.dag)
        status = value.get(self.args.status) or {}
        self._latest = (dag, status, title)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.fillRect(self.rect(), QtGui.QColor(*BACKGROUND_COLOUR))
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter):
        dag, status, title = self._latest or (None, {}, None)
        fm = painter.fontMetrics()
        text_pen = QtGui.QPen(QtGui.QColor(*TEXT_COLOUR))

        top = MARGIN
        if title:
            painter.setPen(text_pen)
            painter.drawText(
                QtCore.QRectF(MARGIN, 0, self.width() - 2 * MARGIN, top + fm.height()),
                QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                title,
            )
            top += fm.height() + LABEL_OFFSET

        if not dag or not dag.get("nodes"):
            painter.setPen(text_pen)
            painter.drawText(
                self.rect(), QtCore.Qt.AlignCenter, "Waiting for calibration DAG..."
            )
            return

        nodes = list(dag["nodes"])
        edges = [tuple(e) for e in dag.get("edges", [])]
        now = time.time()

        lines = {}
        colours = {}
        for n in nodes:
            entry = status.get(n) if isinstance(status, dict) else None
            lines[n], state = _node_label(n, entry, now)
            colours[n] = STATE_COLOURS[state]

        # Layout, in px: space nodes by the size their labels actually render
        # at, so neither the circles nor the text can ever overlap.
        depth = _layer_by_depth(nodes, edges)
        layers = _ordered_layers(nodes, edges, depth)
        widths = {
            n: max(NODE_SIZE, max(fm.horizontalAdvance(line) for line in lines[n]))
            for n in nodes
        }
        xs = _x_positions(layers, edges, widths, H_GAP)

        line_h = fm.height()
        ys = {}
        y = 0.0
        for layer in layers:
            for n in layer:
                ys[n] = y + NODE_SIZE / 2
            label_h = max(len(lines[n]) for n in layer) * line_h
            y += NODE_SIZE + LABEL_OFFSET + label_h + V_GAP
        total_h = y - V_GAP

        x0 = min(xs[n] - widths[n] / 2 for n in nodes)
        x1 = max(xs[n] + widths[n] / 2 for n in nodes)
        total_w = x1 - x0

        # Centre the drawing at natural size; scale down uniformly only if it
        # does not fit. Never stretch it to fill the widget.
        avail_w = max(1.0, self.width() - 2 * MARGIN)
        avail_h = max(1.0, self.height() - top - MARGIN)
        scale = min(1.0, avail_w / max(total_w, 1.0), avail_h / max(total_h, 1.0))
        painter.translate(self.width() / 2, top + avail_h / 2)
        painter.scale(scale, scale)
        painter.translate(-(x0 + x1) / 2, -total_h / 2)

        painter.setPen(QtGui.QPen(QtGui.QColor(*EDGE_COLOUR), 2))
        for parent, dep in edges:
            if parent in xs and dep in xs:
                painter.drawLine(
                    QtCore.QPointF(xs[parent], ys[parent]),
                    QtCore.QPointF(xs[dep], ys[dep]),
                )

        painter.setPen(QtCore.Qt.NoPen)
        for n in nodes:
            painter.setBrush(QtGui.QBrush(QtGui.QColor(*colours[n])))
            painter.drawEllipse(
                QtCore.QPointF(xs[n], ys[n]), NODE_SIZE / 2, NODE_SIZE / 2
            )

        painter.setPen(text_pen)
        for n in nodes:
            baseline = ys[n] + NODE_SIZE / 2 + LABEL_OFFSET + fm.ascent()
            for line in lines[n]:
                painter.drawText(
                    QtCore.QPointF(xs[n] - fm.horizontalAdvance(line) / 2, baseline),
                    line,
                )
                baseline += line_h


def main():
    applet = TitleApplet(QbutlerDAGWidget)
    applet.add_dataset("dag", "calibrations.dag structure dataset")
    applet.add_dataset("status", "calibrations.status table dataset")
    applet.run()


if __name__ == "__main__":
    main()
