"""Layout logic of the DAG overview applet.

The applet must stay readable with multiple nodes (issue #40): nodes are
spaced by the rendered size of their labels so nothing overlaps, layers are
ordered to avoid needless edge crossings, and the graph is drawn at natural
size rather than stretched to fill the widget.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from qbutler.applets.dag_applet import H_GAP
from qbutler.applets.dag_applet import _build_layers
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


def test_isolated_nodes_wrap_into_a_grid():
    # A monitor pipeline: dozens of calibrations with no edges at all must
    # wrap into several rows, not one endless line.
    nodes = [f"Monitor{i:02d}" for i in range(24)]
    widths = {n: 150 for n in nodes}
    layers = _build_layers(nodes, [], widths, H_GAP, row_h=130, aspect=1107 / 327)
    assert len(layers) > 1
    assert sorted(n for layer in layers for n in layer) == nodes

    # And every row still keeps the no-overlap spacing guarantee.
    xs = _x_positions(layers, [], widths, H_GAP)
    for layer in layers:
        for left, right in zip(layer, layer[1:]):
            clearance = (xs[right] - xs[left]) - (widths[left] + widths[right]) / 2
            assert clearance >= H_GAP - 1e-9


def test_grid_shape_follows_widget_aspect():
    nodes = [f"m{i}" for i in range(30)]
    widths = {n: 120 for n in nodes}
    wide = _build_layers(nodes, [], widths, H_GAP, row_h=130, aspect=4.0)
    tall = _build_layers(nodes, [], widths, H_GAP, row_h=130, aspect=0.5)
    assert len(wide) < len(tall)
    assert max(len(row) for row in wide) > max(len(row) for row in tall)


def test_isolated_nodes_grid_sits_below_the_dag():
    nodes = ["app", "dep", "lonely_a", "lonely_b", "lonely_c"]
    edges = [("app", "dep")]
    widths = {n: 100 for n in nodes}
    layers = _build_layers(nodes, edges, widths, H_GAP, row_h=130, aspect=1.0)
    assert layers[0] == ["app"]
    assert layers[1] == ["dep"]
    grid = [n for layer in layers[2:] for n in layer]
    assert sorted(grid) == ["lonely_a", "lonely_b", "lonely_c"]


def test_fully_connected_graph_is_not_wrapped():
    nodes = ["app", "cal", "laser"]
    edges = [("app", "cal"), ("cal", "laser")]
    widths = {n: 100 for n in nodes}
    layers = _build_layers(nodes, edges, widths, H_GAP, row_h=130, aspect=3.0)
    assert layers == [["app"], ["cal"], ["laser"]]


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


RENDER_SCRIPT = """
import argparse

from PyQt5 import QtWidgets

from qbutler.applets.dag_applet import QbutlerDAGWidget

app = QtWidgets.QApplication([])
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
assert not widget.grab().isNull()

# The empty state must render too.
widget.data_changed({"dag": None, "status": {}}, {}, {}, [], None)
assert not widget.grab().isNull()
print("rendered")
"""


def test_widget_renders_offscreen():
    # A subprocess, because Qt turns an unusable platform into a qFatal
    # abort that would take the whole pytest run down with it.
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parents[2]), env.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-c", RENDER_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 and "platform plugin" in result.stderr:
        pytest.skip(f"Qt offscreen platform unavailable: {result.stderr.strip()}")
    assert result.returncode == 0, result.stderr
    assert "rendered" in result.stdout
