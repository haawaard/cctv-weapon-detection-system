"""Multi-recording workspace built on the same full-video detector as the single view."""
from hashlib import sha256
from pathlib import Path
from time import monotonic
import traceback

from PySide6.QtCore import QEvent, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QProgressBar, QScrollArea, QSlider, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QSizePolicy)

from mockup_ui.model_bridge import ModelBridge, read_video, timecode
from mockup_ui.multi_camera import Alignment, CameraSource, build_timeline, export_session, validate_session
from mockup_ui.observation_review import ReviewStore
from mockup_ui.report_metrics import calculate_report_metrics
from mockup_ui.review_panel import ObservationReviewDialog
from mockup_ui.video_player import VideoPlayer
from mockup_ui.page_shell import DetectionPageShell, page_panel, workspace_heading, summary_metrics, enhancement_heading
from mockup_ui.ui_theme import load_stylesheet
from mockup_ui.preferences import load_preferences, export_start_directory

VIDEO_FILTER = "Videos (*.mp4 *.avi *.mov *.mkv *.webm *.m4v)"


def label(text, name="dialogDetail"):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def session_time(seconds):
    return ("-" if seconds < 0 else "") + timecode(abs(seconds))


class CameraBatchWorker(QThread):
    progress = Signal(str, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, sources, bridge, threshold, parent=None):
        super().__init__(parent)
        self.sources, self.bridge, self.threshold = sources, bridge, threshold

    def run(self):
        try:
            results = []
            total_frames = max(1, sum(source.video.frame_count for source in self.sources))
            completed_frames = 0
            for index, source in enumerate(self.sources):
                def progress(message, percent):
                    self.progress.emit(f"{source.camera_id} · {index + 1}/{len(self.sources)} cameras · {message}",
                                       round((completed_frames + source.video.frame_count *
                                              min(100, max(0, percent)) / 100) * 100 / total_frames))
                result = self.bridge.analyze_video(source.video, self.threshold, progress, camera_id=source.camera_id)
                ReviewStore.for_result(result)
                results.append(result)
                completed_frames += source.video.frame_count
            self.succeeded.emit(results)
        except Exception:
            self.failed.emit(traceback.format_exc())


class CameraSetupDialog(QDialog):
    """Keeps technical correspondence fields available without crowding the workspace."""
    def __init__(self, sources, alignment, parent=None):
        super().__init__(parent)
        self.sources = sources
        self.alignment = alignment
        self.editors = []
        self.setWindowTitle("Camera and timing details")
        self.setObjectName("cameraSetupDialog")
        self.resize(620, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.addWidget(label("CAMERA & TIMING DETAILS", "sectionTitle"))
        layout.addWidget(label("Use offsets only when recordings did not begin at the same incident time.", "dialogHeading"))
        layout.addWidget(label("Session time = video time + offset. Camera IDs must be unique."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        stack = QVBoxLayout(content)
        for source in sources:
            card = QFrame()
            card.setObjectName("configCard")
            box = QVBoxLayout(card)
            box.addWidget(label(source.video.path.name, "detectionName"))
            identifier = QLineEdit(source.camera_id)
            identifier.setPlaceholderText("Camera ID")
            location = QLineEdit(source.location)
            location.setPlaceholderText("Viewpoint / location (optional)")
            offset = QDoubleSpinBox()
            offset.setRange(-86400, 86400)
            offset.setDecimals(3)
            offset.setSuffix(" s")
            offset.setValue(source.offset_seconds)
            offset.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            box.addWidget(label("Camera ID"))
            box.addWidget(identifier)
            box.addWidget(label("Viewpoint / location"))
            box.addWidget(location)
            timing = QHBoxLayout()
            timing.addWidget(label("Start offset"))
            timing.addWidget(offset)
            box.addLayout(timing)
            stack.addWidget(card)
            self.editors.append((identifier, location, offset))
        stack.addWidget(label("ALIGNMENT OPTIONS", "sectionTitle"))
        self.method = QLineEdit(alignment.method)
        self.method.setPlaceholderText("e.g. shared clock or visible timer at 00:12")
        stack.addWidget(label("Alignment reference"))
        stack.addWidget(self.method)
        row = QHBoxLayout()
        row.addWidget(label("Matching tolerance"))
        self.window = QDoubleSpinBox()
        self.window.setRange(.05, 60)
        self.window.setDecimals(2)
        self.window.setSuffix(" s")
        self.window.setValue(alignment.window_seconds)
        self.window.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        row.addWidget(self.window)
        stack.addLayout(row)
        stack.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate_and_accept(self):
        identifiers = [editor[0].text().strip() for editor in self.editors]
        if any(not value for value in identifiers) or len(set(identifiers)) != len(identifiers):
            QMessageBox.warning(self, "Check camera IDs", "Each camera needs a different, non-empty ID.")
            return
        self.accept()

    def apply(self):
        for source, (identifier, location, offset) in zip(self.sources, self.editors, strict=True):
            source.camera_id = identifier.text().strip()
            source.location = location.text().strip()
            source.offset_seconds = offset.value()
        self.alignment.method = self.method.text().strip()
        self.alignment.window_seconds = self.window.value()


class CameraPreviewDialog(QDialog):
    def __init__(self, path, title, frame_number=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Camera preview · {title}")
        self.setObjectName("cameraPreviewDialog")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1120, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.addWidget(label(title, "dialogHeading"))
        self.player = VideoPlayer()
        self.player.open(path, frame_number)
        self.player.start_button.hide()
        self.player.canvas.setToolTip("Scroll to zoom, drag to move, and double-click to fit the video.")
        layout.addWidget(self.player, 1)
        hint = label("Scroll to zoom · Drag to move · Double-click to fit")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(hint)

    def closeEvent(self, event):
        self.player.release()
        event.accept()

    def done(self, result):
        self.player.release()
        super().done(result)


class MultiCameraReportDialog(QDialog):
    """One report covering every camera and the combined observation timeline."""
    def __init__(self, sources, results, alignment, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Multi-camera forensic report")
        self.setObjectName("forensicReportDialog")
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(11)
        layout.addWidget(label("Multi-camera forensic report", "dialogHeading"))
        layout.addWidget(label(
            "This is a system-generated report. All detections and validation results are subject to human analyst review and should be treated as reviewable observations, not conclusive findings.",
            "reportObservation"))
        _, metrics = build_timeline(sources, results, alignment)
        counts = metrics["counts"]
        score = f"{metrics['mccr_percent']:.2f}%" if metrics["mccr_percent"] is not None else "N/A"
        layout.addWidget(label(
            f"Incident: {alignment.scene_id or 'Not specified'} · {len(sources)} cameras · "
            f"{len(rows)} observations · MCCR {score}", "sectionTitle"))
        layout.addWidget(label(
            f"Corroborated {counts.get('Corroborated', 0)} · Not corroborated {counts.get('Not Corroborated', 0)} · "
            f"Uncertain {counts.get('Uncertain', 0)} · Not applicable {counts.get('Not Applicable', 0)}. "
            f"Alignment: {alignment.method or 'Not confirmed'}; matching window ±{alignment.window_seconds:.2f} s."))
        camera_table = QTableWidget(len(sources), 6)
        camera_table.setHorizontalHeaderLabels(["Camera", "Source video", "Resolution / FPS", "Frames", "Observations", "TCR"])
        camera_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        camera_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        camera_table.verticalHeader().hide()
        camera_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        camera_table.setMaximumHeight(130)
        for index, (source, result) in enumerate(zip(sources, results, strict=True)):
            tcr = calculate_report_metrics(result.metric_input_path)["tcr"] if result.metric_input_path else {}
            tcr_value = tcr.get("value_percent")
            values = [source.camera_id, source.video.path.name,
                      f"{source.video.width} × {source.video.height} · {source.video.fps:.2f} FPS",
                      str(source.video.frame_count), str(len(result.detections)),
                      f"{tcr_value:.2f}%" if tcr_value is not None else "N/A"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(source.video.path))
                camera_table.setItem(index, column, item)
        layout.addWidget(camera_table)
        layout.addWidget(label("COMBINED OBSERVATION TIMELINE", "sectionTitle"))
        decisions = {}
        for result in results:
            decisions.update({item["observationId"]: (item["analystReview"] or {}).get("decision", "Not reviewed")
                              for item in ReviewStore.for_result(result).observations})
        table = QTableWidget(len(rows), 9)
        table.setHorizontalHeaderLabels(["Session time", "Camera", "Video time", "Frame", "Object", "Confidence", "Box", "Cross-view", "Analyst"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for index, row in enumerate(rows):
            values = [session_time(row["session_seconds"]), row["camera_id"], timecode(row["video_seconds"]),
                      str(row["frame_number"]), row["object_label"].title(), f"{row['confidence']:.1%}",
                      str(row["box"]), row["corroboration_status"], decisions.get(row["observation_id"], "Not reviewed")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(f"{row['source_video']}\n{row['reason']}\nObservation: {row['observation_id']}")
                table.setItem(index, column, item)
        layout.addWidget(table, 1)
        layout.addWidget(label("Times are video-relative; source recording timestamps are unavailable. Saving produces a combined landscape PDF, plus an individual PDF, annotated video and structured records for each camera."))
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

class MultiCameraDialog(QWidget):
    """Multi-camera analysis page embedded in the main application window."""
    state_changed = Signal()

    def __init__(self, videos, parent=None):
        super().__init__(parent)
        self.setObjectName("multiCameraWorkspace")
        self.setStyleSheet(load_stylesheet())
        self.sources = [CameraSource(video, f"CAM-{index + 1:02d}") for index, video in enumerate(videos)]
        self.results, self.rows, self.cards, self.editors = [], [], [], []
        self.alignment = Alignment()
        self.bridge = ModelBridge()
        if parent and hasattr(parent, "bridge"):
            self.bridge.set_model_path(parent.bridge.model_path)
        self.threshold = load_preferences().confidence_percent
        self.worker = None
        self._processing_dialog = None
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(40)
        self.play_timer.timeout.connect(self._tick)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        splitter = self.page_shell = DetectionPageShell("multiCameraSplitter")
        layout.addWidget(splitter, 1)

        sidebar, side = page_panel("sidePanel")
        side.addWidget(label("EVIDENCE SOURCES", "sectionTitle"))
        self.setup = QWidget()
        setup = QVBoxLayout(self.setup)
        setup.setContentsMargins(0, 0, 0, 0)
        setup.setSpacing(6)
        self.scene = QLineEdit()
        self.scene.setPlaceholderText("Incident name or ID")
        self.scene.setAccessibleName("Incident name or ID")
        self.scene.setToolTip("Name this incident, for example Scene 004.")
        setup.addWidget(self.scene)
        add = QPushButton("+ Add camera recordings")
        add.clicked.connect(self.add_recordings)
        setup.addWidget(add)
        enhancement_card, enhancement_copy = enhancement_heading()
        enhancement_copy.addWidget(label("Optional · enhance before detection", "cameraSourceDetail"))
        setup.addWidget(enhancement_card)
        self.source_scroll = QScrollArea()
        self.source_scroll.setObjectName("cameraSourcesScroll")
        self.source_scroll.setWidgetResizable(True)
        self.source_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.source_scroll.setMinimumHeight(90)
        self.source_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        setup.addWidget(self.source_scroll, 1)
        setup.addSpacing(4)
        setup.addWidget(label("RECORDING ALIGNMENT", "sectionTitle"))
        self.confirmed = QCheckBox("Same incident, aligned")
        self.confirmed.setToolTip("Confirm after checking that the recordings correspond and their timing is aligned.")
        setup.addWidget(self.confirmed)
        self.details_button = QPushButton("Camera and timing details")
        self.details_button.clicked.connect(self.open_details)
        setup.addWidget(self.details_button)
        side.addWidget(self.setup, 1)

        self.configure_button = QPushButton("Analyze cameras")
        self.configure_button.setObjectName("primaryButton")
        self.configure_button.clicked.connect(self.configure)
        side.addWidget(self.configure_button)
        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_box = QVBoxLayout(status_card)
        status_box.setContentsMargins(12, 12, 12, 12)
        status_box.setSpacing(5)
        self.status_title = label("Ready to analyze", "statusTitle")
        self.status = label("Confirm the incident and analyze all cameras.", "mutedText")
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumHeight(5)
        self.progress_bar.hide()
        status_box.addWidget(self.status_title)
        status_box.addWidget(self.status)
        status_box.addWidget(self.progress_bar)
        side.addWidget(status_card)
        side.addWidget(label("TRAINED MODEL", "sectionTitle"))
        model_path = self.bridge.model_path
        model_text = (f"Faster R-CNN · Handgun / Knife\n{Path(model_path).name}" if model_path and Path(model_path).is_file()
                      else f"Selected checkpoint unavailable\n{Path(model_path).name}" if model_path
                      else "No checkpoint selected")
        self.model_label = label(
            model_text, "mutedText")
        self.model_label.setToolTip(str(model_path) if model_path else "Select a model in detection configuration.")
        side.addWidget(self.model_label)
        splitter.addWidget(sidebar)

        content, center = page_panel("workspace")
        workspace_title = label("MULTI-CAMERA WEAPON DETECTION", "workspaceTitle")
        workspace_title.setWordWrap(False)
        self.workspace_meta = label("Imported recordings", "mutedText")
        heading = workspace_heading(workspace_title, self.workspace_meta)
        center.addLayout(heading)
        notice = label("One incident · Multiple viewpoints · Shared playback", "multiCameraIntro")
        center.addWidget(notice)
        tools = QHBoxLayout()
        self.camera_count = label("CAMERA VIEWS", "sectionTitle")
        tools.addWidget(self.camera_count, 1)
        self.view = QComboBox()
        self.view.addItems(["Imported recordings", "Annotated results"])
        self.view.setEnabled(False)
        self.view.currentIndexChanged.connect(self.change_view)
        tools.addWidget(self.view)
        center.addLayout(tools)
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setObjectName("cameraGridScroll")
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.viewport().installEventFilter(self)
        center.addWidget(self.grid_scroll, 4)
        playback = QHBoxLayout()
        self.play_button = QPushButton("Play all")
        self.play_button.clicked.connect(self.toggle_playback)
        playback.addWidget(self.play_button)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.sliderPressed.connect(self.pause)
        self.seek.valueChanged.connect(self.seek_all)
        playback.addWidget(self.seek, 1)
        self.time_label = label("Session 00:00.000")
        playback.addWidget(self.time_label)
        center.addLayout(playback)
        observations = QFrame()
        observations.setObjectName("observationsPanel")
        observations_layout = QVBoxLayout(observations)
        observations_layout.setContentsMargins(10, 10, 10, 8)
        observations_layout.setSpacing(5)
        self.metrics_label = label("DETECTED OBJECTS · TIMELINE", "sectionTitle")
        self.metrics_label.setToolTip("Select an observation to inspect that time across camera views.")
        observations_layout.addWidget(self.metrics_label)
        self.timeline = QTableWidget(0, 7)
        self.timeline.setHorizontalHeaderLabels(["Session time", "Camera", "Frame", "Object", "Confidence", "Cross-view", "Analyst"])
        self.timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline.setAlternatingRowColors(True)
        self.timeline.verticalHeader().hide()
        self.timeline.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.timeline.horizontalHeader().setStretchLastSection(True)
        self.timeline.cellClicked.connect(self.jump_to_observation)
        self.timeline.setMinimumHeight(160)
        observations_layout.addWidget(self.timeline)
        center.addWidget(observations, 2)
        center.addWidget(label("Automated detections require human review.", "safetyNote"))
        splitter.addWidget(content)

        summary, summary_box = page_panel("summaryPanel")
        summary_box.addWidget(label("DETECTION SUMMARY", "sectionTitle"))
        metrics, (self.total_metric, self.handgun_metric, self.knife_metric) = summary_metrics()
        summary_box.addLayout(metrics)
        self.summary_message = label("Analyze the imported cameras to see weapon observations.", "summaryMessage")
        summary_box.addWidget(self.summary_message)
        summary_box.addWidget(label("CROSS-CAMERA REVIEW", "sectionTitle"))
        mccr_card = QFrame()
        mccr_card.setObjectName("multiMccrCard")
        mccr_box = QVBoxLayout(mccr_card)
        mccr_box.setContentsMargins(12, 12, 12, 12)
        self.mccr_value = label("—", "multiMccrValue")
        mccr_box.addWidget(self.mccr_value)
        mccr_box.addWidget(label("Multi-camera corroboration rate", "mutedText"))
        summary_box.addWidget(mccr_card)
        self.cross_view_counts = label("Awaiting analysis.", "mutedText")
        summary_box.addWidget(self.cross_view_counts)
        self.metric_note = label("Uncertain and Not Applicable observations are excluded from MCCR.", "mutedText")
        summary_box.addWidget(self.metric_note)
        summary_box.addSpacing(8)
        summary_box.addWidget(label("CURRENT SESSION TIME", "sectionTitle"))
        self.current_time_detail = label("00:00.000 · 0 cameras in view", "mutedText")
        summary_box.addWidget(self.current_time_detail)
        summary_box.addWidget(label("ANALYST REVIEW", "sectionTitle"))
        self.reviewed_metric = label("0 observations reviewed", "mutedText")
        summary_box.addWidget(self.reviewed_metric)
        summary_box.addStretch()
        summary_box.addWidget(label(
            "Cross-camera matches are reviewable observations, not proof of the same physical object.",
            "enhancementNotice"))
        splitter.addWidget(summary)
        splitter.finish()
        self.rebuild_sources()

    def eventFilter(self, watched, event):
        if watched is self.grid_scroll.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._layout_camera_cards)
        return super().eventFilter(watched, event)

    def _layout_camera_cards(self):
        if not hasattr(self, "camera_grid"):
            return
        compact = self.grid_scroll.viewport().width() < 620
        for column in (2, 4, 6):
            self.timeline.setColumnHidden(column, compact)
        columns = 1 if compact else 2
        if columns == self._grid_columns:
            return
        self._grid_columns = columns
        while self.camera_grid.count():
            self.camera_grid.takeAt(0)
        for index, card in enumerate(self.camera_frames):
            self.camera_grid.addWidget(card, index // columns, index % columns)
        self.camera_grid.setColumnStretch(0, 1)
        self.camera_grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def capture_settings(self):
        self.alignment.scene_id = self.scene.text().strip()
        self.alignment.confirmed = self.confirmed.isChecked()
        if self.alignment.confirmed and not self.alignment.method:
            self.alignment.method = "User-confirmed synchronized recordings"

    def open_details(self):
        self.capture_settings()
        dialog = CameraSetupDialog(self.sources, self.alignment, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.apply()
            self.rebuild_sources()
            self.status.setText("Camera and timing details saved. Confirm the incident, then analyze all cameras.")

    def rebuild_sources(self):
        self.pause()
        for card in self.cards:
            card[0].release()
        self.cards, self.editors = [], []
        source_widget, grid_widget = QWidget(), QWidget()
        source_layout, grid = QVBoxLayout(source_widget), QGridLayout(grid_widget)
        self.camera_grid, self.camera_frames, self._grid_columns = grid, [], 0
        source_layout.setContentsMargins(0, 0, 5, 0)
        source_layout.setSpacing(9)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for index, source in enumerate(self.sources):
            editor = QFrame()
            editor.setObjectName("cameraEditor")
            box = QVBoxLayout(editor)
            box.setContentsMargins(11, 10, 11, 10)
            box.setSpacing(6)
            source_heading = QHBoxLayout()
            identifier = label(source.camera_id, "cameraSourceId")
            source_heading.addWidget(identifier, 1)
            state = label("Enhanced" if source.original_video_path else "Original", "cameraSourceState")
            state.setWordWrap(False)
            state.setProperty("enhanced", bool(source.original_video_path))
            source_heading.addWidget(state)
            remove = QPushButton("Remove")
            remove.setObjectName("cameraRemoveButton")
            remove.clicked.connect(lambda checked=False, i=index: self.remove_recording(i))
            enhance = QPushButton("Enhance")
            enhance.setObjectName("cameraEnhanceButton")
            enhance.setToolTip(f"Run BasicVSR++ enhancement on {source.camera_id} before detection.")
            enhance.clicked.connect(lambda checked=False, i=index: self.enhance_camera(i))
            box.addLayout(source_heading)
            filename = label(source.video.path.name, "cameraSourceName")
            filename.setToolTip(f"Detection input: {source.video.path}" +
                                (f"\nOriginal recording: {source.original_video_path}" if source.original_video_path else ""))
            box.addWidget(filename)
            detail = f"{source.video.width} × {source.video.height} · {source.video.fps:.2f} FPS"
            if source.location:
                detail += f" · {source.location}"
            if source.offset_seconds:
                detail += f" · offset {source.offset_seconds:+.3f} s"
            box.addWidget(label(detail, "cameraSourceDetail"))
            actions = QHBoxLayout()
            actions.setSpacing(8)
            actions.addWidget(enhance, 1)
            actions.addWidget(remove)
            box.addLayout(actions)
            source_layout.addWidget(editor)
            card = QFrame()
            card.setObjectName("cameraCard")
            body = QVBoxLayout(card)
            body.setContentsMargins(10, 10, 10, 10)
            body.setSpacing(6)
            top = QHBoxLayout()
            title = label(source.camera_id, "enhancementModel")
            top.addWidget(title, 1)
            review = QPushButton("Review")
            review.setEnabled(False)
            review.clicked.connect(lambda checked=False, i=index: self.review_camera(i))
            preview = QPushButton("Preview")
            preview.clicked.connect(lambda checked=False, i=index: self.preview_camera(i))
            top.addWidget(preview)
            top.addWidget(review)
            body.addLayout(top)
            name = label(source.video.path.name)
            name.setToolTip(str(source.video.path))
            body.addWidget(name)
            player = VideoPlayer()
            player.canvas.setMinimumSize(260, 145)
            player.canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            player.canvas.setAcceptDrops(False)
            player.layout().itemAt(1).widget().hide()
            player.open(source.video.path)
            player.failed.connect(self.playback_error)
            body.addWidget(player, 1)
            info = label(f"{source.video.width} × {source.video.height} · {source.video.fps:.2f} FPS · {timecode(source.video.duration)}")
            body.addWidget(info)
            state = label("Enhanced · Ready for analysis" if source.original_video_path else
                          "Imported · Ready for analysis", "cameraStatus")
            body.addWidget(state)
            self.camera_frames.append(card)
            self.cards.append((player, title, state, review))
        source_layout.addStretch()
        for scroll, widget in ((self.source_scroll, source_widget), (self.grid_scroll, grid_widget)):
            old = scroll.takeWidget()
            if old:
                old.deleteLater()
            scroll.setWidget(widget)
        self._layout_camera_cards()
        self.camera_count.setText(f"CAMERA VIEWS / {len(self.sources):02d}")
        self.workspace_meta.setText(f"{len(self.sources)} imported recordings")
        self.configure_button.setEnabled(len(self.sources) >= 2)
        self.update_range()

    def update_range(self):
        if self.sources:
            self.seek.setRange(round(min(s.offset_seconds for s in self.sources) * 1000),
                               round(max(s.offset_seconds + s.video.duration for s in self.sources) * 1000) - 1)
            self.seek.setValue(self.seek.minimum())
            self.seek_all(self.seek.value())

    def clear_results(self):
        self.results, self.rows = [], []
        self.timeline.setRowCount(0)
        self.view.setEnabled(False)
        self.view.setCurrentIndex(0)
        self.metrics_label.setText("DETECTED OBJECTS · TIMELINE")
        for value in (self.total_metric, self.handgun_metric, self.knife_metric, self.mccr_value):
            value.setText("—")
        self.summary_message.setText("Analyze the imported cameras to see weapon observations.")
        self.cross_view_counts.setText("Awaiting analysis.")
        self.reviewed_metric.setText("0 observations reviewed")
        self.state_changed.emit()

    def add_recordings(self):
        filenames, _ = QFileDialog.getOpenFileNames(self, "Add camera recordings", "", VIDEO_FILTER)
        if not filenames:
            return
        self.capture_settings()
        existing = {s.video.path.resolve() for s in self.sources}
        try:
            videos = [read_video(path) for path in dict.fromkeys(filenames) if Path(path).resolve() not in existing]
        except Exception as exc:
            QMessageBox.warning(self, "Recording unavailable", str(exc))
            return
        for video in videos:
            number = 1
            while f"CAM-{number:02d}" in {s.camera_id for s in self.sources}:
                number += 1
            self.sources.append(CameraSource(video, f"CAM-{number:02d}"))
        self.clear_results()
        self.rebuild_sources()

    def remove_recording(self, index):
        self.capture_settings()
        self.sources.pop(index)
        self.clear_results()
        self.rebuild_sources()

    def enhance_camera(self, index):
        if self.worker or self.results or not 0 <= index < len(self.sources):
            return
        self.pause()
        source = self.sources[index]
        source_key = f"{source.video.path.resolve()}:{source.video.size_bytes}:{source.video.modified_ns}"
        digest = sha256(source_key.encode("utf-8")).hexdigest()[:12]
        output = Path(__file__).parent / "outputs" / "enhanced_videos" / f"camera_{index + 1:02d}_{digest}_enhanced.mp4"
        from mockup_ui.app import VideoEnhancementDialog
        dialog = VideoEnhancementDialog(source.video, self, output_path=output)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.enhanced_video_path:
            return
        try:
            enhanced = read_video(dialog.enhanced_video_path)
        except Exception as exc:
            QMessageBox.warning(self, "Enhanced video unavailable", str(exc))
            return
        if source.original_video_path is None:
            source.original_video_path = source.video.path
        source.video = enhanced
        self.clear_results()
        self.rebuild_sources()
        self.status.setText(f"{source.camera_id} enhanced with BasicVSR++. Ready to analyze all cameras.")

    def configure(self):
        if self.worker:
            return
        if self.results:
            self.clear_results()
            self.rebuild_sources()
            self.setup.setEnabled(True)
            self.configure_button.setText("Analyze cameras")
            self.status_title.setText("Ready to analyze")
            self.status.setText("Edit camera setup, then configure the next analysis. Previous exports remain unchanged.")
            return
        self.pause()
        self.capture_settings()
        try:
            validate_session(self.sources, self.alignment)
        except ValueError as exc:
            QMessageBox.warning(self, "Check camera setup", str(exc))
            return
        from mockup_ui.app import DetectionConfigDialog
        dialog = DetectionConfigDialog(self.sources[0].video, self.threshold, self, self.bridge.model_path,
                                       videos=[s.video for s in self.sources])
        enhanced_count = sum(source.original_video_path is not None for source in self.sources)
        if enhanced_count:
            dialog.enhancement_notice.setText(
                f"INPUT FOOTAGE\n{enhanced_count} of {len(self.sources)} camera recordings were enhanced with BasicVSR++. "
                "Detection will analyze the current recording for each camera."
            )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.threshold = dialog.slider.value()
        self.bridge.set_model_path(dialog.selected_model)
        self.model_label.setText(f"Faster R-CNN · Handgun / Knife\n{Path(dialog.selected_model).name}")
        self.model_label.setToolTip(str(dialog.selected_model))
        self.bridge.enable_cctv_intelligence = dialog.cctv_intel_checkbox.isChecked()
        self.bridge.enable_temporal_consistency = dialog.temporal_checkbox.isChecked()
        self.start_analysis()

    def start_analysis(self):
        self.clear_results()
        self.rebuild_sources()
        self.setup.setEnabled(False)
        self.configure_button.setEnabled(False)
        self.play_button.setEnabled(False)
        self.seek.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_title.setText("Scanning cameras")
        self.status.setText("The detector is processing each recording in sequence.")
        self.workspace_meta.setText("Analyzing · please wait")
        self.summary_message.setText("Detection totals will appear when every camera finishes.")
        from mockup_ui.app import ProcessingDialog
        self._processing_dialog = ProcessingDialog(
            sum(source.video.frame_count for source in self.sources), self.window(), camera_count=len(self.sources))
        self._processing_dialog.show()
        self.worker = CameraBatchWorker(list(self.sources), self.bridge, self.threshold / 100, self)
        self.worker.progress.connect(self.on_progress, Qt.ConnectionType.QueuedConnection)
        self.worker.succeeded.connect(self.completed, Qt.ConnectionType.QueuedConnection)
        self.worker.failed.connect(self.failed, Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self.worker_finished, Qt.ConnectionType.QueuedConnection)
        self.worker.start()
        self.state_changed.emit()

    @Slot(str, int)
    def on_progress(self, message, percent):
        self.status.setText(message)
        self.progress_bar.setValue(percent)
        if self._processing_dialog:
            self._processing_dialog.update_progress(message, percent)

    @Slot(object)
    def completed(self, results):
        self._close_processing_dialog()
        self.results = results
        self.rows, metrics = build_timeline(self.sources, results, self.alignment)
        counts = metrics["counts"]
        score = f"{metrics['mccr_percent']:.2f}%" if metrics["mccr_percent"] is not None else "N/A"
        self.metrics_label.setText(f"DETECTED OBJECTS · {len(self.rows)} OBSERVATIONS")
        self.total_metric.setText(f"{len(self.rows):,}")
        self.handgun_metric.setText(f"{sum(result.counts.get('handgun', 0) for result in results):,}")
        self.knife_metric.setText(f"{sum(result.counts.get('knife', 0) for result in results):,}")
        self.mccr_value.setText(score)
        self.summary_message.setText(
            f"{len(self.rows):,} frame observations across {len(results)} cameras. "
            "Open Review on a camera to record analyst decisions." if self.rows else
            "No handgun or knife met the configured threshold in these recordings.")
        self.cross_view_counts.setText(
            f"{counts.get('Corroborated', 0)} corroborated · {counts.get('Not Corroborated', 0)} not corroborated\n"
            f"{counts.get('Uncertain', 0)} uncertain · {counts.get('Not Applicable', 0)} not applicable")
        self.refresh_timeline()
        for result, card in zip(results, self.cards, strict=True):
            card[2].setText(f"Analyzed · {len(result.detections)} observations · {result.analyzed_frames} frames")
            card[3].setEnabled(bool(result.detections))
        self.view.setEnabled(True)
        self.view.setCurrentIndex(1)
        self.status_title.setText("Analysis complete")
        self.status.setText("Preview a camera or select an observation to inspect all views.")
        self.workspace_meta.setText("Annotated results")
        self.configure_button.setText("New analysis")
        self.state_changed.emit()

    @Slot(str)
    def failed(self, details):
        self._close_processing_dialog()
        import logging
        logging.error("Multi-camera analysis failed\n%s", details)
        self.status_title.setText("Analysis unavailable")
        self.status.setText("Session analysis did not complete. Correct the input or model issue and retry.")
        self.summary_message.setText("No completed multi-camera result was produced.")
        self.workspace_meta.setText("Analysis incomplete")
        QMessageBox.warning(self, "Camera analysis failed", details.splitlines()[-1])

    @Slot()
    def worker_finished(self):
        self._close_processing_dialog()
        self.worker.wait()
        self.worker.deleteLater()
        self.worker = None
        self.setup.setEnabled(not bool(self.results))
        self.configure_button.setEnabled(len(self.sources) >= 2)
        self.play_button.setEnabled(True)
        self.seek.setEnabled(True)
        self.progress_bar.hide()
        self.state_changed.emit()
        if self.results and load_preferences().autoplay_results:
            self.toggle_playback()

    def _close_processing_dialog(self):
        if self._processing_dialog:
            self._processing_dialog.accept()
            self._processing_dialog.deleteLater()
            self._processing_dialog = None

    def refresh_timeline(self):
        decisions = {}
        for result in self.results:
            decisions.update({row["observationId"]: (row["analystReview"] or {}).get("decision", "Not reviewed")
                              for row in ReviewStore.for_result(result).observations})
        self.timeline.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            values = [session_time(row["session_seconds"]), row["camera_id"],
                      str(row["frame_number"]), row["object_label"].title(), f"{row['confidence']:.1%}",
                      row["corroboration_status"], decisions.get(row["observation_id"], "Not reviewed")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(f"{row['source_video']}\nVideo time: {timecode(row['video_seconds'])}\n"
                                f"Frame: {row['frame_number']} · Confidence: {row['confidence']:.1%}\n"
                                f"Analyst: {decisions.get(row['observation_id'], 'Not reviewed')}\n"
                                f"{row['reason']}\nTemporal status: {row['temporal_status']}\nBox: {row['box']}")
                self.timeline.setItem(index, column, item)
        reviewed = sum(decision != "Not reviewed" for decision in decisions.values())
        self.reviewed_metric.setText(f"{reviewed} / {len(self.rows)} observations reviewed")

    def review_camera(self, index):
        self.pause()
        _, metrics = build_timeline(self.sources, self.results, self.alignment)
        ObservationReviewDialog(self.results[index], self,
                                cross_view={row["observation_id"]: row for row in self.rows},
                                session_mccr=metrics["mccr_percent"]).exec()
        self.refresh_timeline()

    def preview_camera(self, index):
        self.pause()
        source = self.sources[index]
        path = self.results[index].output_path if self.results and self.view.currentIndex() == 1 else source.video.path
        frame = self.cards[index][0].frame_number
        CameraPreviewDialog(path, f"{source.camera_id} · {source.video.path.name}", frame, self).exec()

    def jump_to_observation(self, row, column):
        self.pause()
        self.seek.setValue(round(self.rows[row]["session_seconds"] * 1000))

    def change_view(self):
        self.pause()
        for index, (source, card) in enumerate(zip(self.sources, self.cards)):
            path = self.results[index].output_path if self.results and self.view.currentIndex() == 1 else source.video.path
            try:
                card[0].open(path)
            except ValueError as exc:
                self.playback_error(str(exc))
        self.seek_all(self.seek.value())

    def seek_all(self, milliseconds):
        seconds = milliseconds / 1000
        self.time_label.setText(f"Session {session_time(seconds)}")
        coverage = sum(source.offset_seconds <= seconds < source.offset_seconds + source.video.duration
                       for source in self.sources)
        self.current_time_detail.setText(f"{session_time(seconds)} · {coverage} of {len(self.sources)} cameras in view")
        for source, card in zip(self.sources, self.cards):
            local = seconds - source.offset_seconds
            if 0 <= local < source.video.duration:
                card[0].seek_frame(min(round(local * source.video.fps), source.video.frame_count - 1))
                card[0].canvas.show()
            else:
                card[0].canvas._source_pixmap = QPixmap()
                card[0].canvas.clear()
                card[0].canvas.setText("No recording at this session time")
            card[1].setText(f"{source.camera_id} · {timecode(local) if 0 <= local < source.video.duration else 'Outside recording'}")

    def toggle_playback(self):
        if self.play_timer.isActive():
            self.pause()
        elif self.sources:
            if self.seek.value() >= self.seek.maximum():
                self.seek.setValue(self.seek.minimum())
            self._play_start, self._play_position = monotonic(), self.seek.value()
            self.play_timer.start()
            self.play_button.setText("Pause all")

    def _tick(self):
        position = self._play_position + round((monotonic() - self._play_start) * 1000)
        self.seek.setValue(min(position, self.seek.maximum()))
        if position >= self.seek.maximum():
            self.pause()

    def pause(self):
        self.play_timer.stop()
        self.play_button.setText("Play all")

    def playback_error(self, message):
        self.pause()
        self.status.setText(message)

    def export(self):
        self.pause()
        if not self.results or self.worker:
            return
        destination = QFileDialog.getExistingDirectory(self, "Save videos and forensic reports", export_start_directory())
        if not destination:
            return
        try:
            folder = export_session(self.sources, self.results, self.alignment, destination)
        except Exception as exc:
            QMessageBox.warning(self, "Export incomplete", f"The session could not be fully saved: {exc}")
            return
        QMessageBox.information(self, "Session saved", f"Saved a combined landscape forensic PDF and combined observations, plus an individual forensic PDF, annotated video and structured records for each camera.\n\n{folder}")

    def show_report(self):
        if self.results and not self.worker:
            self.pause()
            MultiCameraReportDialog(self.sources, self.results, self.alignment, self.rows, self).exec()

    def reject(self):
        self.close()

    def accept(self):
        self.close()

    def closeEvent(self, event):
        if self.worker:
            self.status.setText("The detector is processing recordings. Wait for completion before closing this workspace.")
            event.ignore()
            return
        self.pause()
        for preview in self.findChildren(CameraPreviewDialog):
            preview.reject()
        for player, *_ in self.cards:
            player.release()
        event.accept()
