import importlib.util
import math
import os
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from support import ROOT, VehicleState, wait_until
from multi_drone.app import App
from multi_drone.config import Config
from multi_drone.models import FACT_NAMES
from multi_drone.plot_data import PlotHistory, UNITS, snapshot_frame, validate_fields


def frame(now, value=1., stamp=None, epoch=1, connected=True):
    return {"epoch": epoch, "now": now, "connected": connected, "endpoint": "sim",
            "timeouts": {"heartbeat": 3., "state": .6, "health": 3.},
            "vehicles": {1: {"heartbeat_at": now, "mode": "HOLD", "armed": False,
                            "facts": {"localPosition.x": (value, now if stamp is None else stamp)}}}}


class PlotDataTests(unittest.TestCase):
    def test_all_52_fields_have_units_and_invalid_names_fail(self):
        self.assertEqual(set(UNITS), set(FACT_NAMES))
        self.assertEqual(validate_fields(2, ["localPosition.x"]*2), ["localPosition.x"])
        for n, fields in ((True, ["localPosition.x"]), (1, ["unknown"]), (256, ["gps.lat"]), (2, [])):
            with self.assertRaises(ValueError):
                validate_fields(n, fields)

    def test_no_duplicate_samples_or_zero_for_unavailable(self):
        history = PlotHistory()
        history.update(frame(10))
        history.update(frame(10.1, stamp=10))
        self.assertEqual(history.series(1, "localPosition.x"), ([0.], [1.]))
        self.assertEqual(history.series(1, "battery.voltage"), ([], []))
        history.update(frame(10.2, value=float("inf")))
        self.assertTrue(math.isnan(history.series(1, "localPosition.x")[1][-1]))

    def test_stale_data_and_recovery_break_the_line(self):
        history = PlotHistory()
        history.update(frame(10))
        history.advance(11)
        self.assertIsNone(history.value(1, "localPosition.x", 11)[0])
        history.update(frame(11.1, 3.))
        values = history.series(1, "localPosition.x")[1]
        self.assertEqual(values[0], 1.)
        self.assertTrue(math.isnan(values[1]))
        self.assertEqual(values[2], 3.)

    def test_gap_is_detected_even_when_intermediate_frames_were_dropped(self):
        history = PlotHistory()
        history.update(frame(10))
        history.update(frame(12, 4.))
        self.assertTrue(math.isnan(history.series(1, "localPosition.x")[1][1]))

    def test_disconnect_invalidates_fresh_values_and_reconnect_resets_history(self):
        history = PlotHistory()
        history.update(frame(10))
        history.update(frame(10.1, connected=False))
        self.assertIsNone(history.value(1, "localPosition.x", 10.1)[0])
        history.update(frame(10.2, 5., epoch=2))
        self.assertEqual(history.series(1, "localPosition.x"), ([0.], [5.]))

    def test_history_bounded_by_points_and_time(self):
        history = PlotHistory(seconds=2, max_points=4)
        for i in range(30):
            history.update(frame(10+i/10))
        self.assertEqual(len(history.series(1, "localPosition.x")[0]), 4)
        history.advance(20)
        x, y = history.series(1, "localPosition.x")
        self.assertTrue(all(t >= 8 for t in x))
        self.assertTrue(all(math.isnan(v) for v in y))

    def test_snapshot_is_detached_and_keeps_per_drone_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(Config.default(), directory)
            try:
                for n in (1, 2):
                    state = VehicleState(n, heartbeat_at=10)
                    state.set_fact("localPosition.x", n*10, 10)
                    app.registry.vehicles[n] = state
                data = snapshot_frame(app, 10)
                app.registry.vehicles[1].set_fact("localPosition.x", 999, 11)
                self.assertEqual(data["vehicles"][1]["facts"]["localPosition.x"], (10, 10))
                self.assertEqual(data["vehicles"][2]["facts"]["localPosition.x"], (20, 10))
            finally:
                app.close()

    def test_full_display_queue_does_not_block_the_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(Config.default(), directory)
            service = app.plots
            service.frames = queue.Queue(maxsize=2)
            service.stop = threading.Event()
            service.process = SimpleNamespace(is_alive=lambda: True)
            worker = threading.Thread(target=service._publish)
            try:
                worker.start()
                wait_until(service.frames.full)
                service.stop.set()
                worker.join(timeout=.5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(service.frames.qsize(), 2)
            finally:
                service.stop.set()
                worker.join(timeout=1)
                service.process = service.frames = service.stop = None
                app.close()


HAS_QT = all(importlib.util.find_spec(name) is not None for name in ("PySide6", "pyqtgraph"))


@unittest.skipUnless(HAS_QT, "Install .[plots] for Qt tests")
class PlotWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6 import QtWidgets
        cls.qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from multi_drone.plot_window import PlotWindow
        self.frames = queue.Queue()
        self.window = PlotWindow(self.frames, threading.Event())
        self.window.timer.stop()
        self.addCleanup(self.window.close)

    def test_drag_drop_merges_same_drone_and_removes_source(self):
        from PySide6 import QtCore, QtGui, QtWidgets
        from multi_drone.plot_window import MIME
        import json
        a = self.window.add_chart(2, ["localPosition.x"])
        b = self.window.add_chart(2, ["localPosition.y"])
        self.window.add_chart(2, ["battery.voltage"])
        data = QtCore.QMimeData()
        data.setData(MIME, json.dumps({"system_id": 2, "fields": b.fields, "source": b.chart_id}).encode())
        enter = QtGui.QDragEnterEvent(QtCore.QPoint(5, 5), QtCore.Qt.DropAction.CopyAction, data,
                                     QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(a.drop_zone, enter)
        self.assertTrue(enter.isAccepted())
        drop = QtGui.QDropEvent(QtCore.QPointF(5, 5), QtCore.Qt.DropAction.CopyAction, data,
                               QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(a.drop_zone, drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(a.fields, ["localPosition.x", "localPosition.y"])
        self.assertNotIn(b.chart_id, self.window.charts)
        self.assertEqual(set(a.plots), {"m"})
        self.assertEqual(len(a.curves), 2)
        self.assertEqual(len(self.window.charts), 2)

    def test_different_drone_cannot_merge(self):
        a = self.window.add_chart(1, ["localPosition.x"])
        with self.assertRaises(ValueError):
            self.window.apply_drop(a, {"system_id": 2, "fields": ["localPosition.y"]})
        self.assertEqual(a.fields, ["localPosition.x"])

    def test_split_remove_tab_and_workspace(self):
        from PySide6 import QtWidgets
        a = self.window.add_chart(1, ["localPosition.x", "localPosition.y"])
        self.window.split_chart(a)
        self.assertEqual(len(self.window.charts), 2)
        self.window.tabify_charts()
        self.assertEqual(self.window.workspace.viewMode(), QtWidgets.QMdiArea.ViewMode.TabbedView)
        self.window.stack_charts()
        b = next(iter(self.window.charts.values()))
        self.assertEqual(self.window.workspace.viewMode(), QtWidgets.QMdiArea.ViewMode.SubWindowView)
        self.assertIs(b.mdiArea(), self.window.workspace)
        self.assertFalse(b.isWindow())
        b.remove_field(b.fields[0])
        self.assertEqual(len(self.window.charts), 1)

    def test_poll_updates_curves_and_reconnection_clears_old_tree(self):
        now = time.monotonic()
        self.frames.put(frame(now, 8.))
        self.window.poll()
        self.assertEqual(len(self.window.items), 52)
        chart = next(iter(self.window.charts.values()))
        self.assertEqual(list(chart.curves["localPosition.x"].getData()[1]), [8.])
        self.frames.put({**frame(now+1, epoch=2), "vehicles": {}})
        self.window.poll()
        self.assertFalse(self.window.items)
        self.assertFalse(self.window.history.points)

    def assert_unique_curves(self):
        keys = [(c.system_id, f) for c in self.window.charts.values() for f in c.fields]
        self.assertEqual(len(keys), len(set(keys)))

    def test_reopening_field_refreshes_existing_curve_even_in_a_group(self):
        now = time.monotonic()
        self.window.history.update(frame(now, 8.))
        a = self.window.add_chart(1, ["localPosition.x", "localPosition.y"])
        curve = a.curves["localPosition.x"]
        self.window.history.update(frame(now+.1, 9.))
        self.window.pause.setChecked(True)
        for _ in range(5):
            self.assertIs(self.window.add_chart(1, ["localPosition.x"]), a)
        self.assertEqual(len(self.window.charts), 1)
        self.assertIs(a.curves["localPosition.x"], curve)
        self.assertEqual(list(curve.getData()[1]), [8., 9.])
        self.assertTrue(self.window.pause.isChecked())  # Explicit refresh is one-shot.
        self.assert_unique_curves()

    @staticmethod
    def curve_data(chart, name="localPosition.x"):
        x, y = chart.curves[name].getData()
        return ([] if x is None else list(x), [] if y is None else list(y))

    def test_refresh_button_restarts_only_its_scope_from_click_time(self):
        now = time.monotonic()
        self.window.history.update(frame(now, 8.))
        a = self.window.add_chart(1, ["localPosition.x"])
        other = self.window.add_chart(1, ["localPosition.y"])
        self.window.history.frame["vehicles"][1]["facts"]["localPosition.y"] = (6., now)
        self.window.history.advance(now)
        other.render(self.window.history, now, 60, True)
        with patch("multi_drone.plot_window.time.monotonic", return_value=now+.2):
            a.refresh_button.click()
        self.assertEqual(a.reset_at, now+.2)
        self.assertEqual(self.curve_data(a), ([], []))
        self.assertEqual(self.curve_data(other, "localPosition.y")[1], [6.])
        self.assertEqual(self.window.history.series(1, "localPosition.x")[1], [8.])
        # An old receive timestamp in a later display snapshot must stay hidden.
        self.window.history.update(frame(now+.25, 99., stamp=now+.15))
        a.render(self.window.history, now+.25, 60, True)
        self.assertEqual(self.curve_data(a), ([], []))
        self.window.history.update(frame(now+.4, 10.))
        a.render(self.window.history, now+.4, 60, True)
        x, y = self.curve_data(a)
        self.assertEqual(y, [10.])
        self.assertAlmostEqual(x[0], .2, places=6)

    def test_refresh_while_paused_clears_then_waits_for_resume(self):
        now = time.monotonic()
        self.window.history.update(frame(now, 8.))
        a = self.window.add_chart(1, ["localPosition.x"])
        a.render(self.window.history, now, 60, True)
        self.window.pause.setChecked(True)
        with patch("multi_drone.plot_window.time.monotonic", return_value=now+.1):
            a.refresh_button.click()
        self.assertEqual(self.curve_data(a), ([], []))
        self.window.history.update(frame(now+.2, 9.))
        with patch("multi_drone.plot_window.time.monotonic", return_value=now+.2):
            self.window.poll()
        self.assertTrue(self.window.pause.isChecked())
        self.assertEqual(self.curve_data(a), ([], []))
        self.window.pause.setChecked(False)
        with patch("multi_drone.plot_window.time.monotonic", return_value=now+.2):
            self.window.poll()
        self.assertEqual(self.curve_data(a)[1], [9.])

    def test_split_merge_and_focus_do_not_resurrect_pre_refresh_history(self):
        now = time.monotonic()
        self.window.history.update(frame(now, 8.))
        a = self.window.add_chart(1, ["localPosition.x", "localPosition.y"])
        with patch("multi_drone.plot_window.time.monotonic", return_value=now+.1):
            a.refresh_button.click()
            self.window.split_chart(a)
            self.window.merge_by_drone()
            merged = self.window.add_chart(1, ["localPosition.x"])
        self.assertEqual(merged.reset_at, now+.1)
        self.assertEqual(self.curve_data(merged), ([], []))

    def test_refresh_before_first_data_and_reconnect(self):
        now = time.monotonic()
        a = self.window.add_chart(1, ["localPosition.x"])
        with patch("multi_drone.plot_window.time.monotonic", return_value=now):
            a.refresh_button.click()
        self.window.history.update(frame(now+.2, 3.))
        a.render(self.window.history, now+.2, 60, True)
        self.assertAlmostEqual(self.curve_data(a)[0][0], .2, places=6)
        self.window.history.update(frame(now+1, 7., epoch=2))
        a.render(self.window.history, now+1, 60, True)
        self.assertIsNone(a.reset_at)
        self.assertEqual(self.curve_data(a), ([0.], [7.]))

    def test_large_resize_handles_drag_all_edges_without_moving_sidebar(self):
        from PySide6 import QtCore, QtGui, QtTest, QtWidgets
        self.window.show()
        self.qt.processEvents()
        a = self.window.add_chart(1, ["localPosition.x"])
        a.detach()
        self.qt.processEvents()
        sidebar_rect = self.window.sidebar.geometry()
        for edge in ("l", "r", "t", "b", "lt", "rt", "lb", "rb"):
            with self.subTest(edge=edge):
                a.setGeometry(80, 80, 600, 420)
                self.qt.processEvents()
                handle = a.resize_handles[edge]
                self.assertTrue(handle.isVisible())
                center = handle.rect().center()
                self.assertIs(a.childAt(handle.mapTo(a, center)), handle)
                before = a.geometry()
                delta = QtCore.QPoint(-20 if "l" in edge else 20 if "r" in edge else 0,
                                     -20 if "t" in edge else 20 if "b" in edge else 0)
                QtTest.QTest.mousePress(handle, QtCore.Qt.MouseButton.LeftButton, pos=center)
                end = center+delta
                move = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(end),
                                        QtCore.QPointF(handle.mapToGlobal(end)), QtCore.Qt.MouseButton.NoButton,
                                        QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
                self.qt.sendEvent(handle, move)
                QtTest.QTest.mouseRelease(handle, QtCore.Qt.MouseButton.LeftButton, pos=end)
                if "l" in edge or "r" in edge:
                    self.assertEqual(a.width(), before.width()+20)
                if "t" in edge or "b" in edge:
                    self.assertEqual(a.height(), before.height()+20)
                self.assertEqual(self.window.sidebar.geometry(), sidebar_rect)
                self.assertFalse(a.isWindow())
        self.assertEqual(a.resize_handles["rb"].size(), QtCore.QSize(28, 28))
        self.assertEqual(a.resize_handles["l"].width(), 12)
        # Wide grips must leave the native title-bar controls clickable.
        options = QtWidgets.QStyleOptionTitleBar()
        options.initFrom(a)
        options.titleBarFlags = a.windowFlags()
        options.rect = QtCore.QRect(0, 0, a.width(),
                                   a.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_TitleBarHeight))
        close_rect = a.style().subControlRect(QtWidgets.QStyle.ComplexControl.CC_TitleBar, options,
                                              QtWidgets.QStyle.SubControl.SC_TitleBarCloseButton, a)
        self.assertFalse(any(h.geometry().contains(close_rect.center()) for h in a.resize_handles.values()))
        a.showMaximized()
        self.qt.processEvents()
        self.assertTrue(all(not h.isVisible() for h in a.resize_handles.values()))
        a.showNormal()
        self.qt.processEvents()
        self.window.tabify_charts()
        self.assertTrue(all(not h.isVisible() for h in a.resize_handles.values()))

    def test_same_field_on_different_drones_is_allowed(self):
        a = self.window.add_chart(1, ["localPosition.x"])
        b = self.window.add_chart(2, ["localPosition.x"])
        self.assertIsNot(a, b)
        self.assertEqual(a.windowTitle(), "Drone ID 1: localPosition.x")
        self.assertEqual(b.windowTitle(), "Drone ID 2: localPosition.x")
        self.assert_unique_curves()

    def test_multi_selection_transfers_only_selected_curves(self):
        a = self.window.add_chart(1, ["localPosition.x"])
        b = self.window.add_chart(1, ["localPosition.y", "localPosition.z"])
        result = self.window.add_chart(1, ["localPosition.x", "localPosition.y", "battery.voltage"])
        self.assertIs(result, a)
        self.assertEqual(b.fields, ["localPosition.z"])
        self.assertEqual(a.fields, ["localPosition.x", "localPosition.y"])
        voltage = next(c for c in self.window.charts.values() if "battery.voltage" in c.fields)
        self.assertEqual(voltage.fields, ["battery.voltage"])
        self.assertEqual(b.windowTitle(), "Drone ID 1: localPosition.z")
        self.assert_unique_curves()

    def test_drag_different_unit_keeps_separate_scopes(self):
        a = self.window.add_chart(1, ["localPosition.x", "localPosition.y"])
        b = self.window.add_chart(1, ["battery.voltage"])
        self.window.apply_drop(b, {"system_id": 1, "fields": ["localPosition.x"]})
        self.assertEqual(a.fields, ["localPosition.x", "localPosition.y"])
        self.assertEqual(b.windowTitle(), "Drone ID 1: battery.voltage")
        self.window.apply_drop(b, {"system_id": 1, "fields": ["localPosition.x"]})
        self.assert_unique_curves()

    def test_double_click_groups_selected_items(self):
        self.window.history.update(frame(time.monotonic()))
        self.window.update_tree(time.monotonic())
        for chart in list(self.window.charts.values()):
            chart.close()
        x = self.window.items[1, "localPosition.x"]
        y = self.window.items[1, "localPosition.y"]
        x.setSelected(True)
        y.setSelected(True)
        self.window.tree.itemDoubleClicked.emit(x, 0)
        self.window.tree.itemDoubleClicked.emit(x, 0)
        self.window.tree.itemDoubleClicked.emit(x.parent(), 0)
        self.assertEqual(len(self.window.charts), 1)
        self.assertEqual(next(iter(self.window.charts.values())).fields, ["localPosition.x", "localPosition.y"])

    def test_detach_from_tabs_stays_inside_right_workspace(self):
        from PySide6 import QtWidgets
        a = self.window.add_chart(1, ["localPosition.x"])
        self.window.tabify_charts()
        self.window.stack_charts()
        self.assertEqual(self.window.workspace.viewMode(), QtWidgets.QMdiArea.ViewMode.SubWindowView)
        self.assertFalse(a.isWindow())
        self.assertIs(a.parentWidget(), self.window.workspace.viewport())
        self.assertFalse(any(button.text() == "Màn hình"
                             for button in a.findChildren(QtWidgets.QToolButton)))
        self.assertIs(self.window.add_chart(1, ["localPosition.x"]), a)
        self.assertIs(self.window.workspace.activeSubWindow(), a)
        self.assert_unique_curves()

    def test_split_scope_close_and_reopen_in_workspace(self):
        a = self.window.add_chart(1, ["localPosition.x", "localPosition.y"])
        a.detach()
        self.window.split_chart(a)
        self.assertEqual(len(self.window.charts), 2)
        self.assertTrue(all(c.mdiArea() is self.window.workspace and not c.isWindow()
                            for c in self.window.charts.values()))
        b = next(c for c in self.window.charts.values() if "localPosition.x" in c.fields)
        b.close()
        reopened = self.window.add_chart(1, ["localPosition.x"])
        self.assertIsNot(b, reopened)
        self.assert_unique_curves()
        self.assertFalse(self.window.stop.is_set())

    def test_sidebar_only_resizes_horizontally_and_scopes_cannot_undock(self):
        from PySide6 import QtCore, QtGui, QtTest
        self.window.show()
        self.qt.processEvents()
        sidebar = self.window.sidebar
        before = sidebar.geometry()
        splitter = self.window.splitter
        self.assertEqual(splitter.orientation(), QtCore.Qt.Orientation.Horizontal)
        handle = splitter.handle(1)
        center = handle.rect().center()
        QtTest.QTest.mousePress(handle, QtCore.Qt.MouseButton.LeftButton, pos=center)
        QtTest.QTest.mouseMove(handle, center+QtCore.QPoint(80, 0), delay=10)
        QtTest.QTest.mouseRelease(handle, QtCore.Qt.MouseButton.LeftButton,
                                  pos=center+QtCore.QPoint(80, 0))
        self.qt.processEvents()
        after = sidebar.geometry()
        self.assertGreater(after.width(), before.width())
        self.assertEqual((after.x(), after.y(), after.height()), (before.x(), before.y(), before.height()))
        a = self.window.add_chart(1, ["localPosition.x"])
        a.detach()
        a.resize(600, 400)
        a.move(40, 50)
        self.qt.processEvents()
        def drag_scope(start, delta):
            # QMdiSubWindow chooses its move/resize operation on hover before press.
            hover = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(start),
                                     QtCore.QPointF(a.mapToGlobal(start)),
                                     QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.NoButton,
                                     QtCore.Qt.KeyboardModifier.NoModifier)
            self.qt.sendEvent(a, hover)
            QtTest.QTest.mousePress(a, QtCore.Qt.MouseButton.LeftButton, pos=start)
            end = start+delta
            event = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(end),
                                     QtCore.QPointF(a.mapToGlobal(end)),
                                     QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.LeftButton,
                                     QtCore.Qt.KeyboardModifier.NoModifier)
            self.qt.sendEvent(a, event)
            QtTest.QTest.mouseRelease(a, QtCore.Qt.MouseButton.LeftButton, pos=end)
            self.qt.processEvents()
        old_position = a.pos()
        title_point = QtCore.QPoint(200, 14)
        drag_scope(title_point, QtCore.QPoint(50, 30))
        self.assertNotEqual(a.pos(), old_position)
        old_size = a.size()
        corner = a.rect().bottomRight()-QtCore.QPoint(1, 1)
        drag_scope(corner, QtCore.QPoint(30, 20))
        self.assertGreater(a.width(), old_size.width())
        self.assertGreater(a.height(), old_size.height())
        self.assertEqual(sidebar.geometry(), after)
        self.assertIs(a.parentWidget(), self.window.workspace.viewport())
        self.assertFalse(a.isWindow())
        a.showMaximized()
        self.qt.processEvents()
        self.assertEqual(sidebar.geometry(), after)
        self.assertIs(a.parentWidget(), self.window.workspace.viewport())
        a.showNormal()
        self.window.tabify_charts()
        self.assertEqual(sidebar.geometry(), after)

    def test_merge_by_drone_after_splitting_preserves_unique_curves(self):
        a = self.window.add_chart(1, ["localPosition.x", "localPosition.y", "localPosition.z"])
        self.window.split_chart(a)
        self.window.merge_by_drone()
        self.assertEqual(len(self.window.charts), 1)
        chart = next(iter(self.window.charts.values()))
        self.assertEqual(set(chart.fields), {"localPosition.x", "localPosition.y", "localPosition.z"})
        self.assertIn("localPosition.x", chart.windowTitle())
        self.assertIn("localPosition.y", chart.windowTitle())
        self.assertIn("localPosition.z", chart.windowTitle())
        self.assert_unique_curves()

    def test_zoom_buttons_hold_both_axes_through_live_updates_and_focus(self):
        now = time.monotonic()
        self.window.history.update(frame(now, 2.))
        chart = self.window.add_chart(1, ["localPosition.x"])
        plot = chart.plots["m"]
        plot.setRange(xRange=(0, 60), yRange=(-10, 10), padding=0)
        chart.zoom_in_button.click()
        zoomed = plot.viewRange()
        self.assertAlmostEqual(zoomed[0][1]-zoomed[0][0], 48.)
        self.assertAlmostEqual(zoomed[1][1]-zoomed[1][0], 16.)
        self.window.history.update(frame(now+.1, 999.))
        chart.render(self.window.history, now+.1, 60, True)
        self.window.add_chart(1, ["localPosition.x"])
        self.assertEqual(plot.viewRange(), zoomed)
        chart.zoom_out_button.click()
        self.assertAlmostEqual(plot.viewRange()[0][1]-plot.viewRange()[0][0], 60.)
        chart.live_button.click()
        self.assertFalse(chart.manual_view)
        self.assertEqual(plot.viewRange()[0], [0., 60.])
        self.assertTrue(plot.getViewBox().autoRangeEnabled()[1])

    def test_wheel_zoom_routes_to_plot_and_survives_next_frame(self):
        from PySide6 import QtCore, QtGui
        self.window.show()
        chart = self.window.add_chart(1, ["localPosition.x"])
        self.qt.processEvents()
        plot = chart.plots["m"]
        plot.setRange(xRange=(0, 60), yRange=(-10, 10), padding=0)
        point = plot.mapFromScene(plot.getViewBox().sceneBoundingRect().center())
        event = QtGui.QWheelEvent(QtCore.QPointF(point), QtCore.QPointF(plot.viewport().mapToGlobal(point)),
            QtCore.QPoint(), QtCore.QPoint(0, 120), QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        self.qt.sendEvent(plot.viewport(), event)
        self.assertTrue(chart.manual_view)
        bounds = plot.viewRange()
        self.assertLess(bounds[0][1]-bounds[0][0], 60.)
        self.window.history.update(frame(time.monotonic()))
        chart.render(self.window.history, time.monotonic(), 60, True)
        self.assertEqual(plot.viewRange(), bounds)

    def test_box_zoom_and_pan_mouse_gestures(self):
        from PySide6 import QtCore, QtGui, QtTest
        import pyqtgraph as pg
        self.window.show()
        chart = self.window.add_chart(1, ["localPosition.x"])
        self.qt.processEvents()
        plot = chart.plots["m"]
        plot.setRange(xRange=(0, 60), yRange=(-10, 10), padding=0)
        chart.box_zoom_button.click()
        self.assertEqual(plot.getViewBox().state["mouseMode"], pg.ViewBox.RectMode)
        self.qt.processEvents()

        def drag(start, end):
            QtTest.QTest.mousePress(plot.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
            # A first move starts the drag; the second supplies a changed position.
            for point in (start+(end-start)/2, end):
                event = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(point),
                    QtCore.QPointF(plot.viewport().mapToGlobal(point)), QtCore.Qt.MouseButton.NoButton,
                    QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
                self.qt.sendEvent(plot.viewport(), event)
            self.assertTrue(chart.manual_view)  # Freeze even while the mouse is still held.
            QtTest.QTest.mouseRelease(plot.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
            self.qt.processEvents()

        rect = plot.getViewBox().sceneBoundingRect()
        start = plot.mapFromScene(rect.topLeft()+QtCore.QPointF(rect.width()*.25, rect.height()*.25))
        end = plot.mapFromScene(rect.topLeft()+QtCore.QPointF(rect.width()*.75, rect.height()*.75))
        drag(start, end)
        self.assertTrue(chart.manual_view)
        bounds = plot.viewRange()
        self.assertLess(bounds[0][1]-bounds[0][0], 40.)
        self.assertLess(bounds[1][1]-bounds[1][0], 15.)
        chart.box_zoom_button.click()
        self.assertEqual(plot.getViewBox().state["mouseMode"], pg.ViewBox.PanMode)
        QtTest.QTest.qWait(self.qt.doubleClickInterval()+20)
        center = plot.mapFromScene(rect.center())
        drag(center, center+QtCore.QPoint(30, 15))
        self.assertNotEqual(plot.viewRange()[0], bounds[0])

    def test_fit_and_global_follow_controls(self):
        now = time.monotonic()
        self.window.history.update(frame(now, 2.))
        self.window.history.update(frame(now+.1, 4.))
        chart = self.window.add_chart(1, ["localPosition.x"])
        self.window.refresh_chart(chart)
        chart.fit_button.click()
        self.assertTrue(chart.manual_view)
        x, y = chart.plots["m"].viewRange()
        self.assertLessEqual(x[0], 0.)
        self.assertGreaterEqual(x[1], .1-1e-6)
        self.assertLessEqual(y[0], 2.)
        self.assertGreaterEqual(y[1], 4.)
        self.window.follow.setChecked(False)
        self.window.follow.setChecked(True)
        self.assertFalse(chart.manual_view)

    def test_selective_split_dialog_keeps_unselected_curves_together(self):
        from PySide6 import QtCore, QtWidgets
        chart = self.window.add_chart(1, ["localPosition.x", "localPosition.y", "localPosition.z"])
        chart.reset_at = time.monotonic()
        chart.zoom(.8)
        bounds = chart.plots["m"].viewRange()
        def choose():
            dialog = self.qt.activeModalWidget()
            choices = dialog.findChild(QtWidgets.QListWidget)
            choices.item(1).setCheckState(QtCore.Qt.CheckState.Checked)
            dialog.accept()
        QtCore.QTimer.singleShot(0, choose)
        chart.split_button.click()
        self.assertEqual(chart.fields, ["localPosition.x", "localPosition.z"])
        child = next(c for c in self.window.charts.values() if c is not chart)
        self.assertEqual(child.fields, ["localPosition.y"])
        self.assertEqual(child.reset_at, chart.reset_at)
        self.assertEqual(child.plots["m"].viewRange(), bounds)
        self.assertTrue(child.split_button.isHidden())
        self.assertTrue(child.remove_button.isHidden())
        self.assert_unique_curves()

    def test_selection_and_merge_partition_by_drone_and_unit(self):
        fields = ["vehicle.roll", "vehicle.pitch", "vehicle.yaw", "battery.voltage"]
        with patch.object(self.window.tree, "selected_fields", return_value={1: fields, 2: fields}):
            self.window.add_selected()
        self.assertEqual(len(self.window.charts), 4)
        attitude = next(c for c in self.window.charts.values() if c.system_id == 1 and "vehicle.roll" in c.fields)
        self.assertEqual(attitude.fields, fields[:3])
        self.window.split_chart(attitude)
        self.window.merge_by_drone()
        self.assertEqual(len(self.window.charts), 4)
        self.assertTrue(all(len({UNITS[f] for f in c.fields}) == 1 for c in self.window.charts.values()))
        self.assert_unique_curves()


@unittest.skipUnless(HAS_QT, "Install .[plots] for process integration tests")
class PlotProcessTests(unittest.TestCase):
    def test_plot_lifecycle_does_not_stop_telemetry_or_control(self):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        with tempfile.TemporaryDirectory() as directory:
            app = App(Config.default(), directory)
            try:
                app.connect("sim")
                app.wait_for_vehicles(3)
                self.assertTrue(app.plots.open()["running"])
                wait_until(lambda: app.registry.snapshot()[1].landed is not None)
                before = app.registry.snapshot()[1].messages
                self.assertEqual(app.command("arm", [1])["status"], "accepted")
                wait_until(lambda: app.registry.snapshot()[1].messages > before+5)
                app.plots.close()
                self.assertFalse(app.plots.status()["running"])
                self.assertIsNotNone(app.transport)
                self.assertTrue(app.registry.snapshot()[1].armed)
                self.assertTrue(app.plots.open()["running"])
            finally:
                app.close()
            self.assertFalse(app.plots.status()["running"])
