from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPixmap
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
                              QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
from .theme import current_colors

STATUS_COLORS = {"NORMAL": "#249873", "ATTENTION": "#c18724", "ABNORMAL": "#d15c67",
                 "OFFLINE": "#8795a6", "INFO": "#148a87", "WARNING": "#c18724", "CRITICAL": "#d15c67"}


class DogMascot(QLabel):
    """Decorative brand companion, independent of the monitored pet's profile."""
    def __init__(self, width=64, height=76):
        super().__init__()
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAccessibleName("Realistic brown dog mascot")
        self.setToolTip("Your PetCare companion")
        path = Path(__file__).resolve().parents[1] / "resources" / "brown-dog-mascot.png"
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            self.setPixmap(pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))


def label(text, role=None, wrap=False):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(wrap or role in ("title", "sectionTitle", "metric"))
    return widget


def button(text, callback=None, primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName("primary")
    if callback:
        widget.clicked.connect(callback)
    return widget


class Card(QFrame):
    def __init__(self, title=None):
        super().__init__()
        self.setObjectName("card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(22, 20, 22, 20)
        self.box.setSpacing(12)
        if title:
            self.box.addWidget(label(title, "sectionTitle"))


class Page(QWidget):
    def __init__(self, title, subtitle):
        super().__init__()
        self.setObjectName("page")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(30, 26, 30, 24)
        self.box.setSpacing(22)
        heading = QVBoxLayout()
        heading.setSpacing(6)
        self.heading = label(title, "title")
        heading.addWidget(self.heading)
        heading.addWidget(label(subtitle, "muted", True))
        self.box.addLayout(heading)


class Metric(Card):
    def __init__(self, name, unit=""):
        super().__init__()
        self.unit = unit
        self.box.addWidget(label(name.upper(), "eyebrow"))
        self.value = label("—", "metric")
        self.caption = label("Normal", "muted", True)
        self.box.addWidget(self.value)
        self.box.addWidget(self.caption)


class WellnessRing(QWidget):
    def __init__(self):
        super().__init__()
        self.score = 0
        self.status = "NORMAL"
        self.setMinimumSize(205, 205)
        self.setMaximumHeight(235)

    def set_result(self, result):
        self.score, self.status = result.score, result.status
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = min(self.width(), self.height()) - 26
        rect = QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)
        colors = current_colors()
        painter.setPen(QPen(QColor(colors["border"]), 12))
        painter.drawArc(rect, 0, 360 * 16)
        painter.setPen(QPen(QColor(STATUS_COLORS[self.status]), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 90 * 16, -round(self.score / 100 * 360 * 16))
        painter.setPen(self.palette().windowText().color())
        painter.setFont(QFont("Segoe UI", 39, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(0, -13, 0, -13), Qt.AlignmentFlag.AlignCenter, str(self.score))
        painter.setFont(QFont("Segoe UI", 11))
        painter.setPen(QColor(colors["muted"]))
        painter.drawText(rect.adjusted(0, 74, 0, 0), Qt.AlignmentFlag.AlignCenter, "out of 100")


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().hide()
    widget.setShowGrid(False)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setMinimumHeight(210)
    widget.verticalHeader().setDefaultSectionSize(44)
    return widget


def fill_table(widget, rows, ids=None):
    selected = widget.item(widget.currentRow(), 0)
    identifier = selected.data(Qt.ItemDataRole.UserRole) if selected else None
    widget.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            if j == 0 and ids:
                item.setData(Qt.ItemDataRole.UserRole, ids[i])
            if str(value) in STATUS_COLORS:
                item.setForeground(QColor(STATUS_COLORS[str(value)]))
            widget.setItem(i, j, item)
        if ids and ids[i] == identifier:
            widget.selectRow(i)


def time_text(timestamp):
    return timestamp.replace("T", " ")
