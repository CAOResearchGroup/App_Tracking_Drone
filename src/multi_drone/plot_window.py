"""Qt telemetry viewer, imported only inside the optional display process."""
from __future__ import annotations

import json
import queue
import time
import uuid

from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .models import FACT_GROUPS, FACT_NAMES
from .plot_data import PlotHistory, UNITS, group_by_unit, validate_fields
from .plot_resize import ScopeResizeHandle, layout_resize_handles

Qt = QtCore.Qt
MIME = "application/x-multi-drone-telemetry"
COLORS = ("#46d9b5", "#6ba9ff", "#ffbe70", "#e690ed", "#ff8089", "#d2e477")


def payload_from_mime(mime):
    if not mime.hasFormat(MIME):
        raise ValueError("Drag telemetry fields or a chart's merge handle")
    payload = json.loads(bytes(mime.data(MIME)))
    payload["fields"] = validate_fields(payload["system_id"], payload["fields"])
    return payload


def start_drag(widget, payload):
    mime = QtCore.QMimeData()
    mime.setData(MIME, json.dumps(payload).encode())
    drag = QtGui.QDrag(widget)
    drag.setMimeData(mime)
    drag.exec(Qt.DropAction.CopyAction)


class FieldTree(QtWidgets.QTreeWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setHeaderLabels(["Tham số", "Giá trị"])
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setColumnWidth(0, 220)
        self.itemDoubleClicked.connect(self.open_item)

    def open_item(self, item, column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            system_id, name = data
            if sum(map(len, self.selected_fields().values())) > 1:
                self.window.add_selected()
            else:
                self.window.add_chart(system_id, [name])

    def selected_fields(self):
        groups = {}
        for item in self.selectedItems():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data:
                n, name = data
                groups.setdefault(n, []).append(name)
        return groups

    def startDrag(self, actions):
        groups = self.selected_fields()
        if len(groups) != 1:
            self.window.statusBar().showMessage("Mỗi lần kéo, chọn các tham số của cùng một drone.", 5000)
            return
        n, fields = next(iter(groups.items()))
        start_drag(self, {"system_id": n, "fields": fields})


class MergeHandle(QtWidgets.QLabel):
    def __init__(self, chart):
        super().__init__("⠿ Kéo để gộp")
        self.chart, self.press = chart, None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Kéo vào vùng thả của đồ thị cùng drone. Các đường sẽ chuyển sang ô đích.")
        self.setStyleSheet("color: #46d9b5; padding: 5px;")

    def mousePressEvent(self, event):
        self.press = event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None

    def mouseMoveEvent(self, event):
        if self.press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint()-self.press).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                self.press = None
                start_drag(self, {"system_id": self.chart.system_id, "fields": self.chart.fields,
                                  "source": self.chart.chart_id})


class DropZone(QtWidgets.QLabel):
    def __init__(self, chart):
        super().__init__("＋ Thả tham số / đồ thị cùng drone vào đây")
        self.chart = chart
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumHeight(30)
        self.setStyleSheet("border: 1px dashed #405269; color: #aebed1; padding: 4px;")

    def dragEnterEvent(self, event):
        try:
            payload = payload_from_mime(event.mimeData())
            if payload["system_id"] == self.chart.system_id:
                event.acceptProposedAction()
        except (ValueError, KeyError, TypeError):
            event.ignore()

    def dropEvent(self, event):
        try:
            self.chart.window.apply_drop(self.chart, payload_from_mime(event.mimeData()))
            event.acceptProposedAction()
        except (ValueError, KeyError, TypeError) as exc:
            self.chart.window.statusBar().showMessage(str(exc), 5000)
            event.ignore()


class ScopeViewBox(pg.ViewBox):
    def mouseDragEvent(self, event, axis=None):
        # Freeze follow at the START of a rectangle drag, not only on release.
        # Otherwise the live X range moves underneath a user's selection.
        if event.isStart():
            self.sigRangeChangedManually.emit(self.state["mouseEnabled"])
        super().mouseDragEvent(event, axis=axis)


class ChartScope(QtWidgets.QMdiSubWindow):
    def __init__(self, window, system_id, fields):
        super().__init__()
        self.window, self.system_id = window, system_id
        self.chart_id = uuid.uuid4().hex
        self.fields = validate_fields(system_id, fields)
        self.manual_view = False
        self.reset_at = None
        self.history_epoch = window.history.epoch
        self.setObjectName(self.chart_id)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(320, 250)
        self.plots, self.curves = {}, {}
        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(12, 4, 12, 26)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(MergeHandle(self))
        row.addStretch()
        self.split_button = QtWidgets.QPushButton("Tách đồ thị…")
        self.split_button.setToolTip("Chọn các đại lượng cần tách thành ô riêng")
        self.split_button.clicked.connect(self.choose_split)
        row.addWidget(self.split_button)
        self.remove_button = QtWidgets.QToolButton()
        self.remove_button.setText("Bỏ đường")
        self.remove_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        row.addWidget(self.remove_button)
        layout.addLayout(row)
        controls = QtWidgets.QHBoxLayout()
        self.zoom_in_button = QtWidgets.QPushButton("Zoom +")
        self.zoom_in_button.clicked.connect(lambda: self.zoom(.8))
        controls.addWidget(self.zoom_in_button)
        self.zoom_out_button = QtWidgets.QPushButton("Zoom −")
        self.zoom_out_button.clicked.connect(lambda: self.zoom(1.25))
        controls.addWidget(self.zoom_out_button)
        self.box_zoom_button = QtWidgets.QPushButton("Khoanh zoom")
        self.box_zoom_button.setCheckable(True)
        self.box_zoom_button.setToolTip("Bật: kéo chuột khoanh vùng phóng to. Tắt: kéo để pan.")
        self.box_zoom_button.toggled.connect(self.set_mouse_mode)
        controls.addWidget(self.box_zoom_button)
        self.fit_button = QtWidgets.QPushButton("Vừa dữ liệu")
        self.fit_button.clicked.connect(self.fit_data)
        controls.addWidget(self.fit_button)
        self.live_button = QtWidgets.QPushButton("Bám lại")
        self.live_button.setToolTip("Trở về dữ liệu mới nhất và tự căn trục Y")
        self.live_button.clicked.connect(self.resume_follow)
        controls.addWidget(self.live_button)
        layout.addLayout(controls)
        controls = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Làm mới")
        self.refresh_button.setToolTip("Xóa phần vẽ của ô này, bắt đầu nhận mẫu mới từ lúc bấm")
        self.refresh_button.clicked.connect(lambda: window.reset_chart(self))
        controls.addWidget(self.refresh_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.drop_zone = DropZone(self)
        layout.addWidget(self.drop_zone)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        self.plot_body = QtWidgets.QWidget()
        self.plot_layout = QtWidgets.QVBoxLayout(self.plot_body)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.plot_body)
        layout.addWidget(scroll)
        self.setWidget(body)
        self.resize_handles = {edge: ScopeResizeHandle(self, edge)
                               for edge in ("l", "r", "t", "b", "lt", "rt", "lb", "rb")}
        self.windowStateChanged.connect(lambda old, new: layout_resize_handles(self))
        self.rebuild()

    def manual_navigation(self, *args):
        self.manual_view = True
        for plot in self.plots.values():
            plot.disableAutoRange()
        self.live_button.show()

    def zoom(self, factor):
        self.manual_navigation()
        for plot in self.plots.values():
            plot.getViewBox().scaleBy((factor, factor))

    def set_mouse_mode(self, rectangle):
        for plot in self.plots.values():
            plot.getViewBox().setMouseMode(pg.ViewBox.RectMode if rectangle else pg.ViewBox.PanMode)

    def fit_data(self):
        self.manual_navigation()
        for plot in self.plots.values():
            plot.getViewBox().autoRange()

    def resume_follow(self):
        self.manual_view = False
        self.window.follow.setChecked(True)
        for plot in self.plots.values():
            plot.enableAutoRange(axis="y", enable=True)
        self.live_button.hide()
        self.window.refresh_chart(self)

    def choose_split(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Chọn đại lượng để tách")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel("Mỗi đại lượng được chọn sẽ mở trong một ô riêng."))
        choices = QtWidgets.QListWidget()
        for name in self.fields:
            item = QtWidgets.QListWidgetItem(name, choices)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        layout.addWidget(choices)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        accept = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        accept.setText("Tách đã chọn")
        accept.setEnabled(False)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        choices.itemChanged.connect(lambda: accept.setEnabled(any(
            choices.item(i).checkState() == Qt.CheckState.Checked for i in range(choices.count()))))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.window.split_chart(self, [choices.item(i).text() for i in range(choices.count())
                                          if choices.item(i).checkState() == Qt.CheckState.Checked])
        dialog.deleteLater()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        layout_resize_handles(self)

    def showEvent(self, event):
        super().showEvent(event)
        layout_resize_handles(self)

    def rebuild(self):
        ranges = {unit: plot.viewRange() for unit, plot in self.plots.items()}
        for plot in self.plots.values():
            self.plot_layout.removeWidget(plot)
            plot.close()
            plot.deleteLater()
        self.plots, self.curves = {}, {}
        first = None
        for index, name in enumerate(self.fields):
            unit = UNITS[name]
            if unit not in self.plots:
                plot = pg.PlotWidget(background="#101b2b", viewBox=ScopeViewBox())
                plot.setMinimumHeight(180 if len({UNITS[f] for f in self.fields}) > 1 else 145)
                plot.showGrid(x=True, y=True, alpha=.16)
                plot.setLabel("left", unit)
                plot.getAxis("left").enableAutoSIPrefix(False)
                plot.getAxis("bottom").enableAutoSIPrefix(False)
                plot.getPlotItem().hideButtons()
                plot.getViewBox().sigRangeChangedManually.connect(self.manual_navigation)
                plot.setToolTip("Cuộn chuột: zoom · Kéo chuột: pan · Khoanh zoom: chọn vùng phóng to")
                plot.setLabel("bottom", "Từ lúc làm mới" if self.reset_at is not None else "Thời gian", units="s")
                plot.addLegend(labelTextSize="10pt", labelTextColor="#e6edf6",
                               brush=pg.mkBrush(16, 27, 43, 210), offset=(10, 8))
                if first is not None:
                    plot.setXLink(first)
                else:
                    first = plot
                self.plots[unit] = plot
                self.plot_layout.addWidget(plot)
            color = COLORS[index % len(COLORS)]
            self.curves[name] = self.plots[unit].plot(
                name=name, pen=pg.mkPen(color, width=1.8,
                    style=Qt.PenStyle.SolidLine if index < len(COLORS) else Qt.PenStyle.DashLine),
                connect="finite")
        old_menu = self.remove_button.menu()
        menu = QtWidgets.QMenu(self)
        for name in self.fields:
            menu.addAction(name, lambda checked=False, field=name: self.remove_field(field))
        self.remove_button.setMenu(menu)
        if old_menu:
            old_menu.deleteLater()
        title = f"Drone ID {self.system_id}: {', '.join(self.fields)}"
        self.setWindowTitle(title)
        self.setToolTip(title)
        self.axis_label_reset = self.reset_at is not None
        self.split_button.setVisible(len(self.fields) > 1)
        self.remove_button.setVisible(len(self.fields) > 1)
        self.live_button.setVisible(self.manual_view or not self.window.follow.isChecked())
        self.set_mouse_mode(self.box_zoom_button.isChecked())
        if self.manual_view:
            for unit, plot in self.plots.items():
                plot.disableAutoRange()
                if unit in ranges:
                    plot.setRange(xRange=ranges[unit][0], yRange=ranges[unit][1], padding=0)

    def detach(self, position=None):
        """A scope is always a child of the right-hand workspace, never a native window."""
        self.window.workspace.setViewMode(QtWidgets.QMdiArea.ViewMode.SubWindowView)
        if position is None:
            position = QtCore.QPoint(24, 24)
        self.showNormal()
        area = self.window.workspace.viewport().size()
        self.resize(min(880, area.width()), min(560, area.height()))
        self.move(max(0, min(position.x(), area.width()-self.width())),
                  max(0, min(position.y(), area.height()-self.height())))
        self.window.refresh_chart(self)

    def remove_field(self, name):
        self.fields.remove(name)
        if self.fields:
            self.rebuild()
        else:
            self.close()

    def render(self, history, now, span, follow):
        if history.epoch != self.history_epoch:
            if self.history_epoch is not None:
                self.reset_at = None  # A new connection already discarded the old history.
            self.history_epoch = history.epoch
        origin = self.reset_at if self.reset_at is not None else history.started
        for name, curve in self.curves.items():
            x, y = history.series(self.system_id, name)
            if self.reset_at is not None and history.started is not None:
                # Filter by sample receive time, including snapshots queued BEFORE the click.
                points = [(history.started+t-origin, v) for t, v in zip(x, y)
                          if history.started+t >= self.reset_at]
                x, y = [p[0] for p in points], [p[1] for p in points]
            curve.setData(x, y, connect="finite")
        for plot in self.plots.values():
            if self.axis_label_reset != (self.reset_at is not None):
                plot.setLabel("bottom", "Từ lúc làm mới" if self.reset_at is not None else "Thời gian", units="s")
            if follow and not self.manual_view and origin is not None:
                end = max(span, now-origin)
                plot.setXRange(end-span, end, padding=0)
        self.axis_label_reset = self.reset_at is not None

    def closeEvent(self, event):
        self.window.charts.pop(self.chart_id, None)
        super().closeEvent(event)


class PlotWindow(QtWidgets.QMainWindow):
    def __init__(self, frames, stop):
        super().__init__()
        self.frames, self.stop = frames, stop
        self.history = PlotHistory()
        self.charts, self.items, self.drone_items = {}, {}, {}
        self.default_ids = set()
        self.last_tree_update = 0.
        self.setWindowTitle("Multi-Drone · Telemetry trực tiếp")
        self.resize(1440, 920)
        self.splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(7)
        self.setCentralWidget(self.splitter)
        self.workspace = QtWidgets.QMdiArea()
        self.workspace.setMinimumSize(460, 320)
        self.workspace.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.workspace.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.workspace.setOption(QtWidgets.QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation)
        self.workspace.setTabsClosable(True)
        self.workspace.setTabsMovable(True)
        self.workspace.setBackground(QtGui.QBrush(QtGui.QColor("#101b2b")))
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #142032; color: #e6edf6; font-size: 12px; }
            QMdiSubWindow { background: #20324a; }
            QSplitter::handle { background: #304259; }
            QSplitter::handle:hover { background: #46d9b5; }
            QTreeWidget, QLineEdit { background: #101b2b; border: 1px solid #304259; }
            QTreeWidget::item { padding: 3px; }
            QTreeWidget::item:selected { background: #285573; }
            QHeaderView::section { background: #20324a; border: 0; padding: 5px; }
            QPushButton, QToolButton, QComboBox { background: #243a54; padding: 5px 8px; border-radius: 4px; }
            QPushButton:hover, QToolButton:hover { background: #34516e; }
            QPushButton:checked { background: #285573; border: 1px solid #46d9b5; }
            QToolBar { spacing: 10px; padding: 6px; border: 0; }
            QMenu { background: #20324a; }
            QMenu::item:selected { background: #34516e; }
        """)
        toolbar = self.addToolBar("Hiển thị")
        toolbar.setMovable(False)
        title = QtWidgets.QLabel("  TELEMETRY  /  ")
        title.setStyleSheet("color: #46d9b5; font-weight: bold;")
        toolbar.addWidget(title)
        self.pause = QtWidgets.QCheckBox("Dừng hình")
        self.pause.setToolTip("Chỉ dừng vẽ; telemetry và điều khiển tiếp tục chạy")
        toolbar.addWidget(self.pause)
        self.follow = QtWidgets.QCheckBox("Bám thời gian")
        self.follow.setChecked(True)
        self.follow.setToolTip("Zoom/pan tự giữ vùng nhìn của từng ô. Bật lại để mọi ô bám dữ liệu mới.")
        self.follow.toggled.connect(self.set_follow)
        toolbar.addWidget(self.follow)
        self.span = QtWidgets.QComboBox()
        self.span.addItems(["15 s", "30 s", "60 s", "120 s"])
        self.span.setCurrentIndex(2)
        toolbar.addWidget(self.span)
        toolbar.addAction("Gộp cùng đơn vị", self.merge_by_drone)
        toolbar.addAction("Xếp tab", self.tabify_charts)
        toolbar.addAction("Xếp dọc", self.stack_charts)
        self.connection_label = QtWidgets.QLabel("  Đang chờ telemetry…")
        toolbar.addWidget(self.connection_label)
        self.sidebar = QtWidgets.QWidget()
        self.sidebar.setObjectName("telemetry-browser")
        layout = QtWidgets.QVBoxLayout(self.sidebar)
        heading = QtWidgets.QLabel("DRONE / THAM SỐ")
        heading.setStyleSheet("background: #20324a; padding: 7px; font-weight: bold;")
        layout.addWidget(heading)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Lọc tên: localPosition, battery…")
        self.search.textChanged.connect(self.filter_fields)
        layout.addWidget(self.search)
        self.tree = FieldTree(self)
        layout.addWidget(self.tree)
        button = QtWidgets.QPushButton("Vẽ các tham số đã chọn")
        button.clicked.connect(self.add_selected)
        layout.addWidget(button)
        tip = QtWidgets.QLabel("Ctrl/Cmd hoặc Shift + click để chọn nhiều,\nrồi bấm Vẽ hoặc double-click lựa chọn.\nCùng drone + đơn vị → chung một ô.\nCuộn chuột để zoom, kéo để pan.\nKéo vạch ngăn để đổi chiều rộng cột.")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        self.sidebar.setMinimumWidth(260)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.workspace)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([350, 1090])
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def add_chart(self, system_id, fields):
        fields = validate_fields(system_id, fields)
        groups = group_by_unit(fields)
        if len(groups) > 1:
            charts = [self.add_chart(system_id, group) for group in groups]
            self.stack_charts()
            return charts[0]
        # Uniqueness is by (sysid, field), across all workspace scopes and tabs.
        chart = next((c for c in self.charts.values()
                      if c.system_id == system_id and any(f in c.fields for f in fields)), None)
        if chart is not None:
            self.move_fields(chart, fields)
            return chart
        chart = ChartScope(self, system_id, fields)
        self.charts[chart.chart_id] = chart
        self.workspace.addSubWindow(chart)
        area = self.workspace.viewport().size()
        chart.resize(min(800, area.width()), min(480, area.height()))
        offset = ((len(self.charts)-1) % 6)*24
        chart.move(offset, offset)
        chart.show()
        return chart

    def set_follow(self, follow):
        for chart in self.charts.values():
            if follow:
                chart.manual_view = False
                for plot in chart.plots.values():
                    plot.enableAutoRange(axis="y", enable=True)
            else:
                chart.manual_navigation()
            chart.live_button.setVisible(not follow)

    def refresh_chart(self, chart):
        """Focus/redraw without resetting; used by selection, merge and layout actions."""
        now = time.monotonic()
        self.history.advance(now)
        chart.render(self.history, now, int(self.span.currentText().split()[0]), self.follow.isChecked())
        if self.isMinimized():
            self.showNormal()
        self.show()
        if chart.isMinimized():
            chart.showNormal()
        chart.show()
        self.workspace.setActiveSubWindow(chart)
        chart.raise_()

    def reset_chart(self, chart):
        """Restart only this scope's view; do not clear shared telemetry or send commands."""
        chart.reset_at = time.monotonic()
        chart.history_epoch = self.history.epoch
        chart.manual_view = False
        for plot in chart.plots.values():
            plot.enableAutoRange(axis="y", enable=True)
            plot.setXRange(0, int(self.span.currentText().split()[0]), padding=0)
        chart.live_button.setVisible(not self.follow.isChecked())
        self.refresh_chart(chart)

    def move_fields(self, destination, fields):
        """Transfer requested curves, keeping every unselected curve in its original scope."""
        if any(UNITS[f] != UNITS[destination.fields[0]] for f in fields):
            raise ValueError("Chỉ gộp các đại lượng cùng đơn vị vào một ô")
        selected = set(fields)
        for chart in list(self.charts.values()):
            if chart is destination or chart.system_id != destination.system_id:
                continue
            remaining = [f for f in chart.fields if f not in selected]
            if remaining != chart.fields:
                # Merging must not resurrect samples hidden by an earlier reset.
                if chart.reset_at is not None:
                    destination.reset_at = max(destination.reset_at or chart.reset_at, chart.reset_at)
                if remaining:
                    chart.fields = remaining
                    chart.rebuild()
                else:
                    chart.close()
        combined = list(dict.fromkeys(destination.fields + fields))
        if combined != destination.fields:
            destination.fields = combined
            destination.rebuild()
        self.refresh_chart(destination)

    def add_selected(self):
        for n, fields in self.tree.selected_fields().items():
            self.add_chart(n, fields)
        self.stack_charts()

    def apply_drop(self, destination, payload):
        fields = validate_fields(payload["system_id"], payload["fields"])
        if payload["system_id"] != destination.system_id:
            raise ValueError("Chỉ gộp các tham số của cùng một drone")
        for group in group_by_unit(fields):
            if UNITS[group[0]] == UNITS[destination.fields[0]]:
                self.move_fields(destination, group)
            else:
                self.add_chart(payload["system_id"], group)
        self.stack_charts()

    def split_chart(self, chart, fields=None):
        if chart.chart_id not in self.charts or len(chart.fields) < 2:
            return
        fields = list(chart.fields) if fields is None else [f for f in dict.fromkeys(fields) if f in chart.fields]
        if not fields:
            return
        system_id = chart.system_id
        position = chart.pos()
        reset_at, history_epoch = chart.reset_at, chart.history_epoch
        manual_view = chart.manual_view
        ranges = {unit: plot.viewRange() for unit, plot in chart.plots.items()}
        remaining = [f for f in chart.fields if f not in fields]
        # Release only selected curves; keep the remainder together.
        if remaining:
            chart.fields = remaining
            chart.rebuild()
        else:
            chart.close()
        for index, name in enumerate(fields):
            child = self.add_chart(system_id, [name])
            child.reset_at, child.history_epoch = reset_at, history_epoch
            child.manual_view = manual_view
            child.detach(position+QtCore.QPoint(index*24, index*24))
            if manual_view:
                child.manual_navigation()
                bounds = ranges[UNITS[name]]
                child.plots[UNITS[name]].setRange(xRange=bounds[0], yRange=bounds[1], padding=0)
        self.stack_charts()

    def merge_by_drone(self):
        destinations = {}
        for chart in list(self.charts.values()):
            key = (chart.system_id, UNITS[chart.fields[0]])
            if key not in destinations:
                destinations[key] = chart
            else:
                self.move_fields(destinations[key], chart.fields)
        self.stack_charts()

    def tabify_charts(self):
        self.workspace.setViewMode(QtWidgets.QMdiArea.ViewMode.TabbedView)
        for chart in self.charts.values():
            layout_resize_handles(chart)

    def stack_charts(self):
        charts = list(self.charts.values())
        self.workspace.setViewMode(QtWidgets.QMdiArea.ViewMode.SubWindowView)
        if not charts:
            return
        area = self.workspace.viewport().size()
        height = max(280, area.height()//len(charts))
        width = area.width()-(20 if height*len(charts) > area.height() else 0)
        for index, chart in enumerate(charts):
            chart.showNormal()
            chart.setGeometry(0, index*height, width, height)

    def filter_fields(self):
        query = self.search.text().lower().strip()
        for (n, name), item in self.items.items():
            item.setHidden(bool(query and query not in name.lower() and query != str(n)))
        for drone in self.drone_items.values():
            for i in range(drone.childCount()):
                group = drone.child(i)
                group.setHidden(all(group.child(j).isHidden() for j in range(group.childCount())))
                if query:
                    group.setExpanded(True)

    def update_tree(self, now):
        for n, state in sorted(self.history.frame.get("vehicles", {}).items()):
            if n not in self.drone_items:
                drone = QtWidgets.QTreeWidgetItem(self.tree)
                self.drone_items[n] = drone
                drone.setExpanded(True)
                for group, names in FACT_GROUPS.items():
                    branch = QtWidgets.QTreeWidgetItem(drone, [group])
                    branch.setExpanded(group == "localPosition")
                    for short in names:
                        name = f"{group}.{short}"
                        item = QtWidgets.QTreeWidgetItem(branch, [f"{short} [{UNITS[name]}]", "—"])
                        item.setData(0, Qt.ItemDataRole.UserRole, (n, name))
                        item.setToolTip(0, name)
                        self.items[n, name] = item
                self.filter_fields()
                if n not in self.default_ids and len(self.default_ids) < 3:
                    self.default_ids.add(n)
                    if not any(c.system_id == n for c in self.charts.values()):
                        self.add_chart(n, ["localPosition.x", "localPosition.y", "localPosition.z"])
                        self.stack_charts()
            hb = state["heartbeat_at"]
            live = (self.history.frame["connected"] and hb is not None and
                    now-hb <= self.history.frame["timeouts"]["heartbeat"])
            self.drone_items[n].setText(0, f"Drone {n} · {state['mode'] or '—'}")
            self.drone_items[n].setText(1, "LIVE" if live else "MẤT LINK")
            for name in FACT_NAMES:
                value, at = self.history.value(n, name, now)
                item = self.items[n, name]
                item.setText(1, f"{value:.7g}" if value is not None else "—")
                item.setToolTip(1, "Chưa có / dữ liệu cũ" if at is None else f"Tuổi dữ liệu: {max(0, now-at):.2f} s")
        endpoint = self.history.frame.get("endpoint") or "chưa kết nối"
        connection = "" if self.history.frame.get("connected") else " · ĐÃ NGẮT"
        self.connection_label.setText(f"  {endpoint}{connection} · 10 Hz hiển thị")

    def poll(self):
        if self.stop.is_set():
            self.close()
            return
        while True:
            try:
                frame = self.frames.get_nowait()
                if self.history.epoch is not None and frame["epoch"] != self.history.epoch:
                    self.tree.clear()
                    self.items.clear()
                    self.drone_items.clear()
                self.history.update(frame)
            except queue.Empty:
                break
        now = time.monotonic()
        self.history.advance(now)
        if now-self.last_tree_update > .3:
            self.update_tree(now)
            self.last_tree_update = now
        if not self.pause.isChecked():
            span = int(self.span.currentText().split()[0])
            for chart in list(self.charts.values()):
                chart.render(self.history, now, span, self.follow.isChecked())

    def closeEvent(self, event):
        self.timer.stop()
        self.stop.set()
        # Closing scopes never touches the flight app.
        for chart in list(self.charts.values()):
            chart.close()
        super().closeEvent(event)


def run_window(frames, stop, ready):
    app = QtWidgets.QApplication([])
    app.setApplicationName("Multi-Drone Telemetry")
    window = PlotWindow(frames, stop)
    window.show()
    ready.send({"ready": True})
    app.exec()
