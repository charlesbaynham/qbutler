"""Layout logic of the DAG overview applet.

The applet must stay readable with multiple nodes (issue #40): nodes are
spaced by the rendered size of their labels so nothing overlaps, layers are
ordered to avoid needless edge crossings, and the graph is drawn at natural
size rather than stretched to fill the widget.
"""

import argparse
import os

import pytest

from qbutler.applets.dag_applet import H_GAP
from qbutler.applets.dag_applet import _layer_by_depth
from qbutler.applets.dag_applet import _node_label
from qbutler.applets.dag_applet import _node_state
from qbutler.applets.dag_applet import _ordered_layers
from qbutler.applets.dag_applet import _x_positions


def _layout(nodes, edges, widths=None, gap=H_GAP):
    widths = widths or {n: 38 for n in nodes}
    depth = _layer_by_depth(nodes, edges)
    layers = _ordered_layers(nodes, edges, depth)
    xs = _x_positions(layers, edges, widths, gap)
    return layers, xs


def test_dependents_are_layered_above_dependencies():
    nodes = ["app", "cal", "laser"]
    edges = [("app", "cal"), ("cal", "laser")]
    layers, _ = _layout(nodes, edges)
    assert layers == [["app"], ["cal"], ["laser"]]


def test_ordering_avoids_edge_crossings():
    # Alphabetical order within layers would cross both edges; barycenter
    # ordering must untangle them.
    nodes = ["p_a", "p_b", "d_a", "d_b"]
    edges = [("p_a", "d_b"), ("p_b", "d_a")]
    layers, xs = _layout(nodes, edges)
    assert len(layers) == 2
    crossings = [
        ((xs[u1] - xs[u2]) * (xs[v1] - xs[v2])) < 0
        for u1, v1 in edges
        for u2, v2 in edges
        if (u1, v1) < (u2, v2)
    ]
    assert not any(crossings)


def test_wide_labels_never_overlap():
    # One layer of independent nodes with very different label widths: every
    # adjacent pair must clear both half-widths plus the gap.
    nodes = [f"n{i}" for i in range(6)]
    widths = {"n0": 40, "n1": 260, "n2": 38, "n3": 120, "n4": 90, "n5": 300}
    (layer,), xs = _layout(nodes, [], widths=widths)
    for left, right in zip(layer, layer[1:]):
        clearance = (xs[right] - xs[left]) - (widths[left] + widths[right]) / 2
        assert clearance >= H_GAP - 1e-9


def test_spacing_survives_alignment_nudges():
    # Layer-alignment shifts are rigid, so a multi-layer graph keeps the
    # no-overlap guarantee in every layer.
    nodes = ["top", "mid_a", "mid_b", "deep"]
    edges = [("top", "mid_a"), ("top", "mid_b"), ("mid_a", "deep"), ("mid_b", "deep")]
    widths = {"top": 200, "mid_a": 150, "mid_b": 40, "deep": 90}
    layers, xs = _layout(nodes, edges, widths=widths)
    for layer in layers:
        for left, right in zip(layer, layer[1:]):
            clearance = (xs[right] - xs[left]) - (widths[left] + widths[right]) / 2
            assert clearance >= H_GAP - 1e-9


def test_parent_is_centred_over_its_children():
    nodes = ["parent", "child_a", "child_b"]
    edges = [("parent", "child_a"), ("parent", "child_b")]
    _, xs = _layout(nodes, edges)
    assert xs["parent"] == pytest.approx((xs["child_a"] + xs["child_b"]) / 2)


def test_layout_is_deterministic():
    nodes = ["a", "b", "c", "d", "e"]
    edges = [("a", "c"), ("b", "c"), ("b", "d"), ("c", "e")]
    assert _layout(nodes, edges) == _layout(nodes, edges)


def test_node_state():
    now = 1000.0
    assert _node_state(None, now) == ("unknown", "")
    assert _node_state({"last_check": None}, now) == ("unknown", "")
    ok = {"status": 0, "last_check": now - 5, "timeout": 60}
    assert _node_state(ok, now) == ("ok", "5 s ago")
    expired = {"status": 0, "last_check": now - 90, "timeout": 60}
    assert _node_state(expired, now) == ("expired", "90 s ago")
    bad = {"status": 4, "last_check": now - 5, "timeout": 60}
    assert _node_state(bad, now) == ("bad", "5 s ago")


def test_node_label_handles_non_numeric_data():
    entry = {"status": 0, "last_check": 995.0, "timeout": 60, "data": [1, 2]}
    lines, state = _node_label("MyCal", entry, 1000.0)
    assert state == "ok"
    assert lines == ["MyCal", "OK", "[1, 2]", "5 s ago"]


def test_widget_renders_offscreen():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    from qbutler.applets.dag_applet import QbutlerDAGWidget

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None

    widget = QbutlerDAGWidget(argparse.Namespace(dag="dag", status="status"), None)
    widget.resize(1107, 327)
    widget.data_changed(
        {
            "dag": {
                "nodes": ["A", "B", "C", "D"],
                "edges": [["A", "B"], ["A", "C"], ["B", "D"], ["C", "D"]],
            },
            "status": {"A": {"status": 0, "last_check": 0, "timeout": 60}},
        },
        {},
        {},
        [],
        "Calibration DAG",
    )
    pixmap = widget.grab()
    assert not pixmap.isNull()

    # The empty state must render too.
    widget.data_changed({"dag": None, "status": {}}, {}, {}, [], None)
    assert not widget.grab().isNull()
