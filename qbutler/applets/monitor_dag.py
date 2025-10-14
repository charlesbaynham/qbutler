#!/usr/bin/env python3
"""
ARTIQ Applet for visualizing the qbutler Monitor DAG

This applet displays a directed acyclic graph (DAG) showing the status of all
monitored Calibrations. Each node represents a Monitor/Calibration, colored by
its current status (OK=green, BAD=red, etc.), with edges showing dependencies.

Usage:
    artiq_applet monitor_dag --dataset-prefix "monitors."

The applet expects the MonitorController to publish datasets with keys like:
    monitors.<monitor_name>.status
    monitors.<monitor_name>.data
    monitors.<monitor_name>.timestamp
    monitors.dag_structure  # JSON structure of the DAG
"""

import json
import logging
from typing import Dict
from typing import Optional
from typing import Tuple

import networkx as nx
from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

logger = logging.getLogger(__name__)


# Color scheme for CalibrationResult states
STATUS_COLORS = {
    0: QtGui.QColor(50, 205, 50),  # OK = lime green
    1: QtGui.QColor(255, 215, 0),  # BAD_EXPIRED = gold
    2: QtGui.QColor(255, 140, 0),  # BAD_DEPS = dark orange
    4: QtGui.QColor(220, 20, 60),  # BAD_DATA = crimson
    7: QtGui.QColor(178, 34, 34),  # BAD (combined) = firebrick
    8: QtGui.QColor(138, 43, 226),  # INVALID_DATA = blue violet
}

DEFAULT_COLOR = QtGui.QColor(169, 169, 169)  # Dark gray for unknown


class GraphNode:
    """Represents a node in the visualization"""

    def __init__(self, name: str, x: float, y: float):
        self.name = name
        self.x = x
        self.y = y
        self.status = None
        self.data = None
        self.timestamp = None
        self.radius = 30

    def get_color(self) -> QtGui.QColor:
        """Get the color for this node based on its status"""
        if self.status is None:
            return DEFAULT_COLOR
        return STATUS_COLORS.get(self.status, DEFAULT_COLOR)

    def contains_point(self, x: float, y: float) -> bool:
        """Check if a point is inside this node"""
        dx = x - self.x
        dy = y - self.y
        return (dx * dx + dy * dy) <= (self.radius * self.radius)


class DAGWidget(QtWidgets.QWidget):
    """Widget for rendering the DAG visualization"""

    def __init__(self):
        super().__init__()
        self.nodes: Dict[str, GraphNode] = {}
        self.edges = []
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.selected_node: Optional[str] = None
        self.dragging = False
        self.last_mouse_pos = None

        self.setMinimumSize(600, 400)
        self.setMouseTracking(True)

    def set_graph_structure(self, nodes: Dict[str, Tuple[float, float]], edges):
        """
        Set the structure of the graph

        Args:
            nodes: Dict mapping node names to (x, y) positions
            edges: List of (source, target) tuples
        """
        logger.info(f"Setting graph structure: {len(nodes)} nodes, {len(edges)} edges")
        self.nodes = {name: GraphNode(name, x, y) for name, (x, y) in nodes.items()}
        self.edges = edges
        logger.debug(f"Nodes: {list(nodes.keys())}")
        logger.debug(f"Edges: {edges}")
        self.update()

    def update_node_status(self, name: str, status: int, data, timestamp: float):
        """Update the status of a node"""
        if name in self.nodes:
            node = self.nodes[name]
            old_status = node.status
            node.status = status
            node.data = data
            node.timestamp = timestamp

            if old_status != status:
                logger.info(f"Node '{name}' status changed: {old_status} -> {status}")

            logger.debug(f"Updated node '{name}': status={status}, data={data}")
            self.update()
        else:
            logger.warning(f"Received status for unknown node: {name}")

    def paintEvent(self, event):
        """Render the graph"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        # Fill background
        painter.fillRect(self.rect(), QtGui.QColor(30, 30, 30))

        if not self.nodes:
            # Draw "waiting for data" message
            painter.setPen(QtGui.QColor(200, 200, 200))
            font = painter.font()
            font.setPointSize(16)
            painter.setFont(font)
            painter.drawText(
                self.rect(), QtCore.Qt.AlignCenter, "Waiting for monitor data..."
            )
            return

        # Apply transformations
        painter.translate(
            self.width() / 2 + self.offset_x, self.height() / 2 + self.offset_y
        )
        painter.scale(self.scale, self.scale)

        # Draw edges
        pen = QtGui.QPen(QtGui.QColor(100, 100, 100), 2)
        pen.setStyle(QtCore.Qt.SolidLine)
        painter.setPen(pen)

        for source_name, target_name in self.edges:
            if source_name in self.nodes and target_name in self.nodes:
                source = self.nodes[source_name]
                target = self.nodes[target_name]

                # Draw arrow from source to target
                painter.drawLine(
                    int(source.x), int(source.y), int(target.x), int(target.y)
                )

                # Draw arrowhead
                self._draw_arrow_head(painter, source, target)

        # Draw nodes
        for name, node in self.nodes.items():
            # Draw node circle
            color = node.get_color()
            painter.setBrush(QtGui.QBrush(color))

            # Highlight selected node
            if name == self.selected_node:
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255), 4)
            else:
                pen = QtGui.QPen(QtGui.QColor(50, 50, 50), 2)
            painter.setPen(pen)

            painter.drawEllipse(
                QtCore.QPointF(node.x, node.y), node.radius, node.radius
            )

            # Draw node label
            painter.setPen(QtGui.QColor(255, 255, 255))
            font = painter.font()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)

            text_rect = QtCore.QRectF(
                node.x - node.radius,
                node.y - 8,
                node.radius * 2,
                16,
            )
            painter.drawText(
                text_rect, QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, name
            )

    def _draw_arrow_head(self, painter, source: GraphNode, target: GraphNode):
        """Draw an arrowhead at the target end of an edge"""
        # Calculate arrow direction
        dx = target.x - source.x
        dy = target.y - source.y
        length = (dx * dx + dy * dy) ** 0.5

        if length == 0:
            return

        # Normalize
        dx /= length
        dy /= length

        # Position arrowhead at edge of target circle
        arrow_x = target.x - dx * target.radius
        arrow_y = target.y - dy * target.radius

        # Arrow geometry
        arrow_size = 10
        angle = 0.4  # radians

        # Calculate arrow points
        left_x = arrow_x - arrow_size * (dx * 0.866 - dy * 0.5)
        left_y = arrow_y - arrow_size * (dy * 0.866 + dx * 0.5)
        right_x = arrow_x - arrow_size * (dx * 0.866 + dy * 0.5)
        right_y = arrow_y - arrow_size * (dy * 0.866 - dx * 0.5)

        polygon = QtGui.QPolygonF(
            [
                QtCore.QPointF(arrow_x, arrow_y),
                QtCore.QPointF(left_x, left_y),
                QtCore.QPointF(right_x, right_y),
            ]
        )

        painter.setBrush(QtGui.QBrush(QtGui.QColor(100, 100, 100)))
        painter.drawPolygon(polygon)

    def wheelEvent(self, event):
        """Handle zoom with mouse wheel"""
        delta = event.angleDelta().y()
        zoom_factor = 1.1 if delta > 0 else 0.9
        self.scale *= zoom_factor
        self.scale = max(0.1, min(5.0, self.scale))
        self.update()

    def mousePressEvent(self, event):
        """Handle mouse press for dragging and selection"""
        if event.button() == QtCore.Qt.LeftButton:
            # Transform mouse position to graph coordinates
            x, y = self._screen_to_graph(event.x(), event.y())

            # Check if we clicked on a node
            for name, node in self.nodes.items():
                if node.contains_point(x, y):
                    logger.info(f"Selected node: {name}")
                    self.selected_node = name
                    self.update()
                    return

            # Otherwise, start dragging
            logger.debug("Starting graph panning")
            self.dragging = True
            self.last_mouse_pos = (event.x(), event.y())

    def mouseMoveEvent(self, event):
        """Handle mouse move for panning"""
        if self.dragging and self.last_mouse_pos:
            dx = event.x() - self.last_mouse_pos[0]
            dy = event.y() - self.last_mouse_pos[1]
            self.offset_x += dx
            self.offset_y += dy
            self.last_mouse_pos = (event.x(), event.y())
            self.update()

    def mouseReleaseEvent(self, event):
        """Handle mouse release"""
        if event.button() == QtCore.Qt.LeftButton:
            self.dragging = False
            self.last_mouse_pos = None

    def _screen_to_graph(self, screen_x: float, screen_y: float) -> Tuple[float, float]:
        """Convert screen coordinates to graph coordinates"""
        x = (screen_x - self.width() / 2 - self.offset_x) / self.scale
        y = (screen_y - self.height() / 2 - self.offset_y) / self.scale
        return x, y


class MonitorInfoPanel(QtWidgets.QWidget):
    """Panel showing detailed info about the selected monitor"""

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout()

        self.title_label = QtWidgets.QLabel("No monitor selected")
        font = self.title_label.font()
        font.setPointSize(14)
        font.setBold(True)
        self.title_label.setFont(font)

        self.status_label = QtWidgets.QLabel("")
        self.data_label = QtWidgets.QLabel("")
        self.timestamp_label = QtWidgets.QLabel("")

        layout.addWidget(self.title_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.data_label)
        layout.addWidget(self.timestamp_label)
        layout.addStretch()

        self.setLayout(layout)
        self.setMinimumWidth(250)

    def update_info(self, name: str, node: GraphNode):
        """Update the info panel with node details"""
        logger.debug(f"Updating info panel for node: {name}")

        self.title_label.setText(f"Monitor: {name}")

        status_names = {
            0: "OK",
            1: "BAD_EXPIRED",
            2: "BAD_DEPS",
            4: "BAD_DATA",
            7: "BAD",
            8: "INVALID_DATA",
        }
        status_text = status_names.get(node.status, f"Unknown ({node.status})")
        self.status_label.setText(f"Status: {status_text}")

        self.data_label.setText(f"Data: {node.data}")

        if node.timestamp:
            import time

            time_str = time.strftime("%H:%M:%S", time.localtime(node.timestamp))
            self.timestamp_label.setText(f"Last Update: {time_str}")
        else:
            self.timestamp_label.setText("Last Update: Never")

        logger.info(
            f"Status: {status_text}, Data: {node.data}, Timestamp: {node.timestamp}"
        )

    def clear_info(self):
        """Clear the info panel"""
        self.title_label.setText("No monitor selected")
        self.status_label.setText("")
        self.data_label.setText("")
        self.timestamp_label.setText("")


class MonitorDAGApplet(QtWidgets.QMainWindow):
    """Main applet window"""

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.dataset_prefix = getattr(args, "dataset_prefix", "monitors.")

        self.setWindowTitle("qbutler Monitor DAG")
        self.resize(1000, 600)

        # Create widgets
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QtWidgets.QHBoxLayout()
        central_widget.setLayout(main_layout)

        self.dag_widget = DAGWidget()
        self.info_panel = MonitorInfoPanel()

        main_layout.addWidget(self.dag_widget, stretch=3)
        main_layout.addWidget(self.info_panel, stretch=1)

        # Create legend
        self._create_legend()

        # Connect signals
        self.dag_widget_timer = QtCore.QTimer()
        self.dag_widget_timer.timeout.connect(self._update_info_panel)
        self.dag_widget_timer.start(100)

        # Dataset subscriber (will be set up by ARTIQ applet machinery)
        self.datasets = {}

        # Load dummy data for standalone testing
        use_dummy_data = getattr(args, "dummy_data", False)
        if use_dummy_data:
            self._load_dummy_data()

    def _load_dummy_data(self):
        """Load dummy data for standalone testing"""
        import time

        logger.info("Loading dummy data for standalone testing")

        # Create a sample DAG structure
        dummy_dag = {
            "nodes": ["laser_lock", "temperature", "power", "frequency", "alignment"],
            "edges": [
                ["frequency", "laser_lock"],
                ["power", "laser_lock"],
                ["temperature", "power"],
                ["alignment", "power"],
            ],
        }

        # Simulate dataset updates
        self.datasets[self.dataset_prefix + "dag_structure"] = json.dumps(dummy_dag)

        # Add status data for each node with different states
        current_time = time.time()

        dummy_statuses = {
            "laser_lock": (0, 98.5, current_time),  # OK
            "temperature": (0, 23.4, current_time - 5),  # OK
            "power": (4, 0.85, current_time - 2),  # BAD_DATA
            "frequency": (1, 150.3e6, current_time - 30),  # BAD_EXPIRED
            "alignment": (0, 0.92, current_time - 1),  # OK
        }

        for name, (status, data, timestamp) in dummy_statuses.items():
            self.datasets[f"{self.dataset_prefix}{name}.status"] = status
            self.datasets[f"{self.dataset_prefix}{name}.data"] = data
            self.datasets[f"{self.dataset_prefix}{name}.timestamp"] = timestamp

        # Trigger data update
        self.data_changed(self.datasets, [])

        # Set up timer to simulate live updates
        self.dummy_update_timer = QtCore.QTimer()
        self.dummy_update_timer.timeout.connect(self._update_dummy_data)
        self.dummy_update_timer.start(2000)  # Update every 2 seconds

    def _update_dummy_data(self):
        """Simulate live data updates for testing"""
        import random
        import time

        # Randomly update one of the monitors
        monitors = ["laser_lock", "temperature", "power", "frequency", "alignment"]
        monitor_to_update = random.choice(monitors)

        # Cycle through different statuses
        statuses = [0, 0, 0, 1, 4]  # Mostly OK, sometimes bad
        new_status = random.choice(statuses)
        new_data = random.uniform(0, 100)
        new_timestamp = time.time()

        self.datasets[f"{self.dataset_prefix}{monitor_to_update}.status"] = new_status
        self.datasets[f"{self.dataset_prefix}{monitor_to_update}.data"] = new_data
        self.datasets[f"{self.dataset_prefix}{monitor_to_update}.timestamp"] = (
            new_timestamp
        )

        logger.debug(
            f"Dummy update: {monitor_to_update} -> status={new_status}, data={new_data:.2f}"
        )

        # Update the widget
        self.dag_widget.update_node_status(
            monitor_to_update, new_status, new_data, new_timestamp
        )

    def _create_legend(self):
        """Create a legend showing status colors"""
        legend_dock = QtWidgets.QDockWidget("Legend", self)
        legend_widget = QtWidgets.QWidget()
        legend_layout = QtWidgets.QVBoxLayout()

        legend_items = [
            ("OK", STATUS_COLORS[0]),
            ("EXPIRED", STATUS_COLORS[1]),
            ("BAD_DEPS", STATUS_COLORS[2]),
            ("BAD_DATA", STATUS_COLORS[4]),
            ("BAD", STATUS_COLORS[7]),
            ("INVALID", STATUS_COLORS[8]),
        ]

        for label, color in legend_items:
            item_layout = QtWidgets.QHBoxLayout()

            color_square = QtWidgets.QLabel()
            color_square.setFixedSize(20, 20)
            color_square.setStyleSheet(
                f"background-color: rgb({color.red()}, {color.green()}, {color.blue()}); border: 1px solid black;"
            )

            text_label = QtWidgets.QLabel(label)

            item_layout.addWidget(color_square)
            item_layout.addWidget(text_label)
            item_layout.addStretch()

            legend_layout.addLayout(item_layout)

        legend_layout.addStretch()
        legend_widget.setLayout(legend_layout)
        legend_dock.setWidget(legend_widget)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, legend_dock)

    def _update_info_panel(self):
        """Update the info panel based on selected node"""
        if self.dag_widget.selected_node:
            node = self.dag_widget.nodes.get(self.dag_widget.selected_node)
            if node:
                self.info_panel.update_info(self.dag_widget.selected_node, node)
            else:
                self.info_panel.clear_info()
        else:
            self.info_panel.clear_info()

    def data_changed(self, data, mods):
        """Called when dataset data changes (ARTIQ applet callback)"""
        self.datasets.update(data)

        # Update DAG structure if it changed
        dag_key = self.dataset_prefix + "dag_structure"
        if dag_key in self.datasets:
            try:
                dag_data = json.loads(self.datasets[dag_key])
                self._update_dag_structure(dag_data)
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse DAG structure: {e}")

        # Update individual monitor statuses
        for key, value in self.datasets.items():
            if key.startswith(self.dataset_prefix) and key.endswith(".status"):
                monitor_name = key[len(self.dataset_prefix) : -len(".status")]
                status = value

                data_key = f"{self.dataset_prefix}{monitor_name}.data"
                timestamp_key = f"{self.dataset_prefix}{monitor_name}.timestamp"

                data = self.datasets.get(data_key)
                timestamp = self.datasets.get(timestamp_key)

                self.dag_widget.update_node_status(
                    monitor_name, status, data, timestamp
                )

    def _update_dag_structure(self, dag_data: dict):
        """Update the DAG structure from dataset"""
        # Use NetworkX to compute layout
        G = nx.DiGraph()

        for node in dag_data.get("nodes", []):
            G.add_node(node)

        for source, target in dag_data.get("edges", []):
            G.add_edge(source, target)

        # Compute layout (hierarchical if DAG, spring otherwise)
        try:
            pos = nx.spring_layout(G, k=2, iterations=50, scale=200)
        except Exception as e:
            logger.error(f"Failed to compute graph layout: {e}")
            pos = {node: (0, 0) for node in G.nodes()}

        # Convert to our format
        nodes_dict = {node: (x * 200, y * 200) for node, (x, y) in pos.items()}
        edges_list = list(G.edges())

        self.dag_widget.set_graph_structure(nodes_dict, edges_list)


def main():
    """Main entry point for standalone testing"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Monitor DAG Applet")
    parser.add_argument(
        "--dataset-prefix", default="monitors.", help="Prefix for monitor datasets"
    )
    parser.add_argument(
        "--dummy-data", action="store_true", help="Load dummy data for testing"
    )

    logging.basicConfig(level=logging.DEBUG)

    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    applet = MonitorDAGApplet(args)
    applet.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
