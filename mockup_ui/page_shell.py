"""Shared outer geometry for both weapon-detection pages."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QSplitter, QVBoxLayout, QWidget


class DetectionPageShell(QSplitter):
    def __init__(self, name):
        super().__init__(Qt.Orientation.Horizontal)
        self.setObjectName(name)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(1)

    def finish(self):
        self.setSizes([290, 840, 320])
        for index, stretch in enumerate((0, 1, 0)):
            self.setStretchFactor(index, stretch)


def page_panel(kind):
    panel = QFrame()
    panel.setObjectName(kind)
    layout = QVBoxLayout(panel)
    if kind == "sidePanel":
        # The outer frame consumes space; scroll the controls instead of clipping them.
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("sidebarScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.verticalScrollBar().setFixedWidth(8)
        content = QWidget()
        content.setObjectName("sidebarContent")
        scroll.setWidget(content)
        layout.addWidget(scroll)
        layout = QVBoxLayout(content)
        panel.setMinimumWidth(270)
        panel.setMaximumWidth(310)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)
    elif kind == "summaryPanel":
        panel.setMinimumWidth(275)
        panel.setMaximumWidth(390)
        layout.setContentsMargins(17, 19, 17, 16)
        layout.setSpacing(10)
    else:
        layout.setContentsMargins(14, 16, 14, 15)
        layout.setSpacing(10)
    return panel, layout


def workspace_heading(title, status):
    row = QHBoxLayout()
    title.setWordWrap(False)
    title.setFixedHeight(22)
    status.setFixedHeight(22)
    row.addWidget(title)
    row.addStretch()
    row.addWidget(status)
    return row


def summary_metrics():
    row = QHBoxLayout()
    row.setSpacing(6)
    values = []
    for caption in ("DETECTIONS", "HANDGUNS", "KNIVES"):
        card = QFrame()
        card.setObjectName("metricCard")
        card.setFixedHeight(64)
        box = QVBoxLayout(card)
        box.setContentsMargins(5, 8, 5, 8)
        box.setSpacing(1)
        value, title = QLabel("—"), QLabel(caption)
        value.setObjectName("metricNumber")
        title.setObjectName("metricLabel")
        for item in (value, title):
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(item)
        row.addWidget(card, 1)
        values.append(value)
    return row, values


def enhancement_heading():
    frame = QFrame()
    frame.setObjectName("cameraEnhancementHeading")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(11, 9, 11, 9)
    layout.setSpacing(5)
    top = QHBoxLayout()
    title, engine = QLabel("Enhancement"), QLabel("BasicVSR++")
    title.setObjectName("cameraEnhancementTitle")
    engine.setObjectName("cameraEnhancementEngine")
    top.addWidget(title, 1)
    top.addWidget(engine)
    layout.addLayout(top)
    return frame, layout
