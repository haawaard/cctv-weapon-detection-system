"""Local OpenCV video playback with frame-accurate pause/seek for detection review."""
from pathlib import Path
import math

import cv2
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget

from mockup_ui.model_bridge import timecode
from mockup_ui.motion import SpringButton as QPushButton


class VideoCanvas(QLabel):
    file_dropped = Signal(str)
    browse_requested = Signal()

    def __init__(self):
        super().__init__()
        self._source_pixmap = QPixmap()
        self._zoom = 1.0
        self._center_x = .5
        self._center_y = .5
        self._pan_start = QPoint()
        self.setObjectName("imageCanvas")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(440, 245)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setAcceptDrops(True)
        self.setText("Import a CCTV video to begin\n\nMP4, AVI, MOV, MKV or WebM\n\nEnhance if needed, then analyze")
        self.setProperty("hasImage", False)

    def show_frame(self, frame):
        height, width, _ = frame.shape
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_BGR888)
        self._source_pixmap = QPixmap.fromImage(image.copy())
        self.setText("")
        self.setProperty("hasImage", True)
        self._fit()

    def reset(self):
        self._source_pixmap = QPixmap()
        self.set_zoom(1.0)
        self.clear()
        self.setProperty("hasImage", False)
        self.setText("Import a CCTV video to begin\n\nMP4, AVI, MOV, MKV or WebM\n\nEnhance if needed, then analyze")
        self.style().unpolish(self)
        self.style().polish(self)

    def _fit(self):
        if not self._source_pixmap.isNull():
            if self._zoom <= 1.001:
                shown = self._source_pixmap
            else:
                width = max(1, round(self._source_pixmap.width() / self._zoom))
                height = max(1, round(self._source_pixmap.height() / self._zoom))
                left = round(self._center_x * self._source_pixmap.width() - width / 2)
                top = round(self._center_y * self._source_pixmap.height() - height / 2)
                left = min(max(0, left), self._source_pixmap.width() - width)
                top = min(max(0, top), self._source_pixmap.height() - height)
                shown = self._source_pixmap.copy(left, top, width, height)
            self.setPixmap(shown.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))

    @property
    def zoom(self):
        return self._zoom

    def set_zoom(self, value):
        self._zoom = min(4.0, max(1.0, float(value)))
        if self._zoom == 1.0:
            self._center_x = self._center_y = .5
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._zoom > 1 else Qt.CursorShape.ArrowCursor)
        self._fit()

    def wheelEvent(self, event):
        if not self._source_pixmap.isNull():
            self.set_zoom(self._zoom + (.25 if event.angleDelta().y() > 0 else -.25))
            event.accept()
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._source_pixmap.isNull():
            self.set_zoom(1.0)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        self._fit()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._zoom > 1:
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._source_pixmap.isNull():
            self.browse_requested.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._zoom > 1 and not self._pan_start.isNull():
            point = event.position().toPoint()
            delta = point - self._pan_start
            self._pan_start = point
            self._center_x = min(1.0, max(0.0, self._center_x - delta.x() / max(1, self.width()) / self._zoom))
            self._center_y = min(1.0, max(0.0, self._center_y - delta.y() / max(1, self.height()) / self._zoom))
            self._fit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._zoom > 1:
            self._pan_start = QPoint()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.file_dropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class VideoPlayer(QWidget):
    frame_changed = Signal(int)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.capture = None
        self.path = None
        self.fps = 0.0
        self.frame_count = 0
        self.frame_number = 0
        self.locked = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.canvas = VideoCanvas()
        layout.addWidget(self.canvas, 1)
        controls = QFrame()
        controls.setObjectName("playerControls")
        row = QHBoxLayout(controls)
        row.setContentsMargins(9, 6, 9, 6)
        row.setSpacing(9)
        self.play_button = QPushButton("Play")
        self.play_button.setMinimumWidth(60)
        self.play_button.clicked.connect(self.toggle_playback)
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(lambda: self.seek_frame(0))
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.valueChanged.connect(self.seek_frame)
        self.seek_slider.sliderPressed.connect(self.pause)
        self.time_label = QLabel("00:00.000 / 00:00.000")
        row.addWidget(self.play_button)
        row.addWidget(self.start_button)
        row.addWidget(self.seek_slider, 1)
        row.addWidget(self.time_label)
        layout.addWidget(controls)
        self.set_locked(False)

    def open(self, path: str | Path, frame_number=0):
        path = Path(path).resolve()
        capture = cv2.VideoCapture(str(path))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or count <= 0:
            capture.release()
            raise ValueError("This video could not be opened for playback.")
        number = min(max(0, int(frame_number)), count - 1)
        capture.set(cv2.CAP_PROP_POS_FRAMES, number)
        ok, frame = capture.read()
        if not ok:
            capture.release()
            raise ValueError("The requested video frame could not be decoded.")
        self.release()
        self.capture = capture
        self.path, self.fps, self.frame_count = path, fps, count
        self.timer.setInterval(max(1, round(1000 / fps)))
        self.seek_slider.blockSignals(True)
        self.seek_slider.setRange(0, count - 1)
        self.seek_slider.blockSignals(False)
        self.set_locked(self.locked)
        self._present(frame, number)

    def set_locked(self, locked: bool):
        self.locked = locked
        if locked:
            self.pause()
        enabled = self.capture is not None and not locked
        for widget in (self.play_button, self.start_button, self.seek_slider):
            widget.setEnabled(enabled)

    def _present(self, frame, number):
        self.frame_number = number
        self.canvas.show_frame(frame)
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(number)
        self.seek_slider.blockSignals(False)
        self.time_label.setText(f"{timecode(number / self.fps)} / {timecode(self.frame_count / self.fps)}")
        self.frame_changed.emit(number)

    def seek_frame(self, number):
        if self.capture is None or self.locked:
            return
        self.pause()
        number = min(max(0, int(number)), self.frame_count - 1)
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, number)
        ok, frame = self.capture.read()
        if ok:
            self._present(frame, number)
        else:
            self.failed.emit("This frame could not be decoded. Try another position or video.")

    def toggle_playback(self):
        if self.capture is None or self.locked:
            return
        if self.timer.isActive():
            self.pause()
        else:
            self.play()

    def play(self, from_start=False):
        """Start explicitly, without toggling an already-playing video to pause."""
        if self.capture is None or self.locked:
            return
        if from_start or self.frame_number >= self.frame_count - 1:
            self.seek_frame(0)
        self.timer.start()
        self.play_button.setText("Pause")

    def _advance(self):
        if self.capture is None or self.frame_number >= self.frame_count - 1:
            self.pause()
            return
        ok, frame = self.capture.read()
        if not ok:
            self.pause()
            self.failed.emit("Playback stopped because a video frame could not be decoded.")
            return
        self._present(frame, self.frame_number + 1)

    def pause(self):
        self.timer.stop()
        self.play_button.setText("Play")

    def release(self):
        self.pause()
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def reset(self):
        """Release the file and return playback to its empty, disabled state."""
        self.release()
        self.path = None
        self.fps = 0.0
        self.frame_count = self.frame_number = 0
        self.seek_slider.blockSignals(True)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setValue(0)
        self.seek_slider.blockSignals(False)
        self.time_label.setText("00:00.000 / 00:00.000")
        self.canvas.reset()
        self.set_locked(True)
