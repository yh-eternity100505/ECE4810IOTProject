"""Outline navigation icons rendered sharply at every display scale."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton
from .theme import current_colors

ICONS = {
    "Sensor Hub": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/>',
    "Overview": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "Live Sensors": '<circle cx="12" cy="12" r="1.5" fill="COLOR"/><path d="M7 7a7 7 0 0 0 0 10m10-10a7 7 0 0 1 0 10M3.5 3.5a12 12 0 0 0 0 17m17-17a12 12 0 0 1 0 17"/>',
    "History": '<path d="M3 10a9 9 0 1 1 1 7M2 5v5h5m5-4v6l4 3"/>',
    "Alerts": '<path d="M5 10a7 7 0 0 1 14 0c0 7 3 7 3 8H2c0-1 3-1 3-8m4 11a3 3 0 0 0 6 0M12 1v2"/>',
    "Pet Profile": '<ellipse cx="5" cy="9" rx="2.2" ry="3"/><ellipse cx="10" cy="5" rx="2.2" ry="3"/><ellipse cx="16" cy="5" rx="2.2" ry="3"/><ellipse cx="21" cy="10" rx="2" ry="3"/><path d="M6 18c0-2 2-3 3-5 2-3 5-3 7 0 1 2 3 3 3 5 0 5-5 2-6 2s-7 3-7-2Z"/>',
    "Security Inspector": '<path d="m12 2 9 4v7c0 5-9 9-9 9s-9-4-9-9V6Zm-5 10 3 3 7-7"/>',
    "Settings": '<path d="m9 2-.5 3-2 .9L4 4.5 1.5 9l2.5 1.5v3L1.5 15 4 19.5l2.5-1.4 2 .9.5 3h6l.5-3 2-.9 2.5 1.4 2.5-4.5-2.5-1.5v-3L22.5 9 20 4.5l-2.5 1.4-2-.9L15 2Z"/><circle cx="12" cy="12" r="4"/>',
}

class NavigationButton(QPushButton):
    def __init__(self, title, callback):
        super().__init__(title)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(52)
        self.clicked.connect(callback)

    def paintEvent(self, event):
        super().paintEvent(event)
        color = current_colors()["sidebar_text"]
        shapes = ICONS[self.text()].replace("COLOR", color)
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{shapes}</svg>'
        painter = QPainter(self)
        QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(18, (self.height()-25)/2, 25, 25))
        painter.end()
