"""Generous resize targets for workspace scopes, without covering title-bar buttons."""
from PySide6 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt


class ScopeResizeHandle(QtWidgets.QWidget):
    def __init__(self, scope, edges):
        super().__init__(scope)
        self.scope, self.edges = scope, edges
        self.anchor = self.original = None
        self.hovered = False
        cursor = (Qt.CursorShape.SizeFDiagCursor if edges in ("lt", "rb") else
                  Qt.CursorShape.SizeBDiagCursor if edges in ("rt", "lb") else
                  Qt.CursorShape.SizeHorCursor if edges in ("l", "r") else Qt.CursorShape.SizeVerCursor)
        self.setCursor(cursor)
        self.setToolTip("Giữ và kéo để co giãn ô đồ thị")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.anchor = event.globalPosition().toPoint()
            self.original = QtCore.QRect(self.scope.geometry())
            self.scope.mdiArea().setActiveSubWindow(self.scope)
            self.scope.raise_()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.anchor is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        delta = event.globalPosition().toPoint()-self.anchor
        rect = QtCore.QRect(self.original)
        minimum = self.scope.minimumSize().expandedTo(self.scope.minimumSizeHint())
        if "l" in self.edges:
            rect.setLeft(min(max(0, rect.left()+delta.x()), rect.right()-minimum.width()+1))
        if "r" in self.edges:
            rect.setRight(max(rect.left()+minimum.width()-1, rect.right()+delta.x()))
        if "t" in self.edges:
            rect.setTop(min(max(0, rect.top()+delta.y()), rect.bottom()-minimum.height()+1))
        if "b" in self.edges:
            rect.setBottom(max(rect.top()+minimum.height()-1, rect.bottom()+delta.y()))
        self.scope.setGeometry(rect)
        event.accept()

    def mouseReleaseEvent(self, event):
        self.anchor = self.original = None
        event.accept()

    def enterEvent(self, event):
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#31566b" if self.hovered else "#20324a"))
        if len(self.edges) == 2:
            painter.setPen(QtGui.QPen(QtGui.QColor("#46d9b5"), 2))
            # Bottom corners are large, visible grips. Top corners stay clear of controls.
            if self.edges in ("rb", "lb"):
                if self.edges == "lb":
                    painter.translate(self.width(), 0)
                    painter.scale(-1, 1)
                for inset in (6, 12, 18):
                    painter.drawLine(self.width()-inset, self.height()-4,
                                     self.width()-4, self.height()-inset)


def layout_resize_handles(scope):
    if not hasattr(scope, "resize_handles"):
        return
    area = scope.mdiArea()
    enabled = (area is not None and not scope.isMaximized() and not scope.isMinimized() and
               area.viewMode() == QtWidgets.QMdiArea.ViewMode.SubWindowView)
    w, h = scope.width(), scope.height()
    rectangles = {
        "l": (0, 32, 12, max(0, h-60)), "r": (w-12, 32, 12, max(0, h-60)),
        "t": (24, 0, max(0, w-124), 8), "b": (28, h-12, max(0, w-56), 12),
        "lt": (0, 0, 12, 12), "rt": (w-12, 0, 12, 12),
        "lb": (0, h-28, 28, 28), "rb": (w-28, h-28, 28, 28),
    }
    for edges, handle in scope.resize_handles.items():
        handle.setGeometry(*rectangles[edges])
        handle.setVisible(enabled)
        handle.raise_()
