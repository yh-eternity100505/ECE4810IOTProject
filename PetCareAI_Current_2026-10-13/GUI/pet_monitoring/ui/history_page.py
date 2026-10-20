from PySide6.QtWidgets import QHBoxLayout, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView
from .widgets import Page, label, button


class HistoryPage(Page):
    def __init__(self, controller, change_log):
        super().__init__("History", "Phone-app setting changes and pet activity, saved as a chronological summary.")
        self.c, self.log = controller, change_log
        controls = QHBoxLayout()
        self.period = QComboBox()
        self.period.addItems(["Today", "7 Days", "30 Days", "All time"])
        self.period.setCurrentIndex(1)
        self.category = QComboBox()
        self.category.addItems(["All", "Phone settings", "Activity", "Entrance"])
        controls.addWidget(self.period)
        controls.addWidget(self.category)
        controls.addStretch()
        controls.addWidget(button("Refresh history", self.refresh))
        self.box.addLayout(controls)
        self.note = label("Changes are timestamped when observed from the Pi hub. Settings snapshots cannot reconstruct intermediate edits made while disconnected. Repeated readings are not logged.", "muted", True)
        self.box.addWidget(self.note)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Observed", "Category", "Summary"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.setMinimumHeight(400)
        self.box.addWidget(self.table)
        self.empty = label("No changes recorded yet. Connect the Pi and edit your phone-app settings, or wait for a completed zone visit.", "muted", True)
        self.box.addWidget(self.empty)
        self.period.currentIndexChanged.connect(self.refresh)
        self.category.currentIndexChanged.connect(self.refresh)
        self.log.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        rows = self.log.rows([1, 7, 30, 36500][self.period.currentIndex()], self.category.currentText())
        self.table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            for column, value in enumerate(entry):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.table.resizeRowsToContents()
        self.empty.setVisible(not rows)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
