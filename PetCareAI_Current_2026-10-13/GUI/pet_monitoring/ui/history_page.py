from datetime import date
from PySide6.QtWidgets import QHBoxLayout, QComboBox, QTabWidget
import pyqtgraph as pg
from .widgets import Page, Card, Metric, label, button
from .theme import DARK, LIGHT


class HistoryPage(Page):
    METRICS = {"sleep": ("Sleep", "hours"), "activity": ("Activity", "hours"), "food": ("Food", "visits"),
               "water": ("Water", "visits"), "litter": ("Litter", "visits")}

    def __init__(self, controller):
        super().__init__("Behaviour history", "Explore daily trends and compare routines against the normal baseline.")
        self.c = controller
        controls = QHBoxLayout()
        self.source = label("SIMULATED HISTORY", "eyebrow")
        controls.addWidget(self.source)
        controls.addStretch()
        self.period = QComboBox()
        self.period.addItems(["Today", "7 Days", "30 Days"])
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.period)
        controls.addWidget(button("Refresh history", self.refresh))
        self.box.addLayout(controls)
        self.tabs = QTabWidget()
        self.plots = {}
        for key, (name, unit) in self.METRICS.items():
            plot = pg.PlotWidget()
            plot.setMinimumHeight(310)
            plot.setLabel("left", name, units=unit)
            plot.showGrid(x=False, y=True, alpha=.13)
            plot.setMouseEnabled(x=False, y=False)
            plot.hideButtons()
            self.tabs.addTab(plot, name)
            self.plots[key] = plot
        self.tabs.currentChanged.connect(self.refresh_comparison)
        self.box.addWidget(self.tabs)
        comparison = QHBoxLayout()
        self.today = Metric("Today's value")
        self.baseline = Metric("Normal daily baseline")
        self.difference = Metric("Difference")
        for card in (self.today, self.baseline, self.difference):
            comparison.addWidget(card)
        self.box.addLayout(comparison)
        self.note = label("", "muted", True)
        self.box.addWidget(self.note)
        self.box.addStretch()
        controller.settings_changed.connect(self.refresh)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        days = [1, 7, 30][self.period.currentIndex()]
        summaries = self.c.db.summaries(days, "demo" if self.c.settings["mock_mode"] else "live")
        # Overlay the current state without requiring a database write on every repaint.
        summaries = [s for s in summaries if s.date != self.c.summary.date] + [self.c.summary]
        summaries.sort(key=lambda s: s.date)
        colors = DARK if self.c.settings["theme"] == "Dark" else LIGHT
        baselines = self.c.profile.baselines()
        kinds = {s.source for s in summaries}
        self.source.setText("SIMULATED HISTORY" if kinds == {"demo"} else "LIVE HISTORY" if kinds == {"live"} else "MIXED HISTORY · DEMO + LIVE")
        for key, plot in self.plots.items():
            plot.clear()
            plot.setBackground(colors["card"])
            for axis in ("left", "bottom"):
                plot.getAxis(axis).setTextPen(colors["muted"])
                plot.getAxis(axis).setPen(colors["border"])
            ticks = [(i, date.fromisoformat(s.date).strftime("%d %b")) for i, s in enumerate(summaries)]
            step = max(1, len(ticks) // 8)
            plot.getAxis("bottom").setTicks([ticks[::step]])
            x = list(range(len(summaries)))
            y = [getattr(s, key) for s in summaries]
            plot.plot(x, y, pen=pg.mkPen(colors["accent"], width=3), symbol="o", symbolSize=7,
                      symbolBrush=colors["accent"], symbolPen=None)
            plot.addItem(pg.InfiniteLine(pos=baselines[key], angle=0,
                         pen=pg.mkPen(colors["secondary"], width=2, style=pg.QtCore.Qt.PenStyle.DashLine)))
            plot.setXRange(-.4, max(.6, len(summaries) - .6), padding=0)
            plot.setYRange(0, max(baselines[key], max(y, default=0)) * 1.25, padding=0)
        self.note.setText("Bronze: observed daily totals. Dashed line: normal daily baseline. Today's totals are incomplete. Demo data is illustrative; activity and sleep durations are estimates from station presence.")
        self.refresh_comparison()

    def refresh_comparison(self):
        key = list(self.METRICS)[self.tabs.currentIndex()]
        unit = self.METRICS[key][1]
        value, baseline = getattr(self.c.summary, key), self.c.profile.baselines()[key]
        delta = (value - baseline) / baseline if baseline else 0
        self.today.value.setText(f"{value:.1f}" if unit == "hours" else f"{value:g}")
        self.today.caption.setText(unit + " · today so far")
        self.baseline.value.setText(f"{baseline:g}")
        self.baseline.caption.setText(unit + " / day")
        self.difference.value.setText(f"{'↑' if delta > 0 else '↓' if delta < 0 else '→'} {delta:+.0%}")
        self.difference.caption.setText("Normal" if abs(delta) <= self.c.settings["anomaly_threshold"] else "Higher than normal" if delta > 0 else "Lower than normal")
