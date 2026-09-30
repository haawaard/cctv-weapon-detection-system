"""Forensikada video recognition and weapon detection interface."""

from __future__ import annotations

import argparse
import importlib.util
import logging
from hashlib import sha256
from math import sin, tau
from pathlib import Path
import subprocess
import sys
import traceback
from time import monotonic

MOCKUP_DIR = Path(__file__).resolve().parent
ROOT_DIR = MOCKUP_DIR.parent
TEMP_DIR = MOCKUP_DIR / "temp"
ASSET_DIR = MOCKUP_DIR / "assets"
BRAND_LOGO_PATH = ASSET_DIR / "forensikada-logo.png"
APP_ICON_PATH = ASSET_DIR / "forensikada-app-icon.png"

# Make the common `python mockup_ui/app.py` command use the UI environment when
# the selected system Python does not have PySide6 installed.
UI_PYTHON = MOCKUP_DIR / ".venv" / "Scripts" / "python.exe"
if importlib.util.find_spec("PySide6") is None and UI_PYTHON.is_file():
    if Path(sys.executable).resolve() != UI_PYTHON.resolve():
        raise SystemExit(subprocess.call([str(UI_PYTHON), "-B", str(Path(__file__).resolve()), *sys.argv[1:]]))

for directory in (TEMP_DIR, MOCKUP_DIR / "outputs", MOCKUP_DIR / "models"):
    directory.mkdir(parents=True, exist_ok=True)
sys.dont_write_bytecode = True
# Give the original app/ package priority over this launcher's app.py filename.
if str(ROOT_DIR) in sys.path:
    sys.path.remove(str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR))

from PySide6.QtCore import (
    QAbstractTableModel, QEasingCurve, QModelIndex, QPropertyAnimation, QRectF, QSize,
    Qt, QThread, QTimer, Signal, Slot,
)
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
    QTableView,
    QHeaderView,
    QAbstractItemView,
    QCheckBox,
)

from mockup_ui.model_bridge import (
    MODEL_FILENAME, ModelBridge, ModelSetupError, VideoAnalysisResult, VideoInfo, VideoInputError,
    discover_available_models, read_video, save_result, timecode,
)
from mockup_ui.video_player import VideoPlayer
from mockup_ui.model_selector import ModelSelector
from mockup_ui.page_shell import DetectionPageShell, page_panel, workspace_heading, summary_metrics, enhancement_heading
from mockup_ui.ui_theme import apply_theme, current_theme, load_stylesheet, restore_theme
from mockup_ui.preferences import load_preferences, export_start_directory
from mockup_ui.settings_dialog import SettingsDialog
from mockup_ui.observation_review import REVIEW_DIR, ReviewStore
from mockup_ui.review_panel import ObservationReviewDialog

logging.basicConfig(
    filename=TEMP_DIR / "mockup.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


class DetectionTableModel(QAbstractTableModel):
    HEADERS = ("Time", "Object", "Confidence", "Frame")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = []

    def set_records(self, records):
        self.beginResetModel()
        self.records = records
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self.records[index.row()]
        return (timecode(row["timestamp_seconds"]), row["class_name"].title(),
                f"{row['confidence']:.1%}", str(row["frame_number"]))[index.column()]


class ReportTableModel(QAbstractTableModel):
    HEADERS = ("Time", "Frame", "Object", "Confidence", "Bounding box", "Analyst decision", "Analyst notes", "Reviewed at (UTC)")

    def __init__(self, records, parent=None, observations=None):
        super().__init__(parent)
        self.records = records
        self.observations = observations or []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self.records[index.row()]
        box = row["box"]
        observation = self.observations[index.row()] if self.observations else {}
        review = observation.get("analystReview") or {}
        return (timecode(row["timestamp_seconds"]), str(row["frame_number"]),
                row["class_name"].title(), f"{row['confidence']:.1%}",
                f"[{box[0]}, {box[1]}, {box[2]}, {box[3]}]",
                review.get("decision", "Not reviewed"), review.get("notes", ""), review.get("reviewedAt", ""))[index.column()]


class EnhancementFramePreview(QLabel):
    """Responsive still-frame preview used only by the enhancement setup UI."""

    def __init__(self, frame, parent=None):
        super().__init__(parent)
        height, width, _ = frame.shape
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_BGR888)
        self._source_pixmap = QPixmap.fromImage(image.copy())
        self.setObjectName("enhancementPreview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 100)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._fit()

    def _fit(self):
        if not self._source_pixmap.isNull():
            self.setPixmap(self._source_pixmap.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def resizeEvent(self, event):
        self._fit()
        super().resizeEvent(event)


class EnhancementRestorationPreview(QWidget):
    """Animated BasicVSR++ restoration sweep shown while enhancement is active."""

    def __init__(self, source_pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setObjectName("enhancementRestorationPreview")
        self.setMinimumSize(200, 100)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._source_pixmap = source_pixmap
        self._phase = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    @property
    def phase(self):
        return self._phase

    @property
    def is_animating(self):
        return self._active

    def start(self):
        self._phase = 0.0
        self._active = True
        self._timer.start()
        self.update()

    def stop(self):
        self._active = False
        self._timer.stop()
        self.update()

    def _advance(self):
        self._phase = (self._phase + 0.0045) % 1.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#111827"))
        if self._source_pixmap.isNull():
            return

        fitted = self._source_pixmap.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        left = (self.width() - fitted.width()) // 2
        top = (self.height() - fitted.height()) // 2
        image_rect = QRectF(left, top, fitted.width(), fitted.height())
        painter.drawPixmap(left, top, fitted)
        painter.fillRect(image_rect, QColor(5, 10, 29, 122))

        # The bidirectional sweep mirrors the forward/backward propagation used
        # by BasicVSR++. The restored area is revealed behind the scan head.
        sweep = 1.0 - abs(2.0 * self._phase - 1.0)
        scan_x = image_rect.left() + image_rect.width() * sweep
        reveal_width = max(1.0, scan_x - image_rect.left())
        painter.save()
        painter.setClipRect(QRectF(image_rect.left(), image_rect.top(), reveal_width, image_rect.height()))
        painter.drawPixmap(left, top, fitted)
        painter.fillRect(image_rect, QColor(58, 61, 156, 22))
        painter.restore()

        tile_width = image_rect.width() / 8.0
        tile_height = image_rect.height() / 5.0
        for row in range(5):
            for column in range(8):
                tile_center = image_rect.left() + (column + 0.5) * tile_width
                distance = abs(tile_center - scan_x)
                if distance > tile_width * 1.7:
                    continue
                strength = 1.0 - distance / (tile_width * 1.7)
                pulse = (sin((self._phase * tau * 4.0) + row * 0.85 + column * 0.4) + 1.0) / 2.0
                alpha = int(24 + 74 * strength * pulse)
                tile = QRectF(
                    image_rect.left() + column * tile_width + 2,
                    image_rect.top() + row * tile_height + 2,
                    tile_width - 4,
                    tile_height - 4,
                )
                painter.setPen(QColor(168, 172, 255, alpha))
                painter.drawRoundedRect(tile, 3, 3)

        for width, alpha in ((18, 20), (10, 40), (4, 105)):
            painter.fillRect(
                QRectF(scan_x - width / 2, image_rect.top(), width, image_rect.height()),
                QColor(120, 127, 255, alpha),
            )
        painter.fillRect(QRectF(scan_x - 1, image_rect.top(), 2, image_rect.height()), QColor("#fff2c7"))

        direction = "FORWARD PASS" if self._phase < 0.5 else "BACKWARD PASS"
        badge = QRectF(image_rect.left() + 12, image_rect.bottom() - 40, 255, 29)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 24, 52, 220))
        painter.drawRoundedRect(badge, 6, 6)
        painter.setPen(QColor("#eef0ff"))
        badge_font = painter.font()
        badge_font.setPixelSize(12)
        badge_font.setBold(True)
        painter.setFont(badge_font)
        painter.drawText(badge.adjusted(10, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, f"BASICVSR++  ·  {direction}")


class EnhancementWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(str, object)
    failed = Signal(str)

    def __init__(self, input_path: Path, output_path: Path, parent=None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path

    def run(self):
        try:
            from app.services.video_enhancement_service import VideoEnhancementService
            enhancer = VideoEnhancementService()
            self.progress.emit("Connecting to BasicVSR++ WSL environment...")
            if not enhancer.verify_wsl_access():
                self.failed.emit("WSL Linux environment is not accessible.")
                return

            self.progress.emit("Running BasicVSR++ video super-resolution on GPU... This may take a few moments.")
            enhancer.enhance_video(str(self.input_path), str(self.output_path))
            self.progress.emit("Validating the restored video and preparing the enhanced preview...")

            if not self.output_path.exists():
                self.failed.emit("Enhanced video output was not created.")
                return

            import cv2
            cap = cv2.VideoCapture(str(self.output_path))
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                self.failed.emit("Could not decode first frame of enhanced video.")
                return

            self.succeeded.emit(str(self.output_path), frame)
        except Exception as exc:
            self.failed.emit(str(exc))


class VideoEnhancementDialog(QDialog):
    """Interactive surface for the automatic BasicVSR++ enhancement stage."""

    def __init__(self, video: VideoInfo, parent=None, *, output_path: Path | None = None):
        super().__init__(parent)
        self.video = video
        source_key = f"{video.path.resolve()}:{video.size_bytes}:{video.modified_ns}"
        source_digest = sha256(source_key.encode("utf-8")).hexdigest()[:12]
        self.output_path = output_path or (MOCKUP_DIR / "outputs" / "enhanced_videos" / f"{video.path.stem}_{source_digest}_enhanced.mp4")
        self.enhanced_video_path: Path | None = None
        self.enhanced_first_frame = None
        self._worker: EnhancementWorker | None = None

        self.setObjectName("videoEnhancementDialog")
        self.setWindowTitle("Video Enhancement")
        self.setModal(True)
        self.setMinimumSize(900, 650)
        available = self.screen().availableGeometry()
        self.resize(min(1100, available.width() - 48), min(760, available.height() - 60))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hero = QFrame()
        hero.setObjectName("enhancementHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(24, 14, 24, 14)
        hero_layout.setSpacing(6)
        hero_top = QHBoxLayout()
        eyebrow = QLabel("VIDEO ENHANCEMENT")
        eyebrow.setObjectName("enhancementEyebrow")
        hero_top.addWidget(eyebrow)
        hero_top.addStretch()
        self.engine_status = QLabel("READY")
        self.engine_status.setObjectName("enhancementEngineStatus")
        hero_top.addWidget(self.engine_status)
        hero_layout.addLayout(hero_top)
        heading = QLabel("Enhance footage before detection")
        heading.setObjectName("enhancementHeading")
        hero_layout.addWidget(heading)
        subheading = QLabel(
            "Improve video quality before checking for weapons."
        )
        subheading.setObjectName("enhancementSubheading")
        subheading.setWordWrap(True)
        hero_layout.addWidget(subheading)

        source_summary = QFrame()
        source_summary.setObjectName("enhancementSourceSummary")
        source_row = QHBoxLayout(source_summary)
        source_row.setContentsMargins(11, 7, 11, 7)
        source_row.setSpacing(9)
        source_badge = QLabel("SOURCE VIDEO")
        source_badge.setObjectName("enhancementSourceBadge")
        source_row.addWidget(source_badge)
        source_detail = QLabel(
            f"{video.path.name}  ·  {video.width} × {video.height}  ·  {video.fps:.2f} FPS  ·  "
            f"{timecode(video.duration)}"
        )
        source_detail.setObjectName("enhancementSourceDetail")
        source_detail.setWordWrap(True)
        source_row.addWidget(source_detail, 1)
        hero_layout.addWidget(source_summary)
        layout.addWidget(hero)

        body = QFrame()
        body.setObjectName("enhancementBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 12, 20, 12)
        body_layout.setSpacing(10)

        profile = QFrame()
        profile.setObjectName("basicVsrProfile")
        profile_layout = QHBoxLayout(profile)
        profile_layout.setContentsMargins(14, 9, 14, 9)
        profile_layout.setSpacing(9)
        profile_name = QLabel("BasicVSR++")
        profile_name.setObjectName("enhancementModel")
        profile_layout.addWidget(profile_name)
        profile_description = QLabel("Automatic video enhancement · No manual adjustments required")
        profile_description.setObjectName("enhancementStatus")
        profile_description.setWordWrap(True)
        profile_layout.addWidget(profile_description, 1)
        body_layout.addWidget(profile)

        previews = QFrame()
        previews.setObjectName("enhancementPreviewArea")
        preview_layout = QHBoxLayout(previews)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        preview_layout.setSpacing(12)

        original_card = QFrame()
        original_card.setObjectName("enhancementPreviewCard")
        original_layout = QVBoxLayout(original_card)
        original_layout.setContentsMargins(11, 10, 11, 11)
        original_layout.setSpacing(7)
        original_header = QHBoxLayout()
        original_title = QLabel("Original video")
        original_title.setObjectName("enhancementPreviewTitle")
        original_header.addWidget(original_title)
        original_header.addStretch()
        source_state = QLabel("ORIGINAL")
        source_state.setObjectName("enhancementPreviewBadge")
        original_header.addWidget(source_state)
        original_layout.addLayout(original_header)
        self.source_preview = EnhancementFramePreview(video.first_frame)
        original_layout.addWidget(self.source_preview, 1)
        source_caption = QLabel("Your original recording")
        source_caption.setObjectName("enhancementPreviewCaption")
        original_layout.addWidget(source_caption)

        enhanced_card = QFrame()
        enhanced_card.setObjectName("enhancementPreviewCard")
        self.enhanced_layout = QVBoxLayout(enhanced_card)
        self.enhanced_layout.setContentsMargins(11, 10, 11, 11)
        self.enhanced_layout.setSpacing(7)
        enhanced_header = QHBoxLayout()
        enhanced_title = QLabel("Enhanced video")
        enhanced_title.setObjectName("enhancementPreviewTitle")
        enhanced_header.addWidget(enhanced_title)
        enhanced_header.addStretch()
        self.preview_status = QLabel("AWAITING RUN")
        self.preview_status.setObjectName("enhancementPreviewBadge")
        enhanced_header.addWidget(self.preview_status)
        self.enhanced_layout.addLayout(enhanced_header)
        self.enhanced_preview = QLabel(
            "Enhanced frame preview\n\nBasicVSR++ output will appear here"
        )
        self.enhanced_preview.setObjectName("enhancementPreviewPlaceholder")
        self.enhanced_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.enhanced_preview.setWordWrap(True)
        self.enhanced_preview.setMinimumSize(200, 100)
        self.enhanced_stack = QStackedWidget()
        self.enhanced_stack.setObjectName("enhancementPreviewStack")
        self.enhanced_stack.addWidget(self.enhanced_preview)
        self.restoration_preview = EnhancementRestorationPreview(self.source_preview._source_pixmap)
        self.enhanced_stack.addWidget(self.restoration_preview)
        self.enhanced_layout.addWidget(self.enhanced_stack, 1)
        self.enhanced_caption = QLabel("The result appears here after enhancement")
        self.enhanced_caption.setWordWrap(True)
        self.enhanced_caption.setObjectName("enhancementPreviewCaption")
        self.enhanced_layout.addWidget(self.enhanced_caption)
        preview_layout.addWidget(original_card, 1)
        preview_layout.addWidget(enhanced_card, 1)
        body_layout.addWidget(previews, 1)

        # Retained as a hidden compatibility hook. The visible feedback is the
        # bidirectional restoration sweep rather than a generic loading bar.
        self.enhancement_progress = QProgressBar()
        self.enhancement_progress.setRange(0, 0)
        self.enhancement_progress.setVisible(False)

        process = QFrame()
        process.setObjectName("enhancementProcessCard")
        process_layout = QVBoxLayout(process)
        process_layout.setContentsMargins(13, 10, 13, 10)
        process_layout.setSpacing(7)
        process_header = QHBoxLayout()
        process_title = QLabel("Enhancement progress")
        process_title.setObjectName("enhancementProcessTitle")
        process_header.addWidget(process_title)
        process_header.addStretch()
        self.process_status = QLabel("READY TO ENHANCE")
        self.process_status.setObjectName("enhancementProcessStatus")
        process_header.addWidget(self.process_status)
        process_layout.addLayout(process_header)
        stages = QHBoxLayout()
        stages.setSpacing(7)
        self.stage_labels = []
        for text in ("1  Prepare", "2  Enhance video", "3  Check result"):
            stage = QLabel(text)
            stage.setObjectName("enhancementStage")
            stage.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stage.setProperty("state", "idle")
            stages.addWidget(stage, 1)
            self.stage_labels.append(stage)
        process_layout.addLayout(stages)
        self.notice = QLabel(
            "Start enhancement, then apply the finished video to continue with detection."
        )
        self.notice.setObjectName("enhancementNotice")
        self.notice.setWordWrap(True)
        process_layout.addWidget(self.notice)
        body_layout.addWidget(process)

        action_bar = QFrame()
        action_bar.setObjectName("enhancementActionBar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(13, 9, 13, 9)
        action_copy = QVBoxLayout()
        action_copy.setSpacing(1)
        action_title = QLabel("Ready when you are")
        action_title.setObjectName("enhancementActionTitle")
        action_detail = QLabel("Enhance first, then analyze for weapons.")
        action_detail.setWordWrap(True)
        action_detail.setObjectName("enhancementActionDetail")
        action_copy.addWidget(action_title)
        action_copy.addWidget(action_detail)
        action_layout.addLayout(action_copy, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_text = "Enhance video"
        out_path = self.output_path
        existing_frame = None
        if out_path.exists():
            try:
                import cv2
                cap = cv2.VideoCapture(str(out_path))
                ok, frame = cap.read()
                cap.release()
                if ok and frame is not None:
                    self.enhanced_video_path = out_path
                    self.enhanced_first_frame = frame
                    existing_frame = frame
                    btn_text = "Apply Enhanced Video"
            except Exception:
                pass

        self.run_button = buttons.addButton(btn_text, QDialogButtonBox.ButtonRole.ActionRole)
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.run_button.setObjectName("primaryButton")
        self.run_button.setEnabled(True)
        self.run_button.clicked.connect(self._handle_run_or_apply)
        buttons.rejected.connect(self.reject)
        action_layout.addWidget(buttons)
        body_layout.addWidget(action_bar)
        layout.addWidget(body, 1)

        if existing_frame is not None:
            self._show_enhanced_frame(existing_frame)
            self._set_stage(completed=True)
            self.engine_status.setText("ENHANCED VIDEO READY")
            self.process_status.setText("RESTORATION COMPLETE")
            self.preview_status.setText("ENHANCED")
            self.notice.setText(
                "BasicVSR++ enhancement completed. Apply the enhanced video to use it for detection."
            )

    def _set_stage(self, active_index: int | None = None, *, completed=False, failed=False):
        for index, stage in enumerate(self.stage_labels):
            if completed:
                state = "done"
            elif failed and active_index == index:
                state = "failed"
            elif active_index is not None and index < active_index:
                state = "done"
            elif active_index == index:
                state = "active"
            else:
                state = "idle"
            stage.setProperty("state", state)
            stage.style().unpolish(stage)
            stage.style().polish(stage)

    def _show_enhanced_frame(self, frame):
        current = self.enhanced_preview
        self.enhanced_stack.removeWidget(current)
        current.deleteLater()
        self.enhanced_preview = EnhancementFramePreview(frame)
        self.enhanced_stack.insertWidget(0, self.enhanced_preview)
        self.enhanced_stack.setCurrentWidget(self.enhanced_preview)
        self.enhanced_caption.setText("Restored frame · ready to use for weapon detection")

    def _update_enhancement_progress(self, message: str):
        self.notice.setText(message)
        if "Connecting" in message:
            self._set_stage(0)
            self.process_status.setText("INITIALIZING ENGINE")
        elif "Running" in message:
            self._set_stage(1)
            self.process_status.setText("RESTORING FRAMES")
        elif "Validating" in message:
            self._set_stage(2)
            self.process_status.setText("VERIFYING OUTPUT")

    def _handle_run_or_apply(self):
        if self.enhanced_video_path is not None:
            self.accept()
            return

        self.run_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.engine_status.setText("ENHANCEMENT ACTIVE")
        self.preview_status.setText("RESTORING")
        self.enhanced_caption.setText("Processing animation · the finished result will replace it")
        self.enhanced_stack.setCurrentWidget(self.restoration_preview)
        self.restoration_preview.start()
        self._set_stage(0)
        self.process_status.setText("INITIALIZING ENGINE")
        self.notice.setText("Initializing BasicVSR++ video enhancement on GPU...")

        out_path = self.output_path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        self._worker = EnhancementWorker(self.video.path, out_path, self)
        self._worker.progress.connect(self._update_enhancement_progress)
        self._worker.succeeded.connect(self._enhancement_succeeded)
        self._worker.failed.connect(self._enhancement_failed)
        self._worker.start()

    def _enhancement_succeeded(self, enhanced_path: str, first_frame):
        if self._worker:
            self._worker.wait()
        self.enhanced_video_path = Path(enhanced_path)
        self.enhanced_first_frame = first_frame
        self.restoration_preview.stop()
        self._show_enhanced_frame(first_frame)
        self._set_stage(completed=True)
        self.engine_status.setText("ENHANCED VIDEO READY")
        self.preview_status.setText("ENHANCED")
        self.process_status.setText("RESTORATION COMPLETE")
        self.notice.setText("BasicVSR++ enhancement completed. Apply the enhanced video to use it for detection.")
        self.run_button.setText("Apply Enhanced Video")
        self.run_button.setEnabled(True)
        self.close_button.setEnabled(True)

    def _enhancement_failed(self, error_msg: str):
        if self._worker:
            self._worker.wait()
        self.restoration_preview.stop()
        self.enhanced_stack.setCurrentWidget(self.enhanced_preview)
        self._set_stage(0, failed=True)
        self.engine_status.setText("ENHANCEMENT INTERRUPTED")
        self.preview_status.setText("NOT ENHANCED")
        self.process_status.setText("ACTION REQUIRED")
        self.enhanced_caption.setText("Original video remains available for detection")
        self.notice.setText(f"Enhancement error: {error_msg}\nDetection can continue using the original video.")
        self.run_button.setText("Retry enhancement")
        self.run_button.setEnabled(True)
        self.close_button.setEnabled(True)

    def reject(self):
        if self._worker and self._worker.isRunning():
            self.notice.setText("BasicVSR++ is still processing this video. Please wait for it to finish.")
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            event.ignore()
        else:
            self.restoration_preview.stop()
            event.accept()


class DetectionConfigDialog(QDialog):
    def __init__(self, video: VideoInfo, threshold: int, parent=None, current_model: Path | None = None, videos=None):
        super().__init__(parent)
        videos = videos or [video]
        self.setObjectName("configurationDialog")
        self.setWindowTitle("Detection configuration")
        self.setModal(True)
        self.setMinimumWidth(640)
        self.resize(710, 800)
        self.setStyleSheet(load_stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        def note(text, name="dialogDetail"):
            widget = QLabel(text)
            widget.setWordWrap(True)
            widget.setTextFormat(Qt.TextFormat.PlainText)
            widget.setObjectName(name)
            return widget

        hero = QFrame()
        hero.setObjectName("configHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 20, 28, 20)
        hero_layout.setSpacing(7)
        hero_top = QHBoxLayout()
        hero_top.addWidget(note("DETECTION SETUP", "configEyebrow"))
        hero_top.addStretch()
        self.header_ready = note("READY", "configReadyBadge")
        hero_top.addWidget(self.header_ready)
        hero_layout.addLayout(hero_top)
        hero_layout.addWidget(note(
            f"Analyze {len(videos)} camera recordings" if len(videos) > 1 else "Analyze imported video",
            "configHeading",
        ))
        detail = (f"{len(videos)} cameras · {sum(v.frame_count for v in videos):,} total frames · One shared detector configuration"
                  if len(videos) > 1 else f"{video.path.name}\n{video.width} × {video.height} · {video.fps:.2f} FPS · {timecode(video.duration)}")
        source = QFrame()
        source.setObjectName("configSourceSummary")
        source_layout = QHBoxLayout(source)
        source_layout.setContentsMargins(11, 8, 11, 8)
        source_layout.setSpacing(10)
        source_layout.addWidget(note("VIDEO INPUT", "configSourceBadge"))
        source_layout.addWidget(note(detail, "configSourceDetail"), 1)
        hero_layout.addWidget(source)
        layout.addWidget(hero)

        content = QFrame()
        content.setObjectName("configBody")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 18)
        content_layout.setSpacing(13)
        scroll = QScrollArea()
        scroll.setObjectName("configScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("configScrollBody")
        stack = QVBoxLayout(body)
        stack.setContentsMargins(0, 0, 8, 0)
        stack.setSpacing(12)

        def card(title, description, status_text):
            frame = QFrame()
            frame.setObjectName("configCard")
            box = QVBoxLayout(frame)
            box.setContentsMargins(16, 14, 16, 14)
            box.setSpacing(10)
            header = QHBoxLayout()
            header.setSpacing(10)
            copy = QVBoxLayout()
            copy.setSpacing(2)
            copy.addWidget(note(title, "configCardTitle"))
            copy.addWidget(note(description, "configCardDescription"))
            header.addLayout(copy, 1)
            status = note(status_text, "configCardStatus")
            status.setWordWrap(False)
            status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header.addWidget(status)
            box.addLayout(header)
            stack.addWidget(frame)
            return box, status

        model_box, self.model_status = card(
            "Detection model", "Choose the trained model used to find weapons.", "CHECKING",
        )
        self.model_combo = ModelSelector()
        self.model_combo.setObjectName("dialogModelCombo")
        self.model_combo.setMinimumHeight(40)
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for _, path in discover_available_models():
            self.model_combo.addItem(path.name, str(path))
            self.model_combo.setItemData(self.model_combo.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole)
            if current_model and path.resolve() == Path(current_model).resolve():
                self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
        self.model_combo._update_tooltip(self.model_combo.currentIndex())
        model_box.addWidget(self.model_combo)
        model_box.addWidget(note("Faster R-CNN · Handgun and knife", "configMetaBadge"))
        model_ready = bool(self.model_combo.count())
        self.model_status.setText("READY" if model_ready else "MISSING")
        self.model_status.setProperty("ready", model_ready)
        self.header_ready.setText("READY" if model_ready else "MODEL REQUIRED")

        threshold_box, self.threshold_status = card(
            "Confidence threshold", "Only show detections at or above this confidence score.", "ADJUSTABLE",
        )
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(10, 95)
        self.slider.setValue(threshold)
        self.slider.setMinimumHeight(28)
        self.value_label = note(f"{threshold}%", "dialogThreshold")
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        threshold_box.addLayout(row)
        scale = QHBoxLayout()
        scale.addWidget(note("MORE SENSITIVE", "configScaleLabel"))
        scale.addStretch()
        self.threshold_hint = note("BALANCED REVIEW", "configThresholdHint")
        scale.addWidget(self.threshold_hint)
        scale.addStretch()
        scale.addWidget(note("STRICTER", "configScaleLabel"))
        threshold_box.addLayout(scale)

        def update_threshold(value):
            self.value_label.setText(f"{value}%")
            self.threshold_hint.setText(
                "HIGH RECALL" if value < 40 else "BALANCED REVIEW" if value <= 70 else "HIGH PRECISION"
            )
        self.slider.valueChanged.connect(update_threshold)
        update_threshold(threshold)

        filters, self.filters_status = card(
            "Detection checks", "Use additional checks to reduce false detections.", "2 ACTIVE",
        )

        def filter_option(title, description):
            frame = QFrame()
            frame.setObjectName("configFilter")
            box = QVBoxLayout(frame)
            box.setContentsMargins(12, 10, 12, 10)
            box.setSpacing(5)
            top = QHBoxLayout()
            checkbox = QCheckBox(title)
            checkbox.setChecked(True)
            top.addWidget(checkbox)
            top.addStretch()
            status = note("ACTIVE", "configFilterStatus")
            top.addWidget(status)
            box.addLayout(top)
            box.addWidget(note(description, "configFilterDescription"))
            filters.addWidget(frame)
            return frame, checkbox, status

        self.cctv_filter, self.cctv_intel_checkbox, self.cctv_status = filter_option(
            "CCTV intelligence", "Checks person proximity, motion and object scale to filter background detections.",
        )
        self.temporal_filter, self.temporal_checkbox, self.temporal_status = filter_option(
            "Temporal consistency", "Checks persistence across nearby frames independently for each camera recording.",
        )

        def refresh_filter(frame, status, checked, available=True):
            frame.setProperty("active", checked and available)
            status.setText("ACTIVE" if checked and available else "OFF")
            for widget in (frame, status):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

        self.cctv_intel_checkbox.toggled.connect(
            lambda checked: refresh_filter(self.cctv_filter, self.cctv_status, checked))
        self.temporal_checkbox.toggled.connect(
            lambda checked: refresh_filter(self.temporal_filter, self.temporal_status, checked,
                                           self.temporal_checkbox.isEnabled()))

        def toggle_temporal(enabled):
            self.temporal_checkbox.setEnabled(enabled)
            if not enabled:
                self.temporal_checkbox.setChecked(False)
            refresh_filter(self.temporal_filter, self.temporal_status,
                           self.temporal_checkbox.isChecked(), enabled)
            active = int(self.cctv_intel_checkbox.isChecked()) + int(self.temporal_checkbox.isChecked())
            self.filters_status.setText(f"{active} ACTIVE" if active else "OPTIONAL")
        self.cctv_intel_checkbox.toggled.connect(toggle_temporal)
        self.temporal_checkbox.toggled.connect(
            lambda _: self.filters_status.setText(
                f"{int(self.cctv_intel_checkbox.isChecked()) + int(self.temporal_checkbox.isChecked())} ACTIVE"
            ))
        refresh_filter(self.cctv_filter, self.cctv_status, True)
        refresh_filter(self.temporal_filter, self.temporal_status, True)
        defaults = load_preferences()
        self.cctv_intel_checkbox.setChecked(defaults.cctv_intelligence)
        self.temporal_checkbox.setChecked(defaults.temporal_consistency)

        self.enhancement_notice = note(
            "INPUT FOOTAGE\nNo enhanced video has been created. Detection uses the recordings currently imported. "
            "Apply BasicVSR++ enhancement before analysis if needed.",
            "enhancementNotice")
        stack.addWidget(self.enhancement_notice)
        scroll.setWidget(body)
        content_layout.addWidget(scroll, 1)

        actions = QFrame()
        actions.setObjectName("configActions")
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(14, 11, 12, 11)
        actions_layout.setSpacing(12)
        action_copy = QVBoxLayout()
        action_copy.setSpacing(1)
        action_copy.addWidget(note("READY FOR ANALYSIS", "configActionTitle"))
        action_copy.addWidget(note(
            f"{sum(v.frame_count for v in videos):,} frames queued for detection",
            "configActionDetail",
        ))
        actions_layout.addLayout(action_copy, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start_button = buttons.addButton(
            "Analyze all cameras" if len(videos) > 1 else "Start detection",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.start_button.setObjectName("primaryButton")
        self.start_button.setEnabled(self.model_combo.count() > 0)
        self.start_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        actions_layout.addWidget(buttons)
        content_layout.addWidget(actions)
        layout.addWidget(content, 1)

    @property
    def selected_model(self) -> Path | None:
        data = self.model_combo.currentData()
        return Path(data) if data else None


class PulseDot(QWidget):
    """Continuously animated activity indicator for the detection hero."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.setFixedSize(12, 12)
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._advance)

    def _advance(self):
        self.phase = (self.phase + 0.045) % 1.0
        self.update()

    def showEvent(self, event):
        self._animation_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._animation_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        wave = (sin(self.phase * tau) + 1) / 2
        glow = QColor("#b9c0ff")
        glow.setAlpha(35 + round(wave * 65))
        painter.setBrush(glow)
        radius = 4.2 + wave * 1.2
        painter.drawEllipse(QRectF(6 - radius, 6 - radius, radius * 2, radius * 2))
        core = QColor("#dfe2ff")
        core.setAlpha(185 + round(wave * 70))
        painter.setBrush(core)
        painter.drawEllipse(QRectF(3.5, 3.5, 5, 5))


class ScanWave(QWidget):
    """Low-cost 60 FPS waveform showing active frame analysis."""
    bar_count = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.setFixedSize(72, 22)
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._advance)

    def _advance(self):
        self.phase = (self.phase + 0.018) % 1.0
        self.update()

    def showEvent(self, event):
        self._animation_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._animation_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        bar_width, gap, baseline = 4.0, 3.5, self.height() - 2.0
        for index in range(self.bar_count):
            wave = (sin(self.phase * tau + index * 0.72) + 1) / 2
            height = 4.0 + wave * 15.0
            color = QColor("#e1e3ff" if wave > 0.72 else "#8589e7")
            color.setAlpha(155 + round(wave * 100))
            painter.setBrush(color)
            x = index * (bar_width + gap)
            painter.drawRoundedRect(QRectF(x, baseline - height, bar_width, height), 2, 2)


class ProcessingDialog(QDialog):
    def __init__(self, total_frames: int, parent=None, *, camera_count: int = 1):
        super().__init__(parent)
        self.total_frames = total_frames
        self.camera_count = camera_count
        self._started = monotonic()
        self._allow_close = False
        self._last_percent = -1
        self.setObjectName("processingDialog")
        self.setWindowTitle("Weapon detection in progress")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setFixedWidth(660)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        hero = QFrame()
        hero.setObjectName("processingHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 22, 28, 21)
        hero_layout.setSpacing(7)
        activity_row = QHBoxLayout()
        activity_row.setSpacing(6)
        self.activity_dot = PulseDot()
        activity_row.addWidget(self.activity_dot)
        self.activity = QLabel("DETECTION ACTIVE")
        self.activity.setObjectName("processingActivity")
        activity_row.addWidget(self.activity)
        activity_row.addStretch()
        self.engine_badge = QLabel("FASTER R-CNN · INITIALIZING")
        self.engine_badge.setObjectName("processingEngine")
        activity_row.addWidget(self.engine_badge)
        hero_layout.addLayout(activity_row)
        heading = QLabel("Analyzing video" if camera_count == 1 else f"Analyzing {camera_count} camera recordings")
        heading.setObjectName("processingHeading")
        hero_layout.addWidget(heading)
        context = QLabel("Scanning footage for handgun and knife observations.")
        context.setObjectName("processingContext")
        hero_layout.addWidget(context)
        signal = QFrame()
        signal.setObjectName("processingSignal")
        signal_layout = QHBoxLayout(signal)
        signal_layout.setContentsMargins(10, 5, 10, 5)
        signal_layout.setSpacing(4)
        self.scan_wave = ScanWave()
        signal_layout.addWidget(self.scan_wave, 0, Qt.AlignmentFlag.AlignVCenter)
        signal_layout.addSpacing(7)
        signal_text = QLabel("LIVE FRAME ANALYSIS")
        signal_text.setObjectName("processingSignalText")
        signal_layout.addWidget(signal_text)
        signal_layout.addStretch()
        hero_layout.addWidget(signal)
        layout.addWidget(hero)
        body = QFrame()
        body.setObjectName("processingBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(28, 20, 28, 22)
        body_layout.setSpacing(11)
        stages = QFrame()
        stages.setObjectName("processingStages")
        stages_layout = QHBoxLayout(stages)
        stages_layout.setContentsMargins(5, 5, 5, 5)
        stages_layout.setSpacing(5)
        self.stage_labels = []
        for text in ("01  MODEL READY", "02  FRAME SCAN", "03  VERIFY OUTPUT"):
            step = QLabel(text)
            step.setObjectName("processingStep")
            step.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stages_layout.addWidget(step, 1)
            self.stage_labels.append(step)
        body_layout.addWidget(stages)
        row = QHBoxLayout()
        self.phase = QLabel("PREPARING DETECTOR")
        self.phase.setObjectName("processingPhase")
        row.addWidget(self.phase)
        row.addStretch()
        self.percent = QLabel("Starting…")
        self.percent.setObjectName("processingPercent")
        row.addWidget(self.percent)
        body_layout.addLayout(row)
        self.message = QLabel("Loading the trained detection model.")
        self.message.setObjectName("processingMessage")
        self.message.setWordWrap(True)
        body_layout.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(14)
        body_layout.addWidget(self.progress)
        progress_meta = QHBoxLayout()
        self.frames = QLabel(f"Preparing to process {total_frames:,} frames")
        self.frames.setObjectName("processingFrames")
        progress_meta.addWidget(self.frames)
        progress_meta.addStretch()
        self.eta = QLabel("ETA calculating")
        self.eta.setObjectName("processingEta")
        progress_meta.addWidget(self.eta)
        body_layout.addLayout(progress_meta)
        note = QLabel("The annotated video and detection summary will appear when analysis finishes.")
        note.setObjectName("processingNote")
        note.setWordWrap(True)
        body_layout.addWidget(note)
        footer = QHBoxLayout()
        self.elapsed = QLabel("ELAPSED  00:00")
        self.elapsed.setObjectName("processingElapsed")
        footer.addWidget(self.elapsed)
        footer.addStretch()
        pipeline = QLabel("LOCAL FORENSIC PIPELINE")
        pipeline.setObjectName("processingPipeline")
        footer.addWidget(pipeline)
        body_layout.addLayout(footer)
        layout.addWidget(body)
        self._progress_animation = QPropertyAnimation(self.progress, b"value", self)
        self._progress_animation.setDuration(320)
        self._progress_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._set_stage(0)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_elapsed)
        self._timer.start(500)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent:
            center = parent.window().frameGeometry().center()
            frame = self.frameGeometry()
            self.move(center.x() - frame.width() // 2, center.y() - frame.height() // 2)

    def _update_elapsed(self):
        seconds = int(monotonic() - self._started)
        self.elapsed.setText(f"ELAPSED  {seconds // 60:02d}:{seconds % 60:02d}")
        if 0 < self._last_percent < 100 and seconds:
            remaining = max(0, round(seconds * (100 - self._last_percent) / self._last_percent))
            self.eta.setText(f"EST. REMAINING  {remaining // 60:02d}:{remaining % 60:02d}")

    def _set_stage(self, active: int, *, complete: bool = False):
        for index, step in enumerate(self.stage_labels):
            state = "done" if complete or index < active else "active" if index == active else "pending"
            step.setProperty("state", state)
            step.style().unpolish(step)
            step.style().polish(step)

    def update_progress(self, message: str, percent: int):
        self.message.setText(message)
        self._last_percent = percent
        if percent < 0:
            self.phase.setText("PREPARING DETECTOR")
            self.percent.setText("Starting…")
            self.progress.setRange(0, 0)
            self.frames.setText(f"Preparing to process {self.total_frames:,} frames")
            self.eta.setText("ETA calculating")
            self.engine_badge.setText("FASTER R-CNN · INITIALIZING")
            self._set_stage(0)
        else:
            verifying = percent >= 99
            self.phase.setText(
                "VERIFYING OUTPUT" if verifying else
                "SCANNING RECORDINGS" if self.camera_count > 1 else "SCANNING VIDEO"
            )
            self.percent.setText(f"{percent}%")
            self.progress.setRange(0, 100)
            start = max(0, self.progress.value())
            self._progress_animation.stop()
            self._progress_animation.setStartValue(start)
            self._progress_animation.setEndValue(percent)
            self._progress_animation.start()
            current = min(self.total_frames, round(self.total_frames * percent / 100))
            self.frames.setText(f"Processing frame {current:,} of {self.total_frames:,} · {percent}% complete")
            self.engine_badge.setText("FASTER R-CNN · LIVE")
            self._set_stage(2 if verifying else 1, complete=percent >= 100)
            if percent >= 100:
                self.eta.setText("COMPLETE")

    def accept(self):
        self._allow_close = True
        self._timer.stop()
        super().accept()

    def reject(self):
        pass  # Processing cannot be cancelled safely by the detection pipeline.

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
        else:
            event.ignore()


class ForensicReportDialog(QDialog):
    def __init__(self, result: VideoAnalysisResult, parent=None, camera_id: str | None = None):
        super().__init__(parent)
        observations = ReviewStore.for_result(result).observations
        self.setObjectName("forensicReportDialog")
        self.setWindowTitle("Forensic Analysis Report")
        self.resize(980, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 21, 24, 20)
        layout.setSpacing(11)
        top = QHBoxLayout()
        heading_box = QVBoxLayout()
        heading = QLabel("Forensic Analysis Report")
        heading.setObjectName("dialogHeading")
        subtitle = QLabel(f"{result.video.path.name} · Generated {result.completed_at_utc}")
        subtitle.setObjectName("dialogDetail")
        heading_box.addWidget(heading)
        heading_box.addWidget(subtitle)
        top.addLayout(heading_box)
        top.addStretch()
        badge = QLabel(f"{sum(row['analystReview'] is not None for row in observations)} / {len(observations)} REVIEWED")
        badge.setObjectName("reviewBadge")
        top.addWidget(badge)
        layout.addLayout(top)

        disclaimer = QLabel("This is a system-generated report. All detections and validation results are subject to human analyst review and should be treated as reviewable observations, not conclusive findings.")
        disclaimer.setObjectName("reportObservation")
        disclaimer.setWordWrap(True)
        layout.addWidget(disclaimer)

        # Calculate TCR and MCCR metrics from metric_input.csv
        metric_path = getattr(result, "metric_input_path", None)
        if not metric_path or not Path(metric_path).exists():
            candidate = result.output_path.parent / "metric_input.csv"
            metric_path = candidate if candidate.exists() else None

        metrics = {}
        if metric_path:
            from mockup_ui.report_metrics import calculate_report_metrics
            metrics = calculate_report_metrics(Path(metric_path))

        tcr = metrics.get("tcr", {})
        mccr = metrics.get("mccr", {})

        if tcr.get("value_percent") is not None:
            tcr_str = (
                f"Rate: {tcr['value_percent']:.2f}%\n"
                f"Supported (N_TS): {tcr.get('n_ts', 0)} · Isolated: {tcr.get('n_isolated', 0)}\n"
                f"Total Eligible (N_TE): {tcr.get('n_te', 0)}"
            )
        elif tcr.get("status") == "per_video":
            tcr_str = "Per-Video Calculation\nSee detailed forensic log records"
        else:
            tcr_str = f"Status: N/A\n{tcr.get('reason', 'Pipeline records not available for this run')}"

        if mccr.get("value_percent") is not None:
            mccr_str = (
                f"Rate: {mccr['value_percent']:.2f}%\n"
                f"Corroborated (N_CC): {mccr.get('n_cc', 0)} / {mccr.get('n_mc', 0)} eligible"
            )
        else:
            mccr_str = (
                "Status: N/A (Single Camera Feed)\n"
                "Requires dual-camera layout with concurrent timestamps"
            )

        info = QGridLayout()
        values = (
            ("VIDEO INFORMATION", f"{result.video.width} × {result.video.height} · {result.video.fps:.2f} FPS\n"
                                  f"{timecode(result.video.duration)} · {result.video.frame_count:,} frames"),
            ("DETECTION CONFIGURATION", f"Threshold: {result.threshold:.0%}\nModel: {Path(result.model_path).name}"),
            ("DETECTION SUMMARY", f"{len(result.detections):,} observations · {result.positive_frames:,} positive frames\n"
                                  f"Handgun: {result.counts.get('handgun', 0):,} · Knife: {result.counts.get('knife', 0):,}"),
            ("PROCESSING", f"{result.analyzed_frames:,} frames analyzed on {result.device.upper()}\n"
                           f"Completed in {result.elapsed_seconds:.1f} seconds"),
            ("TEMPORAL CONSISTENCY (TCR)", tcr_str),
            ("MULTI-CAMERA CORROBORATION (MCCR)", mccr_str),
        )
        for index, (title, value) in enumerate(values):
            card = QFrame()
            card.setObjectName("reportCard")
            card_layout = QVBoxLayout(card)
            label = QLabel(title)
            label.setObjectName("sectionTitle")
            content = QLabel(value)
            content.setObjectName("dialogDetail")
            content.setWordWrap(True)
            card_layout.addWidget(label)
            card_layout.addWidget(content)
            info.addWidget(card, index // 2, index % 2)
        layout.addLayout(info)
        layout.addWidget(QLabel("DETECTION TIMELINE"))
        self.model = ReportTableModel(result.detections, self, observations)
        table = QTableView()
        table.setModel(self.model)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((95, 55, 80, 90, 155, 120, 220, 210)):
            table.setColumnWidth(column, width)
        layout.addWidget(table, 1)
        summary = (f"Automated analysis recorded {len(result.detections):,} handgun/knife observation(s) "
                   f"across {result.positive_frames:,} frame(s). " if result.detections else
                   "No handgun or knife observation met the configured threshold. ")
        source_note = (f"Camera ID: {camera_id}. Recording timestamps are not recorded." if camera_id else
                       "Camera ID and recording timestamps are not recorded.")
        observation = QLabel(summary + "All observations, including rejected and uncertain analyst decisions, remain in this report. " + source_note)
        observation.setObjectName("reportObservation")
        observation.setWordWrap(True)
        layout.addWidget(observation)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)


class InferenceWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str, str)
    progress = Signal(str, int)
    def __init__(self, bridge, video, threshold, parent=None):
        super().__init__(parent)
        self.bridge, self.video, self.threshold = bridge, video, threshold

    def run(self):
        try:
            self.succeeded.emit(self.bridge.analyze_video(self.video, self.threshold, self.progress.emit))
        except (VideoInputError, ModelSetupError) as exc:
            self.failed.emit(str(exc), traceback.format_exc())
        except Exception:
            self.failed.emit(
                "Video analysis could not finish. Check the trained checkpoint, video and available disk space. "
                "The original video remains available; technical details are in mockup_ui/temp/mockup.log.",
                traceback.format_exc(),
            )


class DetectionCard(QFrame):
    def __init__(self, detection: dict, index: int):
        super().__init__()
        self.setObjectName("detectionCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 11, 13, 11)
        layout.setSpacing(7)

        top = QHBoxLayout()
        name = QLabel(f"{index:02d}  {detection['class_name'].title()}")
        name.setObjectName("detectionName")
        score = QLabel(f"{detection['confidence']:.1%}")
        score.setObjectName("confidencePill")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(score)
        layout.addLayout(top)
        box = detection["box"]
        coords = QLabel(f"Bounding box   [{box[0]}, {box[1]}, {box[2]}, {box[3]}]")
        coords.setObjectName("mutedText")
        coords.setWordWrap(True)
        layout.addWidget(coords)


class MainWindow(QMainWindow):
    def __init__(self, initial_video: str | None = None):
        super().__init__()
        restore_theme()
        if "Inter Variable" not in QFontDatabase.families():
            QFontDatabase.addApplicationFont(str(MOCKUP_DIR / "assets" / "InterVariable.ttf"))
        self.bridge = ModelBridge()
        self.source_path: Path | None = None
        self.video_info: VideoInfo | None = None
        self.result: VideoAnalysisResult | None = None
        self._enhanced_video_active = False
        self._detections_by_frame = {}
        self._thread: QThread | None = None
        self._analysis_requested = False
        self._config_dialog: DetectionConfigDialog | None = None
        self._processing_dialog: ProcessingDialog | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._model_retry = QTimer(self)
        self._model_retry.setInterval(2000)
        self._model_retry.timeout.connect(self.analyze)
        self._build_ui()
        self._apply_style()
        self._update_model_label()
        self._set_ready("Import a CCTV video to begin.")
        if initial_video:
            self.load_video(initial_video)

    def _build_ui(self):
        self.setWindowTitle("Forensikada · Video Weapon Detection")
        self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.resize(1450, 880)
        self.setMinimumSize(1040, 690)

        shell = QWidget()
        shell.setObjectName("shell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self._header())
        self.mode_tabs = QTabBar()
        self.mode_tabs.setObjectName("modeTabs")
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.addTab("Single video")
        self.mode_tabs.addTab("Multi-camera")
        self.mode_tabs.setTabEnabled(1, False)
        self.mode_tabs.currentChanged.connect(self._mode_changed)
        shell_layout.addWidget(self.mode_tabs)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._body())
        shell_layout.addWidget(self.pages, 1)
        self.setCentralWidget(shell)

    def _header(self):
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 9, 18, 9)
        layout.setSpacing(10)
        logo = QLabel()
        logo.setObjectName("brandLogo")
        logo.setFixedSize(92, 44)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(QPixmap(str(BRAND_LOGO_PATH)).scaled(
            90, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ))
        layout.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel("Forensikada")
        title.setObjectName("brandTitle")
        sub = QLabel("CCTV FORENSIC VIDEO ANALYSIS")
        sub.setObjectName("brandSub")
        brand.addWidget(title)
        brand.addWidget(sub)
        layout.addLayout(brand)
        layout.addStretch()

        self.open_button = QPushButton("Import videos")
        self.open_button.setObjectName("primaryButton")
        self.open_button.setToolTip("Select one video for single-camera analysis, or multiple videos for the multi-camera workspace.")
        self.open_button.setIcon(QIcon(str(MOCKUP_DIR / "assets" / "import-image.png")))
        self.open_button.clicked.connect(self.choose_video)
        self.close_videos_button = QPushButton("Close videos")
        self.close_videos_button.setObjectName("headerButton")
        self.close_videos_button.setToolTip("Close the current recordings and clear this analysis. Saved files are kept.")
        self.close_videos_button.setEnabled(False)
        self.close_videos_button.clicked.connect(self.close_videos)
        self.report_button = QPushButton("Forensic report")
        self.report_button.setObjectName("headerButton")
        self.report_button.setToolTip("Review the completed forensic analysis inside the application.")
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self.show_forensic_report)
        self.save_button = QPushButton("Save video + report")
        self.save_button.setToolTip("Save the annotated video, forensic report, and structured detection records.")
        self.save_button.setObjectName("headerButton")
        self.save_button.setIcon(QIcon(str(MOCKUP_DIR / "assets" / "save-result.svg")))
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_current_result)
        for button in (self.open_button, self.close_videos_button, self.report_button, self.save_button):
            button.setIconSize(QSize(18, 18))
            button.setMinimumHeight(42)
            layout.addWidget(button)
        self.settings_button = QPushButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setIcon(QIcon(str(ASSET_DIR / "settings.svg")))
        self.settings_button.setIconSize(QSize(20, 20))
        self.settings_button.setFixedSize(42, 42)
        self.settings_button.setAccessibleName("System settings")
        self.settings_button.setToolTip("System settings")
        self.settings_button.clicked.connect(self.show_settings)
        layout.addWidget(self.settings_button)
        return header

    def set_theme(self, theme, *, persist=True):
        apply_theme(self, theme, persist=persist)

    def show_settings(self):
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        self._settings_dialog = SettingsDialog(self)
        self._settings_dialog.finished.connect(self._settings_finished)
        self._settings_dialog.open()

    def _settings_finished(self, code):
        dialog = self._settings_dialog
        self._settings_dialog = None
        if dialog is None:
            return
        if code == QDialog.DialogCode.Accepted and dialog.saved_preferences is not None:
            self.set_theme(dialog.saved_preferences.theme, persist=False)
            if self.video_info is None and self._thread is None:
                self.threshold_slider.setValue(dialog.saved_preferences.confidence_percent)
        dialog.deleteLater()

    def _body(self):
        splitter = DetectionPageShell("mainSplitter")
        splitter.addWidget(self._sidebar())
        splitter.addWidget(self._workspace())
        splitter.addWidget(self._summary_panel())
        splitter.finish()
        return splitter

    def _sidebar(self):
        panel, layout = page_panel("sidePanel")
        layout.addWidget(self._section("EVIDENCE SOURCE"))
        evidence = QFrame()
        evidence.setObjectName("selectedEvidence")
        evidence_layout = QVBoxLayout(evidence)
        evidence_layout.setContentsMargins(13, 8, 13, 8)
        self.source_name = QLabel("No video selected")
        self.source_name.setTextFormat(Qt.TextFormat.PlainText)
        self.source_name.setObjectName("evidenceTitle")
        self.source_name.setWordWrap(True)
        self.source_meta = QLabel("Import CCTV footage")
        self.source_meta.setWordWrap(True)
        self.source_meta.setObjectName("mutedText")
        evidence_layout.addWidget(self.source_name)
        evidence_layout.addWidget(self.source_meta)
        layout.addWidget(evidence)

        enhancement, enhancement_layout = enhancement_heading()
        self.enhancement_status = QLabel("Import a video to enhance it.")
        self.enhancement_status.setObjectName("enhancementStatus")
        self.enhancement_status.setWordWrap(True)
        enhancement_layout.addWidget(self.enhancement_status)
        self.enhancement_button = QPushButton("Enhance video")
        self.enhancement_button.setObjectName("cameraEnhanceButton")
        self.enhancement_button.setMinimumHeight(30)
        self.enhancement_button.setToolTip("Open the BasicVSR++ enhancement preview.")
        self.enhancement_button.setEnabled(False)
        self.enhancement_button.clicked.connect(self.show_video_enhancement)
        enhancement_layout.addWidget(self.enhancement_button)
        layout.addWidget(enhancement)

        layout.addWidget(self._section("VIEW MODE"))
        toggle = QFrame()
        toggle.setObjectName("toggleBox")
        toggle_layout = QHBoxLayout(toggle)
        toggle_layout.setContentsMargins(4, 4, 4, 4)
        toggle_layout.setSpacing(0)
        self.original_button = QPushButton("Original")
        self.detected_button = QPushButton("Detected")
        self.original_button.setCheckable(True)
        self.detected_button.setCheckable(True)
        self.original_button.setChecked(True)
        self.detected_button.setEnabled(False)
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        self.view_group.addButton(self.original_button)
        self.view_group.addButton(self.detected_button)
        self.original_button.clicked.connect(self.show_original)
        self.detected_button.clicked.connect(self.show_detected)
        toggle_layout.addWidget(self.original_button)
        toggle_layout.addWidget(self.detected_button)
        layout.addWidget(toggle)

        layout.addWidget(self._section("CONFIDENCE THRESHOLD"))
        threshold_row = QHBoxLayout()
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(10, 95)
        self.threshold_slider.setValue(load_preferences().confidence_percent)
        self.threshold_value = QLabel(f"{self.threshold_slider.value()}%")
        self.threshold_value.setObjectName("thresholdValue")
        self.threshold_slider.valueChanged.connect(
            lambda value: self.threshold_value.setText(f"{value}%")
        )
        threshold_row.addWidget(self.threshold_slider, 1)
        threshold_row.addWidget(self.threshold_value)
        layout.addLayout(threshold_row)
        self.analyze_button = QPushButton("Analyze video")
        self.analyze_button.setObjectName("primaryButton")
        self.analyze_button.setEnabled(False)
        self.analyze_button.setToolTip("Review the threshold and start detection.")
        self.analyze_button.clicked.connect(self.show_detection_configuration)
        layout.addWidget(self.analyze_button)
        self.review_button = QPushButton("Observation review")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self.show_observation_review)
        layout.addWidget(self.review_button)
        self.open_review_button = QPushButton("Open saved review…")
        self.open_review_button.clicked.connect(self.open_saved_review)
        layout.addWidget(self.open_review_button)

        layout.addWidget(self._section("ANALYSIS STATUS"))
        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(12, 8, 12, 8)
        self.status_title = QLabel("Ready")
        self.status_title.setObjectName("statusTitle")
        self.status_detail = QLabel("")
        self.status_detail.setObjectName("mutedText")
        self.status_detail.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(5)
        self.progress.hide()
        status_layout.addWidget(self.status_title)
        status_layout.addWidget(self.status_detail)
        status_layout.addWidget(self.progress)
        layout.addWidget(status_card)
        layout.addStretch()

        model_heading = self._section("TRAINED MODEL")
        layout.addWidget(model_heading)
        self.model_label = QLabel("No checkpoint found")
        self.model_label.setTextFormat(Qt.TextFormat.PlainText)
        self.model_label.setObjectName("mutedText")
        self.model_label.setWordWrap(True)
        layout.addWidget(self.model_label)
        return panel

    def _workspace(self):
        panel, layout = page_panel("workspace")
        self.workspace_title = QLabel("VIDEO WEAPON DETECTION")
        self.workspace_title.setObjectName("workspaceTitle")
        self.workspace_meta = QLabel("Original evidence")
        self.workspace_meta.setObjectName("mutedText")
        heading_row = workspace_heading(self.workspace_title, self.workspace_meta)
        layout.addLayout(heading_row)

        self.player = VideoPlayer()
        self.canvas = self.player.canvas
        self.canvas.browse_requested.connect(self.choose_video)
        self.canvas.file_dropped.connect(self.load_video)
        self.player.frame_changed.connect(self._frame_changed)
        self.player.failed.connect(lambda message: self._show_error("Playback unavailable", message))
        layout.addWidget(self.player, 1)
        observations = QFrame()
        observations.setObjectName("observationsPanel")
        observations_layout = QVBoxLayout(observations)
        observations_layout.setContentsMargins(10, 10, 10, 8)
        observations_layout.setSpacing(5)
        observations_layout.addWidget(self._section("DETECTED OBJECTS · CLICK A ROW TO REVIEW THE FRAME"))
        self.observation_model = DetectionTableModel(self)
        self.observations = QTableView()
        self.observations.setModel(self.observation_model)
        self.observations.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.observations.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.observations.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.observations.verticalHeader().hide()
        self.observations.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.observations.setMinimumHeight(130)
        self.observations.setMaximumHeight(170)
        self.observations.clicked.connect(self._jump_to_detection)
        observations_layout.addWidget(self.observations)
        layout.addWidget(observations)
        footer = QHBoxLayout()
        safety = QLabel("Automated detections require human review.")
        safety.setObjectName("safetyNote")
        self.dimensions_label = QLabel("—")
        self.dimensions_label.setObjectName("mutedText")
        footer.addWidget(safety)
        footer.addStretch()
        footer.addWidget(self.dimensions_label)
        layout.addLayout(footer)
        return panel

    def _summary_panel(self):
        panel, layout = page_panel("summaryPanel")
        layout.addWidget(self._section("DETECTION SUMMARY"))
        metrics, (self.total_metric, self.handgun_metric, self.knife_metric) = summary_metrics()
        layout.addLayout(metrics)

        self.summary_message = QLabel("Import a video, review the confidence threshold, then start detection.")
        self.summary_message.setObjectName("summaryMessage")
        self.summary_message.setWordWrap(True)
        layout.addWidget(self.summary_message)
        layout.addWidget(self._section("CURRENT FRAME"))
        self.frame_message = QLabel("No video analyzed yet.")
        self.frame_message.setObjectName("mutedText")
        self.frame_message.setWordWrap(True)
        layout.addWidget(self.frame_message)

        scroll = QScrollArea()
        scroll.setObjectName("resultsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(8)
        self.results_layout.addStretch()
        scroll.setWidget(self.results_container)
        layout.addWidget(scroll, 1)
        self.run_meta = QLabel("Counts are observations across frames; a weapon may appear in several frames.")
        self.run_meta.setObjectName("mutedText")
        self.run_meta.setWordWrap(True)
        layout.addWidget(self.run_meta)
        return panel

    @staticmethod
    def _section(text: str):
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _apply_style(self):
        apply_theme(self, current_theme(), persist=False)

    def choose_video(self):
        if self._thread or (getattr(self, "_multi_camera_window", None) and self._multi_camera_window.worker):
            return
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "Import CCTV recordings (select one or more)", str(ROOT_DIR),
            "Videos (*.mp4 *.avi *.mov *.mkv *.webm *.m4v)",
        )
        if len(filenames) == 1:
            self.load_video(filenames[0])
        elif filenames:
            self.open_multi_camera(filenames)

    def close_videos(self):
        """End the current session without deleting any source or saved output."""
        multi_camera = getattr(self, "_multi_camera_window", None)
        if self._thread or (multi_camera and multi_camera.worker):
            return False
        self._model_retry.stop()
        self._analysis_requested = False
        if self._config_dialog:
            self._config_dialog.reject()
        if self._processing_dialog:
            self._processing_dialog.accept()
            self._processing_dialog.deleteLater()
            self._processing_dialog = None
        if multi_camera:
            multi_camera.close()
            self.pages.removeWidget(multi_camera)
            self._multi_camera_window = None
            multi_camera.deleteLater()
        self.mode_tabs.setCurrentIndex(0)
        self.mode_tabs.setTabEnabled(1, False)
        self.pages.setCurrentIndex(0)
        self.player.reset()
        self.video_info = self.source_path = self.result = None
        self._enhanced_video_active = False
        self._clear_results()
        self.source_name.setText("No video selected")
        self.source_name.setToolTip("")
        self.source_meta.setText("Import CCTV footage")
        self.dimensions_label.setText("No video loaded")
        self.workspace_meta.setText("Ready to import")
        self.original_button.setChecked(True)
        self.original_button.setEnabled(True)
        for button in (self.detected_button, self.analyze_button, self.enhancement_button, self.review_button):
            button.setEnabled(False)
        self.open_review_button.setEnabled(True)
        self.threshold_slider.setEnabled(True)
        self.progress.hide()
        self.threshold_slider.setValue(load_preferences().confidence_percent)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.enhancement_status.setText("Import a video to enhance it.")
        self._update_model_label()
        self._set_ready("Import a CCTV video to begin a new analysis.")
        self._sync_header_actions()
        return True

    def open_multi_camera(self, filenames):
        from mockup_ui.multi_camera_panel import MultiCameraDialog
        existing = getattr(self, "_multi_camera_window", None)
        if self._thread or (existing and existing.worker):
            return
        try:
            videos = [read_video(path) for path in dict.fromkeys(filenames)]
            if len(videos) < 2:
                raise ValueError("Select at least two different camera recordings.")
            window = MultiCameraDialog(videos, self)
        except (VideoInputError, OSError, ValueError) as exc:
            self._show_error("Camera recordings unavailable", str(exc))
            return
        self.close_videos()
        self._multi_camera_window = window
        window.state_changed.connect(self._sync_header_actions)
        self.pages.addWidget(window)
        self.mode_tabs.setTabEnabled(1, True)
        self.mode_tabs.setCurrentIndex(1)
        self._sync_header_actions()

    def _mode_changed(self, index):
        if hasattr(self, "pages") and index < self.pages.count():
            previous = self.pages.currentWidget()
            target = self.pages.widget(index)
            previous_shell = previous if isinstance(previous, DetectionPageShell) else getattr(previous, "page_shell", None)
            target_shell = target if isinstance(target, DetectionPageShell) else getattr(target, "page_shell", None)
            if previous_shell and target_shell and previous_shell is not target_shell:
                target_shell.setSizes(previous_shell.sizes())
        if index == 0:
            multi_camera = getattr(self, "_multi_camera_window", None)
            if multi_camera:
                multi_camera.pause()
        else:
            self.player.pause()
        if hasattr(self, "pages") and index < self.pages.count():
            self.pages.setCurrentIndex(index)
        self._sync_header_actions()

    def _sync_header_actions(self):
        if not hasattr(self, "report_button"):
            return
        multi_camera = getattr(self, "_multi_camera_window", None)
        busy = bool(self._thread or (multi_camera and multi_camera.worker))
        self.open_button.setEnabled(not busy)
        self.close_videos_button.setEnabled(not busy and bool(self.video_info or multi_camera))
        if hasattr(self, "mode_tabs"):
            self.mode_tabs.setEnabled(not busy)
        completed = bool(multi_camera.results) if self.mode_tabs.currentIndex() == 1 and multi_camera else self.result is not None
        self.report_button.setEnabled(completed and not busy)
        self.save_button.setEnabled(completed and not busy)

    def load_video(self, filename):
        if self._thread or (getattr(self, "_multi_camera_window", None) and self._multi_camera_window.worker):
            return
        self._model_retry.stop()
        if self._config_dialog:
            self._config_dialog.reject()
        try:
            video = read_video(filename)
        except (VideoInputError, OSError, ValueError) as exc:
            self._show_error("Video unavailable", str(exc))
            return
        self.close_videos()
        try:
            self.player.open(video.path)
            self.player.set_locked(True)
        except (VideoInputError, OSError, ValueError) as exc:
            self._show_error("Video unavailable", str(exc))
            return
        self.video_info = video
        self._enhanced_video_active = False
        self.mode_tabs.setCurrentIndex(0)
        self.source_path = video.path
        self.result = None
        self._analysis_requested = False
        self.review_button.setEnabled(False)
        self.source_name.setText(video.path.name)
        self.source_name.setToolTip(str(video.path))
        self.source_meta.setText(f"{video.width} × {video.height} pixels\n{video.fps:.2f} fps · {timecode(video.duration)}")
        self.dimensions_label.setText(f"{video.frame_count:,} frames · Video only")
        self.original_button.setChecked(True)
        self.detected_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.analyze_button.setEnabled(True)
        self.enhancement_button.setEnabled(True)
        self.threshold_slider.setEnabled(True)
        self.workspace_meta.setText("Imported · ready to analyze")
        self.enhancement_status.setText(
            "Optional: BasicVSR++ enhancement"
        )
        self._clear_results()
        self._update_model_label()
        self.status_title.setText("Ready to analyze")
        self.status_detail.setText(f"Selected threshold: {self.threshold_slider.value()}%. Click Analyze video when ready.")
        self.summary_message.setText("Video imported successfully. Enhance it if needed, then choose Analyze video to start detection.")
        self._sync_header_actions()

    def show_detection_configuration(self):
        if self.video_info is None or self._thread:
            return
        if self._config_dialog and self._config_dialog.isVisible():
            self._config_dialog.raise_()
            self._config_dialog.activateWindow()
            return
        dialog = DetectionConfigDialog(
            self.video_info, self.threshold_slider.value(), self,
            current_model=self.bridge.model_path,
        )
        if self._enhanced_video_active:
            dialog.enhancement_notice.setText(
                "INPUT FOOTAGE\nBasicVSR++ enhanced video is active. Detection will analyze the enhanced footage."
            )
        self._config_dialog = dialog
        dialog.finished.connect(self._configuration_finished)
        dialog.open()

    def show_video_enhancement(self):
        if self.video_info is None or self._thread:
            return
        dialog = VideoEnhancementDialog(self.video_info, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.enhanced_video_path:
            try:
                new_info = read_video(dialog.enhanced_video_path)
                self.result = None
                self._clear_results()
                self.video_info = new_info
                self.source_path = new_info.path
                self._enhanced_video_active = True
                self.player.open(new_info.path)
                self.player.set_locked(True)
                self.original_button.setChecked(True)
                self.detected_button.setEnabled(False)
                self.review_button.setEnabled(False)
                self.save_button.setEnabled(False)
                self.report_button.setEnabled(False)
                self._sync_header_actions()
                self.source_name.setText(new_info.path.name)
                self.source_name.setToolTip(str(new_info.path))
                self.source_meta.setText(f"{new_info.width} × {new_info.height} pixels\n{new_info.fps:.2f} fps · {timecode(new_info.duration)}")
                self.dimensions_label.setText(f"{new_info.frame_count:,} frames · Video only")
                self.enhancement_status.setText("Enhanced video ready for detection")
                self.status_detail.setText(f"Enhanced video loaded: {new_info.path.name} · Selected threshold: {self.threshold_slider.value()}%")
                QMessageBox.information(
                    self,
                    "Enhanced Video Loaded",
                    f"Successfully applied BasicVSR++ super-resolution!\n\n"
                    f"Enhanced video: {new_info.path.name}\n"
                    f"Resolution: {new_info.width} × {new_info.height}\n\n"
                    "Click 'Analyze video' to run detection on the enhanced footage.",
                )
            except Exception as exc:
                QMessageBox.warning(self, "Enhanced Video Load Error", f"Could not load enhanced video:\n{exc}")
        dialog.deleteLater()

    @Slot(int)
    def _configuration_finished(self, code):
        dialog = self._config_dialog
        self._config_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        if dialog is None or code != QDialog.DialogCode.Accepted:
            self.status_title.setText("Ready to analyze")
            self.status_detail.setText(f"Video remains imported · Selected threshold: {self.threshold_slider.value()}%")
            return
        self.threshold_slider.setValue(dialog.slider.value())
        if hasattr(dialog, "selected_model") and dialog.selected_model:
            self.bridge.set_model_path(dialog.selected_model)
        if hasattr(dialog, "cctv_intel_checkbox"):
            self.bridge.enable_cctv_intelligence = dialog.cctv_intel_checkbox.isChecked()
        if hasattr(dialog, "temporal_checkbox"):
            self.bridge.enable_temporal_consistency = dialog.temporal_checkbox.isChecked()
        self._update_model_label()
        self._analysis_requested = True
        self.analyze()

    def _update_model_label(self):
        # Completed/reopened results retain the checkpoint that produced them,
        # even if another model is selected or the weights have since been moved.
        path = Path(self.result.model_path) if self.result else self.bridge.model_path
        if path and (self.result is not None or path.is_file()):
            self.model_label.setText(f"Faster R-CNN · Handgun / Knife\n{path.name}")
            self.model_label.setToolTip(str(path))
        elif path:
            self.model_label.setText(f"Selected checkpoint unavailable\n{path.name}")
            self.model_label.setToolTip(str(path))
        else:
            self.model_label.setText("No checkpoint selected")
            self.model_label.setToolTip("Select an available .pth checkpoint in the detection configuration.")

    def analyze(self):
        if self.video_info is None or self._thread or not self._analysis_requested:
            return
        if not self.bridge.find_model():
            self._update_model_label()
            self.status_title.setText("Waiting for trained model")
            self.status_detail.setText(f"Place {MODEL_FILENAME} in mockup_ui/models. Analysis will start automatically when it is found.")
            self.summary_message.setText("Video imported. Waiting for the trained handgun/knife model before scanning.")
            self.workspace_meta.setText("Waiting for trained model")
            if not self._model_retry.isActive():
                self._model_retry.start()
            return
        self._model_retry.stop()
        self.player.pause()
        self.result = None
        self._update_model_label()
        self._clear_results()
        self.original_button.setChecked(True)
        try:
            self.player.open(self.video_info.path)
            self.player.set_locked(True)
        except ValueError as exc:
            self.detected_button.setEnabled(False)
            self.save_button.setEnabled(False)
            self._show_error("Playback unavailable", str(exc))
            return
        for button in (self.open_button, self.save_button,
                       self.report_button, self.analyze_button, self.review_button, self.open_review_button,
                       self.original_button, self.detected_button, self.enhancement_button):
            button.setEnabled(False)
        self.threshold_slider.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status_title.setText("Loading detector")
        self.status_detail.setText("The detection service is loading the trained model to scan the entire video.")
        self.enhancement_status.setText(
            "BasicVSR++ enhanced video is being analyzed" if self._enhanced_video_active else
            "Analyzing the original video"
        )
        self.workspace_meta.setText("Scanning · please wait")
        self.summary_message.setText("Analyzing the video for weapons. Detection totals will appear after processing completes.")
        self.frame_message.setText("Awaiting the completed detection result.")
        self._processing_dialog = ProcessingDialog(self.video_info.frame_count, self)
        self._processing_dialog.show()
        self._thread = InferenceWorker(self.bridge, self.video_info, self.threshold_slider.value() / 100, self)
        self._thread.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self._thread.succeeded.connect(self._analysis_succeeded, Qt.ConnectionType.QueuedConnection)
        self._thread.failed.connect(self._analysis_failed, Qt.ConnectionType.QueuedConnection)
        self._thread.finished.connect(self._thread_finished, Qt.ConnectionType.QueuedConnection)
        self._thread.start()
        self._sync_header_actions()

    @Slot(str, int)
    def _on_progress(self, message, percent):
        self.status_detail.setText(message)
        if percent < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
            self.status_title.setText(f"Scanning video · {percent}%")
        if self._processing_dialog:
            self._processing_dialog.update_progress(message, percent)

    @Slot(object)
    def _analysis_succeeded(self, result):
        self.result = result
        self._update_model_label()
        if self._processing_dialog:
            self._processing_dialog.accept()
            self._processing_dialog.deleteLater()
            self._processing_dialog = None
        try:
            ReviewStore.for_result(result)
        except (OSError, ValueError, KeyError, TypeError):
            logging.exception("Could not persist the completed analysis for review")
            self._show_error("Review storage unavailable", "The video completed, but the review record could not be stored. Check disk space and permissions before saving analyst decisions.")
        self._detections_by_frame = result.by_frame
        self._render_results(result)
        self.detected_button.setEnabled(True)
        self.detected_button.setChecked(True)
        self.report_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.review_button.setEnabled(bool(result.detections))
        try:
            self.player.open(result.output_path)
            self.player.set_locked(False)
            if load_preferences().autoplay_results:
                self.player.play()
            self.workspace_meta.setText("Annotated video")
        except ValueError as exc:
            self._show_error("Playback unavailable", str(exc))
        self.status_title.setText("Detection complete" if result.detections else "No weapons detected")
        self.status_detail.setText(
            f"{result.analyzed_frames:,} frames analyzed\n"
            f"{len(result.detections):,} frame detections · {result.elapsed_seconds:.1f} s"
        )
        self.enhancement_status.setText(
            "BasicVSR++ enhanced video was analyzed" if self._enhanced_video_active else
            "Enhancement not applied to this run"
        )

    @Slot(str, str)
    def _analysis_failed(self, message, details):
        logging.error("Video inference failed\n%s", details)
        if self._processing_dialog:
            self._processing_dialog.accept()
            self._processing_dialog.deleteLater()
            self._processing_dialog = None
        self._restore_after_incomplete()
        self.status_title.setText("Analysis unavailable")
        self.status_detail.setText(message)
        self.summary_message.setText("No completed detection result was produced.")
        self._show_error("Analysis unavailable", message)

    def _restore_after_incomplete(self):
        self.player.pause()
        self.result = None
        self._clear_results()
        self.save_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self.review_button.setEnabled(False)
        self.detected_button.setEnabled(False)
        self.original_button.setChecked(True)
        self.show_original()
        self.player.set_locked(True)

    @Slot()
    def _thread_finished(self):
        # The built-in QThread.finished signal fires after run() returns. Join
        # before disposal, keeping both Qt and Python ownership on the UI thread.
        completed_thread = self._thread
        if completed_thread is None:
            return
        completed_thread.wait()
        completed_thread.deleteLater()
        self.progress.hide()
        self.open_button.setEnabled(True)
        self.open_review_button.setEnabled(True)
        self.analyze_button.setEnabled(self.video_info is not None)
        self.enhancement_button.setEnabled(self.video_info is not None)
        self.report_button.setEnabled(self.result is not None)
        self.original_button.setEnabled(True)
        self.threshold_slider.setEnabled(True)
        self._thread = None
        self._update_model_label()
        self._sync_header_actions()

    def _render_results(self, result):
        self.observation_model.set_records(result.detections)
        self.total_metric.setText(f"{len(result.detections):,}")
        self.handgun_metric.setText(f"{result.counts.get('handgun', 0):,}")
        self.knife_metric.setText(f"{result.counts.get('knife', 0):,}")
        if result.detections:
            self.summary_message.setText(
                f"Weapons detected in {result.positive_frames:,} of {result.analyzed_frames:,} frames. "
                "Use Observation review to record Accept, Reject, or Uncertain for each detection."
            )
        else:
            self.summary_message.setText(
                f"No handgun or knife met the {result.threshold:.0%} confidence threshold in this video."
            )
        self.run_meta.setText(
            f"Threshold {result.threshold:.0%} · {result.device.upper()} · Every frame\n"
            "Counts are frame observations; a weapon may appear in several frames."
        )

    def _frame_changed(self, number):
        self._clear_result_cards()
        if self.result is None:
            self.frame_message.setText("The annotated video and detection summary appear after automatic analysis.")
            return
        rows = self._detections_by_frame.get(number, [])
        self.frame_message.setText(
            f"{timecode(number / self.video_info.fps)} · Frame {number}\n"
            + (f"{len(rows)} detection(s) in this frame" if rows else "No detections in this frame")
        )
        for index, row in enumerate(rows, 1):
            self.results_layout.insertWidget(index - 1, DetectionCard(row, index))

    def _jump_to_detection(self, index):
        if self.result is None or not index.isValid() or self._thread:
            return
        row = self.observation_model.records[index.row()]
        self.detected_button.setChecked(True)
        self._switch_view(self.result.output_path, row["frame_number"], "Annotated video")

    def _switch_view(self, path, frame_number, label):
        try:
            if self.player.path != Path(path).resolve():
                self.player.open(path, frame_number)
            else:
                self.player.seek_frame(frame_number)
            self.workspace_meta.setText(label)
        except ValueError as exc:
            self._show_error("Playback unavailable", str(exc))

    def show_original(self):
        if self.video_info is not None:
            self._switch_view(self.video_info.path, self.player.frame_number, "Original CCTV footage")

    def show_detected(self):
        if self.result is not None:
            self._switch_view(self.result.output_path, self.player.frame_number, "Annotated video")

    def _clear_result_cards(self):
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _clear_results(self):
        self._detections_by_frame = {}
        self._clear_result_cards()
        self.observation_model.set_records([])
        for metric in (self.total_metric, self.handgun_metric, self.knife_metric):
            metric.setText("—")
        self.frame_message.setText("No video analyzed yet.")
        self.summary_message.setText("Import a video, review the confidence threshold, then start detection.")
        self.run_meta.setText("Counts are observations across frames; a weapon may appear in several frames.")

    def save_current_result(self):
        if self.mode_tabs.currentIndex() == 1:
            multi_camera = getattr(self, "_multi_camera_window", None)
            if multi_camera and multi_camera.results and not multi_camera.worker:
                multi_camera.export()
            return
        if not self.result:
            return
        destination = QFileDialog.getExistingDirectory(
            self, "Choose where to save the video and forensic report", export_start_directory()
        )
        if not destination:
            return
        try:
            directory = save_result(self.result, Path(destination))
        except Exception:
            logging.exception("Could not save video result")
            self._show_error("Save failed", "The video/report could not be saved. Check available disk space and permissions for the selected destination.")
            return
        QMessageBox.information(self, "Video and report saved",
                                "Saved annotated video, original detection records, and a forensic report.\n"
                                "The PDF includes saved analyst reviews. Reopen observation_reviews.json to continue reviewing. Structured records are in "
                                f"forensic_report.json and forensic_records.csv.\n\n{directory}")

    def show_forensic_report(self):
        if self.mode_tabs.currentIndex() == 1:
            multi_camera = getattr(self, "_multi_camera_window", None)
            if multi_camera and multi_camera.results and not multi_camera.worker:
                multi_camera.show_report()
            return
        if self.result is None or self._thread:
            return
        try:
            ForensicReportDialog(self.result, self).exec()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._show_error("Report unavailable", str(exc))

    def show_observation_review(self):
        if self.result is None or self._thread or not self.result.detections:
            return
        self.player.pause()
        try:
            ObservationReviewDialog(self.result, self).exec()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._show_error("Observation review unavailable", str(exc))

    def open_saved_review(self):
        if self._thread or (getattr(self, "_multi_camera_window", None) and self._multi_camera_window.worker):
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Open a saved observation review", str(REVIEW_DIR), "Observation review (*.json)")
        if not filename:
            return
        try:
            result = ReviewStore(filename).restore_result()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._show_error("Review file unavailable", f"This file could not be opened as an observation review.\n{exc}")
            return
        self.close_videos()
        self.video_info, self.source_path = result.video, result.video.path
        self._enhanced_video_active = False
        self.source_name.setText(result.video.path.name)
        self.source_name.setToolTip(str(result.video.path))
        self.source_meta.setText(f"{result.video.width} × {result.video.height} pixels\n{result.video.fps:.2f} fps · {timecode(result.video.duration)}")
        self.threshold_slider.setValue(round(result.threshold * 100))
        self.analyze_button.setEnabled(result.video.path.is_file())
        self.enhancement_button.setEnabled(result.video.path.is_file())
        self._analysis_succeeded(result)
        self.enhancement_status.setText(
            "Saved run used the original video"
        )
        self.player.pause()
        self._sync_header_actions()
        self.show_observation_review()

    def _set_ready(self, detail):
        if not self.bridge.model_path or not self.bridge.model_path.is_file():
            self.status_title.setText("Automatic model lookup")
            self.status_detail.setText(f"Place {MODEL_FILENAME} in mockup_ui/models, then import a video.")
        else:
            self.status_title.setText("Ready for detection")
            self.status_detail.setText(detail)

    def _show_error(self, title: str, message: str):
        QMessageBox.warning(self, title, message)

    def closeEvent(self, event):
        multi_camera = getattr(self, "_multi_camera_window", None)
        if multi_camera and multi_camera.worker:
            self.mode_tabs.setCurrentIndex(1)
            event.ignore()
            return
        if self._thread:
            QMessageBox.information(self, "Video analysis in progress", "Let the current video analysis finish before closing the window.")
            event.ignore()
            return
        self.player.release()
        if multi_camera:
            multi_camera.close()
        self._model_retry.stop()
        event.accept()


def parse_args():
    parser = argparse.ArgumentParser(description="Forensikada forensic video weapon detection")
    parser.add_argument("--video", help="Optional CCTV video to analyze automatically on startup")
    return parser.parse_args()


def _set_windows_app_id():
    """Give Windows a stable identity so the FK icon appears on the taskbar."""
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID("Forensikada.VideoAnalysis")
    except (AttributeError, OSError):
        logging.getLogger(__name__).debug("Could not set the Windows application ID", exc_info=True)


def main():
    args = parse_args()
    _set_windows_app_id()
    application = QApplication(sys.argv[:1])
    application.setApplicationName("Forensikada Video Analysis")
    application.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    application.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f5f6f8"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f318e"))
    application.setPalette(palette)
    window = MainWindow(initial_video=args.video)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
