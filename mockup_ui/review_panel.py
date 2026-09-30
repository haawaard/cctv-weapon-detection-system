"""Analyst observation review with independent automated and manual statuses."""
from pathlib import Path

import cv2
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog, QHBoxLayout,
    QLabel, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)

from mockup_ui.observation_review import DECISIONS, ReviewStore, status_label
from mockup_ui.video_player import VideoCanvas
from mockup_ui.ui_theme import load_stylesheet
from mockup_ui.motion import SpringButton as QPushButton, SpringDialog as QDialog, reveal_feedback


class ObservationReviewDialog(QDialog):
    def __init__(self, result, parent=None, cross_view=None, session_mccr=None):
        super().__init__(parent)
        self.result = result
        self.cross_view = cross_view or {}
        self.store = ReviewStore.for_result(result)
        self.current_index = -1
        self._loading = False
        self._drafts = {}
        self.setWindowTitle("Observation Review")
        self.setObjectName("observationReviewDialog")
        self.resize(1120, 800)
        self.setMinimumSize(920, 700)
        self.setStyleSheet(load_stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QLabel("Observation Review")
        heading.setObjectName("dialogHeading")
        layout.addWidget(heading)

        # Compute TCR and MCCR metrics for review session
        metric_path = getattr(result, "metric_input_path", None)
        if not metric_path or not Path(metric_path).exists():
            candidate = result.output_path.parent / "metric_input.csv"
            metric_path = candidate if candidate.exists() else None

        tcr_display = "TCR: N/A"
        mccr_display = "MCCR: N/A"
        if metric_path and Path(metric_path).exists():
            from mockup_ui.report_metrics import calculate_report_metrics
            metrics = calculate_report_metrics(Path(metric_path))
            tcr_val = metrics.get("tcr", {}).get("value_percent")
            mccr_val = metrics.get("mccr", {}).get("value_percent")
            if tcr_val is not None:
                tcr_display = f"TCR: {tcr_val:.1f}%"
            if mccr_val is not None:
                mccr_display = f"MCCR: {mccr_val:.1f}%"

        if cross_view is not None:
            mccr_display = f"Session MCCR: {session_mccr:.1f}%" if session_mccr is not None else "Session MCCR: N/A"
        header_bar = QHBoxLayout()
        self.progress_label = QLabel()
        self.progress_label.setObjectName("dialogDetail")
        header_bar.addWidget(self.progress_label)
        header_bar.addStretch()
        self.metrics_badge = QLabel(f"{tcr_display}  ·  {mccr_display}")
        self.metrics_badge.setObjectName("reviewBadge")
        header_bar.addWidget(self.metrics_badge)
        layout.addLayout(header_bar)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.observation_list = QListWidget()
        self.observation_list.setMinimumWidth(255)
        self.observation_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        splitter.addWidget(self.observation_list)
        detail = QWidget()
        box = QVBoxLayout(detail)
        box.setContentsMargins(20, 0, 0, 0)
        box.setSpacing(9)
        self.title = QLabel()
        self.title.setObjectName("sectionTitle")
        self.title.setWordWrap(True)
        box.addWidget(self.title)
        self.canvas = VideoCanvas()
        self.canvas.setAcceptDrops(False)
        self.canvas.setMinimumSize(320, 180)
        self.canvas.setMaximumHeight(260)
        box.addWidget(self.canvas, 1)
        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setObjectName("dialogDetail")
        box.addWidget(self.details)
        self.automated_label = QLabel()
        self.automated_label.setTextFormat(Qt.TextFormat.PlainText)
        self.automated_label.setWordWrap(True)
        self.automated_label.setObjectName("automatedStatus")
        box.addWidget(self.automated_label)
        box.addWidget(QLabel("ANALYST REVIEW DECISION"))
        self.decision_group = QButtonGroup(self)
        self.decision_group.setExclusive(True)
        self.decision_buttons = {}
        buttons = QHBoxLayout()
        for decision in DECISIONS:
            button = QPushButton(decision)
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setObjectName("decisionButton")
            button.setProperty("decision", decision)
            self.decision_group.addButton(button)
            self.decision_buttons[decision] = button
            buttons.addWidget(button)
        self.decision_group.buttonClicked.connect(self._decision_changed)
        box.addLayout(buttons)
        self.notes_label = QLabel("Analyst Notes (Optional)")
        box.addWidget(self.notes_label)
        self.notes = QPlainTextEdit()
        self.notes.setMinimumHeight(75)
        self.notes.setMaximumHeight(120)
        self.notes.setPlaceholderText("Choose a decision, then add notes if needed...")
        self.notes.textChanged.connect(self._mark_draft)
        box.addWidget(self.notes)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName("reviewFeedback")
        box.addWidget(self.feedback)
        self.save_button = QPushButton("Save review")
        self.save_button.setObjectName("primaryButton")
        self.save_button.setAutoDefault(False)
        self.save_button.clicked.connect(self.save_review)
        box.addWidget(self.save_button)
        splitter.addWidget(detail)
        splitter.setSizes([300, 770])
        layout.addWidget(splitter, 1)
        footer = QHBoxLayout()
        reopen = QLabel("Saved reviews remain available through Open saved review.")
        reopen.setObjectName("dialogDetail")
        footer.addWidget(reopen)
        footer.addStretch()
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        layout.addLayout(footer)
        self.observation_list.currentRowChanged.connect(self.select_observation)
        self.refresh_list()
        if self.store.observations:
            self.observation_list.setCurrentRow(0)
        else:
            self.title.setText("No observations to review")
            detail.setEnabled(False)

    def refresh_list(self):
        observations = self.store.observations
        self.observation_list.blockSignals(True)
        if self.observation_list.count() != len(observations):
            self.observation_list.clear()
            self.observation_list.addItems([""] * len(observations))
        for index, observation in enumerate(observations):
            row = observation["detectionDetails"]
            review = observation["analystReview"]
            decision = review["decision"] if review else "Not reviewed"
            self.observation_list.item(index).setText(
                f"OBS-{index + 1:05d} · {row['class_name'].title()}\n"
                f"Frame {row['frame_number']} · {row['timestamp_seconds']:.2f}s · {decision}"
            )
        self.observation_list.blockSignals(False)
        saved = sum(row["analystReview"] is not None for row in observations)
        self.progress_label.setText(f"{self.result.video.path.name} · {saved} of {len(observations)} observations reviewed")

    def _selected_decision(self):
        selected = self.decision_group.checkedButton()
        return selected.text() if selected else None

    def _mark_draft(self):
        if self._loading or self.current_index < 0:
            return
        self._drafts[self.current_index] = (self._selected_decision(), self.notes.toPlainText())
        self.feedback.setText("Unsaved changes")

    def _decision_changed(self):
        decision = self._selected_decision()
        self.notes_label.setText("Reason for Rejection *" if decision == "Reject" else "Analyst Notes (Optional)")
        self.notes.setPlaceholderText({
            "Reject": "Enter the reason for rejecting this observation...",
            "Uncertain": "Optionally explain why this observation is uncertain...",
        }.get(decision, "Add an optional note..."))
        self._mark_draft()

    def select_observation(self, index):
        if index < 0:
            return
        self.current_index = index
        self._loading = True
        observation = self.store.observations[index]
        row = observation["detectionDetails"]
        review = observation["analystReview"]
        decision, notes = self._drafts.get(index, (review["decision"], review["notes"]) if review else (None, ""))
        self.decision_group.setExclusive(False)
        for value, button in self.decision_buttons.items():
            button.setChecked(value == decision)
        self.decision_group.setExclusive(True)
        self.notes.setPlainText(notes)
        self._decision_changed()
        self.title.setText(observation["observationId"])
        self.details.setText(
            f"Source: {observation['sourceReferences'][0]['videoPath']}\n"
            f"Frame {row['frame_number']} (zero-based) · Video offset {row['timestamp_seconds']:.2f}s\n"
            f"{row['class_name'].title()} · Confidence {row['confidence']:.2%} · Box {row['box']}"
        )
        self.automated_label.setText(f"Automated Validation Result: {status_label(observation['automatedValidationStatus'])}")
        self.automated_label.setToolTip("The current detection pipeline does not perform separate observation validation. This value is never changed by analyst decisions.")
        context = self.cross_view.get(observation["observationId"])
        if context:
            self.details.setText(self.details.text() + f"\nCamera: {context['camera_id']} · Scene: {context['scene_id'] or 'Not recorded'} · Session time: {context['session_seconds']:.3f}s")
            self.automated_label.setText(self.automated_label.text() + f"\nCross-view: {context['corroboration_status']}\n{context['reason']}")
            self.automated_label.setToolTip("Automatic temporal and cross-camera observations are independent of the analyst decision.")
        self.feedback.setText("Unsaved changes" if index in self._drafts else
                              f"Saved {review['reviewedAt']}" if review else "Choose a decision to complete this observation review.")
        self._show_frame(row)
        self._loading = False

    def _show_frame(self, row):
        capture = cv2.VideoCapture(str(self.result.output_path))
        try:
            capture.set(cv2.CAP_PROP_POS_FRAMES, row["frame_number"])
            ok, frame = capture.read()
        finally:
            capture.release()
        if ok:
            self.canvas.show_frame(frame)
        else:
            self.canvas._source_pixmap = QPixmap()
            self.canvas.clear()
            self.canvas.setText("Frame preview unavailable. The saved detection record remains available.")

    def save_review(self):
        if self.current_index < 0:
            return
        observation = self.store.observations[self.current_index]
        try:
            review = self.store.save_review(observation["observationId"], self._selected_decision(), self.notes.toPlainText())
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        except (OSError, KeyError, TypeError):
            self.feedback.setText("The review could not be saved. Check the review folder permissions and disk space, then try again.")
            return
        self._drafts.pop(self.current_index, None)
        self._loading = True
        self.notes.setPlainText(review["notes"])
        self._loading = False
        self.refresh_list()
        self.feedback.setText(f"Review saved · {review['decision']} · {review['reviewedAt']}")
        reveal_feedback(self.feedback)

    def reject(self):
        if self._drafts:
            answer = QMessageBox.question(self, "Unsaved review changes", "Discard the unsaved changes and close? Saved reviews will be kept.",
                                          QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                          QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()
