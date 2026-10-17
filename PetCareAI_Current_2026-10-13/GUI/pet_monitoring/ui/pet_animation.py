"""Small native Qt cartoons. Decorative animation never produces sensor data."""
import math
from PySide6.QtCore import Qt, QTimer, QRectF, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QFont
from PySide6.QtWidgets import QWidget


class PetAnimation(QWidget):
    def __init__(self, mode="sleep", parent=None):
        super().__init__(parent)
        self.mode, self.frame, self.active = mode, 0, False
        self.setMinimumHeight(110)
        self.setMaximumHeight(135)
        self.setAccessibleName(f"Decorative animated {mode} pet illustration")
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.animate)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and getattr(self, "compact_card", None) is watched:
            self.move(max(0, watched.width() - self.width() - 18), 12)
        return super().eventFilter(watched, event)

    def animate(self):
        self.frame += 1
        self.update()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def set_active(self, active):
        self.active = active
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / 250, self.height() / 120)
        p.translate((self.width() - 250 * scale) / 2, (self.height() - 120 * scale) / 2)
        p.scale(scale, scale)
        t = self.frame / 8
        colors = {"sleep": "#D9D0FF", "eat": "#FFD1D9", "water": "#BAE9F1", "toilet": "#D4EBCF", "gesture": "#FFE4AF"}
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(colors.get(self.mode, colors["gesture"])))
        p.drawEllipse(QRectF(35, 14, 180, 97))
        p.setBrush(QColor("#FFFFFF"))
        for x, y in ((30, 35), (218, 26), (205, 95)):
            p.drawEllipse(QRectF(x, y + math.sin(t + x) * 3, 7, 7))
        if self.mode == "sleep":
            p.setBrush(QColor("#AB99DD"))
            p.drawRoundedRect(QRectF(38, 76, 166, 28), 14, 14)
            self.dog(p, 95, 69, self.active, math.sin(t) * 1.5)
            p.setPen(QColor("#7360AA"))
            p.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
            for index in range(3):
                p.drawText(170 + index * 15, int(51 - index * 14 - 4 * math.sin(t + index)), "z" if self.active else "·")
        elif self.mode in {"eat", "water"}:
            self.dog(p, 90, 51 + math.sin(t) * 2, False, 0)
            bowl_color = "#EF889D" if self.mode == "eat" else "#5ABACA"
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(bowl_color))
            p.drawRoundedRect(QRectF(112, 78, 88, 26), 12, 12)
            p.setBrush(QColor("#FFF2D7") if self.mode == "eat" else QColor("#BBF4FF"))
            p.drawEllipse(QRectF(115, 72, 82, 16))
            if self.mode == "eat":
                p.setBrush(QColor("#B8794A"))
                for x, y in ((133, 79), (149, 77), (165, 81), (180, 77)):
                    p.drawEllipse(QRectF(x, y, 7, 5))
            else:
                path = QPainterPath()
                y = 27 + math.sin(t) * 5
                path.moveTo(181, y)
                path.cubicTo(155, y + 31, 203, y + 31, 181, y)
                p.setBrush(QColor("#59BDD8"))
                p.drawPath(path)
        elif self.mode == "toilet":
            p.setBrush(QColor("#8FBA98"))
            p.drawRoundedRect(QRectF(43, 79, 160, 25), 12, 12)
            p.setBrush(QColor("#F3E4C5"))
            p.drawEllipse(QRectF(50, 73, 145, 15))
            self.dog(p, 113, 55 + math.sin(t) * 2, False, 0)
            p.setPen(QColor("#508F70"))
            p.setFont(QFont("Segoe UI", 23))
            p.drawText(179, int(52 + math.sin(t) * 4), "✧")
        else:
            self.dog(p, 117, 64 + math.sin(t) * 3, False, 0)
            p.setPen(QColor("#B47B3E"))
            p.setFont(QFont("Segoe UI", 23))
            p.drawText(184, int(45 + math.sin(t) * 5), "♡")
        p.end()

    def dog(self, p, x, y, sleeping, breath):
        p.save()
        p.translate(x, y)
        p.setPen(QPen(QColor("#AA7756"), 2))
        p.setBrush(QColor("#F3C795"))
        p.drawEllipse(QRectF(-45, -10 - breath, 78, 39 + breath))
        p.setBrush(QColor("#FFF3DC"))
        p.drawEllipse(QRectF(-19, 13, 28, 13))
        p.setBrush(QColor("#C8885B"))
        p.drawEllipse(QRectF(-52, -35, 22, 38))
        p.drawEllipse(QRectF(2, -35, 22, 38))
        p.setBrush(QColor("#F8D8A9"))
        p.drawEllipse(QRectF(-41, -39, 58, 53))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFF6E8"))
        p.drawEllipse(QRectF(-29, -12, 35, 21))
        p.setBrush(QColor("#EB9D9A"))
        p.drawEllipse(QRectF(-35, -12, 10, 6))
        p.drawEllipse(QRectF(0, -12, 10, 6))
        p.setPen(QPen(QColor("#61483E"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        blink = self.frame % 60 in (0, 1, 2)
        if sleeping or blink:
            p.drawArc(QRectF(-30, -23, 11, 9), 180 * 16, 180 * 16)
            p.drawArc(QRectF(-5, -23, 11, 9), 180 * 16, 180 * 16)
        else:
            p.drawPoint(-25, -18)
            p.drawPoint(0, -18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#61483E"))
        p.drawEllipse(QRectF(-17, -9, 10, 7))
        p.restore()


def compact_animation(card, animation):
    """Place a small illustration in existing card space, without growing its layout."""
    animation.setParent(card)
    animation.setFixedSize(88, 42)
    animation.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    animation.compact_card = card
    card.installEventFilter(animation)
    animation.move(max(0, card.width() - animation.width() - 18), 12)
    animation.show()
