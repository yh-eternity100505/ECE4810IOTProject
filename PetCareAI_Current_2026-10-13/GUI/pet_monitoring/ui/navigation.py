"""Outline navigation icons rendered sharply at every display scale."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton
from .theme import current_colors

ICONS = {
    "Door Authentication": '<path d="M4 22V2h13v20M2 22h19M7 22V5l10-3"/><circle cx="13" cy="13" r=".8" fill="COLOR"/>',
    "Sensor Hub": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M8 2v4m4-4v4m4-4v4M8 18v4m4-4v4m4-4v4M2 8h4m-4 4h4m-4 4h4M18 8h4m-4 4h4m-4 4h4"/>',
    "Overview": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "Live Sensors": '<circle cx="12" cy="12" r="1.5" fill="COLOR"/><path d="M7 7a7 7 0 0 0 0 10m10-10a7 7 0 0 1 0 10M3.5 3.5a12 12 0 0 0 0 17m17-17a12 12 0 0 1 0 17"/>',
    "History": '<path d="M3 10a9 9 0 1 1 1 7M2 5v5h5m5-4v6l4 3"/>',
    "Alerts": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 10h18c0-3-3-3-3-10M10 21a2 2 0 0 0 4 0M12 1v1"/>',
    "Pet Profile": '<ellipse cx="4" cy="10" rx="2" ry="2.8"/><ellipse cx="9" cy="5" rx="2" ry="2.8"/><ellipse cx="15" cy="5" rx="2" ry="2.8"/><ellipse cx="20" cy="10" rx="2" ry="2.8"/><path d="M5 18c0-2 2-3 3-5 2-3 6-3 8 0 1 2 3 3 3 5 0 5-5 2-7 2s-7 3-7-2Z"/>',
    "Security Inspector": '<path d="m12 2 9 4v7c0 5-9 9-9 9s-9-4-9-9V6Zm-5 10 3 3 7-7"/>',
    "Settings": '<path d="m9 2-.5 3-2 .9L4 4.5 1.5 9l2.5 1.5v3L1.5 15 4 19.5l2.5-1.4 2 .9.5 3h6l.5-3 2-.9 2.5 1.4 2.5-4.5-2.5-1.5v-3L22.5 9 20 4.5l-2.5 1.4-2-.9L15 2Z"/><circle cx="12" cy="12" r="4"/>',
}

class NavigationButton(QPushButton):
    def __init__(self, title, callback):
        super().__init__(title)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("outlineNav", True)
        self.setMinimumHeight(42)
        self.clicked.connect(callback)

    def paintEvent(self, event):
        super().paintEvent(event)
        color = current_colors()["sidebar_text"]
        shapes = ICONS[self.text()].replace("COLOR", color)
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{shapes}</svg>'
        painter = QPainter(self)
        QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(16, (self.height()-16)/2, 16, 16))
        painter.end()
