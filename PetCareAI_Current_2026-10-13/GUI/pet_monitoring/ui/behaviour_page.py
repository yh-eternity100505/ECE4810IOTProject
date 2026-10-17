from PySide6.QtWidgets import QHBoxLayout, QProgressBar
from .widgets import Page, Card, label, table, fill_table, time_text


class BehaviourPage(Page):
    def __init__(self, controller):
        super().__init__("Behaviour AI", "Live classification with confidence, model performance, and inference history.")
        self.c = controller
        self.signature = None
        row = QHBoxLayout()
        current = Card("Current classification")
        self.state = label("● DEMO INFERENCE RUNNING", "eyebrow")
        self.classification = label("Normal Sleep", "metric")
        self.confidence = label("Confidence · 92%", "sectionTitle")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.model = label("Model: KNN (demo)", "muted")
        for widget in (self.state, self.classification, self.confidence, self.progress, self.model):
            current.box.addWidget(widget)
        row.addWidget(current, 3)
        latency = Card("Inference latency")
        self.latency = label("28 ms", "metric")
        latency.box.addWidget(self.latency)
        latency.box.addWidget(label("Demo confidence and latency are simulated. No trained model is loaded.", "muted", True))
        latency.box.addStretch()
        row.addWidget(latency, 2)
        self.box.addLayout(row)
        history = Card("Classification history")
        self.history = table(["Time", "Classification", "Confidence", "Model", "Latency"])
        history.box.addWidget(self.history)
        self.box.addWidget(history)
        performance = Card("Model performance · example evaluation")
        self.performance = table(["Model", "Accuracy", "Precision", "Recall", "F1 score", "Inference"])
        self.performance.setMinimumHeight(180)
        performance.box.addWidget(self.performance)
        performance.box.addWidget(label("Illustrative demo metrics; replace with results from your held-out evaluation dataset.", "muted", True))
        self.box.addWidget(performance)
        self.box.addStretch()
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        event = self.c.latest_event
        self.classification.setText(event.classification)
        self.confidence.setText(f"Confidence · {event.confidence:.0%}")
        self.progress.setValue(round(event.confidence * 100))
        self.model.setText("Model: " + event.model_name)
        self.latency.setText(f"{event.inference_time_ms:g} ms")
        self.state.setText("● DEMO INFERENCE RUNNING" if event.model_name != "Unavailable" else "● ML MODEL UNAVAILABLE")
        signature = [(e.timestamp, e.classification) for e in self.c.events]
        if signature != self.signature:
            self.signature = signature
            fill_table(self.history, [(time_text(e.timestamp), e.classification, f"{e.confidence:.0%}", e.model_name, f"{e.inference_time_ms:g} ms") for e in self.c.events])
        fill_table(self.performance, [(p["model"], f"{p['accuracy']}%", f"{p['precision']}%", f"{p['recall']}%", f"{p['f1']}%", f"{p['latency']} ms") for p in getattr(self.c.ml, "performance", [])])
